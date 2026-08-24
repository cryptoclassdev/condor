"""Authoritative USD capital and loss budgets for Meteora LP decisions.

This routine values the liquid wallet from a fresh portfolio read and every
open LP from ``get_positions_owned``.  The configured capital is treated as a
ceiling.  If any authority read or quote conversion is unavailable, the result
is UNKNOWN and entries must pause for the tick.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from agents.meteora_regime_lp.capital_math import (
    build_capital_plan,
    build_spendable_quote_caps,
)
from config_manager import get_client
from agents.meteora_regime_lp.wallet_truth import authoritative_wallet_state

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
CORE_SOL_USDC_POOL = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"
MINT_TO_SYMBOL = {SOL_MINT: "SOL", USDC_MINT: "USDC"}


class Config(BaseModel):
    """Value the full book and compute conservative entry/loss budgets."""

    connector: str = "meteora"
    network: str = "solana-mainnet-beta"
    configured_capital_usd: float = 800.0
    daily_loss_limit_pct: float = 6.0
    min_native_reserve: float = 0.06
    next_position_rent_native: float = 0.0574
    quote_tokens: list[str] = Field(default=["SOL", "WSOL", "USDC", "USDT"])
    sleeve_percentages: dict[str, float] = Field(
        default={"core": 60.0, "satellite": 20.0, "runner": 20.0}
    )
    known_pool_quotes: dict[str, str] = Field(
        default={CORE_SOL_USDC_POOL: USDC_MINT},
        description="Authority-read seed pools and their quote mints",
    )
    refresh: bool = True
    rpc_url: str = ""


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _wallet_metrics(
    state: dict, quote_symbols: set[str]
) -> tuple[float, float, dict[str, float], dict[str, float]]:
    wallet_total = 0.0
    liquid_quote = 0.0
    quote_values: dict[str, float] = {}
    prices: dict[str, float] = {}
    for account in state.values():
        if not isinstance(account, dict):
            continue
        for balances in account.values():
            for balance in balances or []:
                if not isinstance(balance, dict):
                    continue
                symbol = str(balance.get("token") or "").upper()
                value = _num(balance.get("value"))
                price = _num(balance.get("price"))
                wallet_total += value
                if symbol in quote_symbols:
                    liquid_quote += value
                    quote_values[symbol] = quote_values.get(symbol, 0.0) + value
                if symbol and price > 0:
                    prices[symbol] = price
    return wallet_total, liquid_quote, quote_values, prices


def _position_value_quote(position: dict) -> float:
    base = _num(position.get("base_token_amount", position.get("base_amount")))
    quote = _num(position.get("quote_token_amount", position.get("quote_amount")))
    price = _num(position.get("current_price", position.get("price")))
    base_fee = _num(position.get("base_fee_amount", position.get("base_fees")))
    quote_fee = _num(position.get("quote_fee_amount", position.get("quote_fees")))
    return (base + base_fee) * price + quote + quote_fee


def _executor_pool_and_quote(executor: dict) -> tuple[str, str]:
    sources = [
        executor.get("config") if isinstance(executor.get("config"), dict) else {},
        executor,
    ]
    pool = ""
    pair = ""
    for source in sources:
        pool = pool or str(source.get("pool_address") or source.get("pool") or "")
        pair = pair or str(source.get("trading_pair") or source.get("pair") or "")
    quote_mint = pair.rsplit("-", 1)[-1] if "-" in pair else ""
    return pool, quote_mint


def _executor_position_address(executor: dict) -> str:
    for source in (
        executor.get("custom_info"),
        executor.get("config"),
        executor,
    ):
        if not isinstance(source, dict):
            continue
        for key in ("position_address", "position", "position_nft"):
            value = source.get(key)
            if isinstance(value, str) and len(value) > 30:
                return value
    return ""


async def _running_executors(client) -> list[dict]:
    response = await client.executors.search_executors(status="RUNNING", limit=50)
    rows = (
        response.get("data") or response.get("executors") or []
        if isinstance(response, dict)
        else response or []
    )
    return [row for row in rows if isinstance(row, dict)]


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "capital_guard: UNKNOWN — no server available. Pause new entries."

    try:
        state, executors = await asyncio.gather(
            client.portfolio.get_state(refresh=config.refresh),
            _running_executors(client),
        )
    except Exception as exc:
        return (
            f"capital_guard: UNKNOWN — wallet/executor read failed ({exc}). "
            "Pause new entries; do not reuse a stale capital figure."
        )

    if not isinstance(state, dict):
        return "capital_guard: UNKNOWN — unexpected wallet payload. Pause new entries."

    chain_verified = False
    try:
        chain_truth = await authoritative_wallet_state(client, rpc_url=config.rpc_url)
    except Exception as exc:
        return (
            f"capital_guard: UNKNOWN — direct Solana wallet verification failed ({exc}). "
            "Pause new entries; cached portfolio data can omit Token-2022 balances."
        )
    if chain_truth is not None:
        state = chain_truth.state
        chain_verified = True

    quote_symbols = {symbol.upper() for symbol in config.quote_tokens}
    wallet_total, liquid_quote, quote_values, prices = _wallet_metrics(
        state, quote_symbols
    )

    # A successful portfolio call can still be partial while balances are being
    # refreshed. Observed live: one read returned only SOL and silently dropped
    # $51.41 USDC; the next read seconds later returned both. A genuinely
    # single-quote wallet is accepted when a second read agrees. If token sets
    # differ, sizing pauses for the tick instead of trusting either snapshot.
    if len(quote_values) == 1 and not chain_verified:
        try:
            retry_state = await client.portfolio.get_state(refresh=config.refresh)
        except Exception as exc:
            return (
                f"capital_guard: UNKNOWN — single-quote wallet read could not be "
                f"verified ({exc}). Pause new entries."
            )
        if not isinstance(retry_state, dict):
            return (
                "capital_guard: UNKNOWN — single-quote wallet verification returned an "
                "unexpected payload. Pause new entries."
            )
        retry_metrics = _wallet_metrics(retry_state, quote_symbols)
        retry_quote_values = retry_metrics[2]
        if set(retry_quote_values) != set(quote_values):
            return (
                "capital_guard: UNKNOWN — consecutive portfolio reads returned "
                "inconsistent quote-token sets. Treat this as a partial refresh and "
                "pause new entries for the tick."
            )
        wallet_total, liquid_quote, quote_values, prices = retry_metrics

    native_price = prices.get("SOL") or prices.get("WSOL")
    if not native_price:
        return (
            "capital_guard: UNKNOWN — fresh wallet read contained no priced native SOL. "
            "Treat this as a partial read and pause new entries."
        )

    # Seed the wallet-wide authority read independently of executors. An executor
    # can disappear while its position remains on-chain; using only RUNNING
    # executor pools undercounts that orphaned capital as zero. Gateway v2.16's
    # owned endpoint is wallet-wide despite requiring a pool argument, so one
    # stable core pool is enough to discover every held position.
    pool_quotes: dict[str, str] = dict(config.known_pool_quotes)
    for executor in executors:
        pool, quote_mint = _executor_pool_and_quote(executor)
        if not pool or not quote_mint:
            return (
                "capital_guard: UNKNOWN — a RUNNING executor lacks pool/quote metadata. "
                "Pause new entries rather than undercounting LP equity."
            )
        previous = pool_quotes.setdefault(pool, quote_mint)
        if previous != quote_mint:
            return (
                f"capital_guard: UNKNOWN — pool {pool[:8]}… has conflicting quote assets. "
                "Pause new entries."
            )

    async def owned(pool: str):
        result = await client.gateway_clmm.get_positions_owned(
            connector=config.connector,
            network=config.network,
            pool_address=pool,
        )
        rows = result.get("data") or [] if isinstance(result, dict) else result or []
        return pool, [row for row in rows if isinstance(row, dict)]

    try:
        owned_results = await asyncio.gather(*(owned(pool) for pool in pool_quotes))
    except Exception as exc:
        return (
            f"capital_guard: UNKNOWN — on-chain LP authority read failed ({exc}). "
            "Pause new entries; do not size from the executor cache."
        )

    # Gateway v2.16 currently ignores ``pool_address`` on this endpoint and can
    # return the wallet-wide position set for every requested pool.  Deduplicate
    # by position address and use the pool address carried by each row; otherwise
    # two pools become four positions and the SOL quote price can be applied to a
    # USDC row (an 80x equity overstatement observed live on Aug 20).
    positions_by_address: dict[str, dict] = {}
    unresolved_pools: set[str] = set()
    for requested_pool, positions in owned_results:
        for position in positions:
            address = str(
                position.get("position_address")
                or position.get("position")
                or position.get("address")
                or ""
            )
            actual_pool = str(position.get("pool_address") or requested_pool)
            quote_mint = str(
                position.get("quote_token_address")
                or position.get("quote_token_mint")
                or position.get("quote_mint")
                or ""
            )
            if not address or not actual_pool:
                return (
                    "capital_guard: UNKNOWN — an on-chain position lacks a unique address "
                    "or pool address. Pause new entries rather than undercounting."
                )
            if quote_mint:
                previous = pool_quotes.setdefault(actual_pool, quote_mint)
                if previous != quote_mint:
                    return (
                        f"capital_guard: UNKNOWN — pool {actual_pool[:8]}… has conflicting "
                        "quote-mint metadata. Pause new entries."
                    )
            elif actual_pool not in pool_quotes:
                unresolved_pools.add(actual_pool)
            normalized = dict(position)
            normalized["pool_address"] = actual_pool
            positions_by_address.setdefault(address, normalized)

    # Gateway v2.16's positions-owned response can omit both token addresses.
    # That is common after an executor disappears: there is no RUNNING config
    # left to provide the pair, so a real orphan otherwise freezes capital
    # valuation forever. Resolve only those unknown pools through the
    # authoritative pool-info endpoint; never infer orientation from symbols,
    # amounts, or price magnitude.
    async def resolve_quote(pool: str) -> tuple[str, str]:
        info = await client.gateway_clmm.get_pool_info(
            connector=config.connector,
            network=config.network,
            pool_address=pool,
        )
        if not isinstance(info, dict):
            return pool, ""
        quote_mint = str(
            info.get("quote_token_address")
            or info.get("quote_token_mint")
            or info.get("quote_mint")
            or ""
        )
        return pool, quote_mint

    if unresolved_pools:
        try:
            resolved_quotes = await asyncio.gather(
                *(resolve_quote(pool) for pool in sorted(unresolved_pools))
            )
        except Exception as exc:
            return (
                "capital_guard: UNKNOWN — pool-info could not resolve quote metadata for "
                f"an on-chain position ({exc}). Pause new entries."
            )
        missing_quotes = []
        for pool, quote_mint in resolved_quotes:
            if not quote_mint:
                missing_quotes.append(pool)
            else:
                pool_quotes[pool] = quote_mint
        if missing_quotes:
            return (
                "capital_guard: UNKNOWN — pool-info omitted quote-mint metadata for "
                f"{len(missing_quotes)} on-chain pool(s). Pause new entries."
            )

    open_lp_usd = 0.0
    for position in positions_by_address.values():
        pool = str(position.get("pool_address") or "")
        quote_mint = pool_quotes[pool]
        quote_symbol = MINT_TO_SYMBOL.get(quote_mint)
        quote_price = 1.0 if quote_mint == USDC_MINT else prices.get(quote_symbol or "")
        if not quote_price:
            return (
                f"capital_guard: UNKNOWN — no USD price for quote mint {quote_mint[:8]}… "
                f"on pool {pool[:8]}…. Pause new entries."
            )
        open_lp_usd += _position_value_quote(position) * quote_price
    position_count = len(positions_by_address)
    missing_executor_positions = [
        address
        for executor in executors
        if (address := _executor_position_address(executor))
        and address not in positions_by_address
    ]
    if missing_executor_positions:
        return (
            "capital_guard: UNKNOWN — a RUNNING executor position is missing from the "
            "owned-position authority read. Pause new entries; verify the position through "
            "the fallback chain RPC rather than sizing from an incomplete book."
        )

    plan = build_capital_plan(
        configured_capital_usd=config.configured_capital_usd,
        wallet_total_usd=wallet_total,
        liquid_quote_usd=liquid_quote,
        open_lp_usd=open_lp_usd,
        native_price_usd=native_price,
        min_native_reserve=config.min_native_reserve,
        next_position_rent_native=config.next_position_rent_native,
        daily_loss_limit_pct=config.daily_loss_limit_pct,
        sleeve_percentages=config.sleeve_percentages,
    )

    sleeves = ", ".join(
        f"{name} ${amount:,.2f}" for name, amount in plan.sleeve_budgets_usd.items()
    )
    quote_caps = build_spendable_quote_caps(
        quote_values_usd=quote_values,
        native_symbols={"SOL"},
        gas_reserve_usd=plan.gas_reserve_usd,
        next_position_rent_usd=plan.next_position_rent_usd,
    )
    per_asset = ", ".join(
        f"{symbol} ${amount:,.2f}" for symbol, amount in sorted(quote_caps.items())
    )
    summary = (
        f"capital_guard: effective equity ${plan.effective_equity_usd:,.2f} = wallet "
        f"${wallet_total:,.2f} + {position_count} on-chain LP(s) ${open_lp_usd:,.2f}; "
        f"risk capital ${plan.risk_capital_usd:,.2f} (configured ceiling "
        f"${config.configured_capital_usd:,.2f}); daily loss "
        f"${plan.daily_loss_limit_usd:,.2f} at {config.daily_loss_limit_pct:g}%; "
        f"liquid quote before reserves ${liquid_quote:,.2f}; max next deposit "
        f"${plan.max_new_deposit_usd:,.2f} after ${plan.gas_reserve_usd:,.2f} gas "
        f"reserve + ${plan.next_position_rent_usd:,.2f} next-position rent. "
        f"Exact-token per-asset max deposits: {per_asset}; aggregate liquid value "
        "cannot substitute one token for another. "
        f"Sleeve ceilings: {sleeves}."
    )

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary)
    except Exception:
        return summary
