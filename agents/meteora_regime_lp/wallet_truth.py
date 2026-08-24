"""Direct Solana wallet inventory for capital-critical Meteora decisions.

Hummingbot's cached portfolio endpoint can omit SPL Token-2022 accounts and has
also been observed alternating between complete and partial legacy-token reads.
This adapter reads native SOL plus both token programs from Solana, then prices
the resulting mint set in one GeckoTerminal request.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
GECKO_MULTI = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/multi"
ENV_RPC_VARS = ("SOLANA_RPC_URL", "RPC_URL")


@dataclass(frozen=True)
class TokenHolding:
    mint: str
    units: float
    program: str


@dataclass(frozen=True)
class WalletTruth:
    wallet_address: str
    state: dict[str, dict[str, list[dict[str, Any]]]]


_CACHE: dict[str, tuple[float, WalletTruth]] = {}
_CACHE_TTL_SEC = 45.0


def _rpc_url(explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    for name in ENV_RPC_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return PUBLIC_RPC


def _wallet_address(payload: Any) -> str:
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("chain", "")).lower() != "solana":
            continue
        default = str(row.get("default_address") or row.get("defaultAddress") or "")
        if default:
            return default
        addresses = row.get("walletAddresses") or row.get("wallet_addresses") or []
        if addresses:
            return str(addresses[0])
    raise RuntimeError("no default Solana Gateway wallet is configured")


async def _rpc(
    session: aiohttp.ClientSession, url: str, method: str, params: list[Any]
) -> Any:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with session.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status == 429:
                    raise RuntimeError(f"{method} rate limited")
                if response.status != 200:
                    raise RuntimeError(f"{method} returned HTTP {response.status}")
                payload = await response.json()
            if payload.get("error"):
                raise RuntimeError(f"{method}: {payload['error']}")
            return payload.get("result")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(1)
    raise last_error or RuntimeError(f"{method} failed")


def _parse_holdings(result: Any, program: str) -> list[TokenHolding]:
    holdings: list[TokenHolding] = []
    for row in (result or {}).get("value", []):
        parsed = (((row or {}).get("account") or {}).get("data") or {}).get(
            "parsed", {}
        )
        info = parsed.get("info", {})
        amount = info.get("tokenAmount") or {}
        try:
            units = float(amount.get("uiAmountString") or 0)
        except (TypeError, ValueError):
            continue
        mint = str(info.get("mint") or "")
        if mint and units:
            holdings.append(TokenHolding(mint=mint, units=units, program=program))
    return holdings


def _parse_prices(payload: Any) -> dict[str, tuple[str, float]]:
    prices: dict[str, tuple[str, float]] = {}
    for row in (payload or {}).get("data", []):
        attributes = row.get("attributes") or {}
        mint = str(row.get("id") or "").removeprefix("solana_")
        symbol = str(attributes.get("symbol") or mint[:8]).upper()
        try:
            price = float(attributes.get("price_usd") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if mint and price > 0:
            prices[mint] = (symbol, price)
    return prices


def build_wallet_state(
    *,
    native_sol: float,
    holdings: list[TokenHolding],
    prices: dict[str, tuple[str, float]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    missing = sorted(
        holding.mint for holding in holdings if holding.units and holding.mint not in prices
    )
    if SOL_MINT not in prices:
        missing.append(SOL_MINT)
    if missing:
        short = ", ".join(f"{mint[:8]}…" for mint in sorted(set(missing)))
        raise RuntimeError(f"no USD price for on-chain holding(s): {short}")

    sol_symbol, sol_price = prices[SOL_MINT]
    rows: list[dict[str, Any]] = [
        {
            "token": "SOL" if sol_symbol in {"SOL", "WSOL"} else sol_symbol,
            "mint": SOL_MINT,
            "units": native_sol,
            "price": sol_price,
            "value": native_sol * sol_price,
            "source": "solana-rpc",
        }
    ]
    for holding in holdings:
        symbol, price = prices[holding.mint]
        rows.append(
            {
                "token": symbol,
                "mint": holding.mint,
                "units": holding.units,
                "price": price,
                "value": holding.units * price,
                "source": f"solana-rpc/{holding.program}",
            }
        )
    return {"gateway_wallet": {"solana-mainnet-beta": rows}}


async def authoritative_wallet_state(client: Any, *, rpc_url: str = "") -> WalletTruth | None:
    """Return direct chain inventory, or ``None`` for an older client without accounts."""

    accounts = getattr(client, "accounts", None)
    if accounts is None or not hasattr(accounts, "list_gateway_wallets"):
        return None
    address = _wallet_address(await accounts.list_gateway_wallets())
    url = _rpc_url(rpc_url)
    cache_key = f"{address}|{url}"
    cached = _CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] <= _CACHE_TTL_SEC:
        return cached[1]

    async with aiohttp.ClientSession() as session:
        balance, legacy, token_2022 = await asyncio.gather(
            _rpc(session, url, "getBalance", [address, {"commitment": "confirmed"}]),
            _rpc(
                session,
                url,
                "getTokenAccountsByOwner",
                [
                    address,
                    {"programId": TOKEN_PROGRAM},
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            ),
            _rpc(
                session,
                url,
                "getTokenAccountsByOwner",
                [
                    address,
                    {"programId": TOKEN_2022_PROGRAM},
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            ),
        )
        holdings = _parse_holdings(legacy, "spl-token") + _parse_holdings(
            token_2022, "spl-token-2022"
        )
        mints = [SOL_MINT, *(holding.mint for holding in holdings)]
        async with session.get(
            f"{GECKO_MULTI}/{','.join(dict.fromkeys(mints))}",
            headers={"accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"wallet price read returned HTTP {response.status}")
            prices = _parse_prices(await response.json())

    native_sol = float((balance or {}).get("value") or 0) / 1_000_000_000
    truth = WalletTruth(
        wallet_address=address,
        state=build_wallet_state(
            native_sol=native_sol,
            holdings=holdings,
            prices=prices,
        ),
    )
    _CACHE[cache_key] = (now, truth)
    return truth
