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

from agents.meteora_regime_lp.capital_math import build_capital_plan
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
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
    refresh: bool = True


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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

    wallet_total = 0.0
    liquid_quote = 0.0
    prices: dict[str, float] = {}
    quote_symbols = {symbol.upper() for symbol in config.quote_tokens}
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
                if symbol and price > 0:
                    prices[symbol] = price

    native_price = prices.get("SOL") or prices.get("WSOL")
    if not native_price:
        return (
            "capital_guard: UNKNOWN — fresh wallet read contained no priced native SOL. "
            "Treat this as a partial read and pause new entries."
        )

    pool_quotes: dict[str, str] = {}
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
    for requested_pool, positions in owned_results:
        for position in positions:
            address = str(
                position.get("position_address")
                or position.get("position")
                or position.get("address")
                or ""
            )
            actual_pool = str(position.get("pool_address") or requested_pool)
            if not address or actual_pool not in pool_quotes:
                return (
                    "capital_guard: UNKNOWN — an on-chain position lacks a unique "
                    "address or recognized pool. Pause new entries rather than double-counting."
                )
            normalized = dict(position)
            normalized["pool_address"] = actual_pool
            positions_by_address.setdefault(address, normalized)

    open_lp_usd = 0.0
    for position in positions_by_address.values():
        pool = str(position.get("pool_address") or "")
        quote_mint = pool_quotes[pool]
        quote_symbol = MINT_TO_SYMBOL.get(quote_mint)
        quote_price = prices.get(quote_symbol or "")
        if not quote_price:
            return (
                f"capital_guard: UNKNOWN — no USD price for quote mint {quote_mint[:8]}… "
                f"on pool {pool[:8]}…. Pause new entries."
            )
        open_lp_usd += _position_value_quote(position) * quote_price
    position_count = len(positions_by_address)

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
    summary = (
        f"capital_guard: effective equity ${plan.effective_equity_usd:,.2f} = wallet "
        f"${wallet_total:,.2f} + {position_count} on-chain LP(s) ${open_lp_usd:,.2f}; "
        f"risk capital ${plan.risk_capital_usd:,.2f} (configured ceiling "
        f"${config.configured_capital_usd:,.2f}); daily loss "
        f"${plan.daily_loss_limit_usd:,.2f} at {config.daily_loss_limit_pct:g}%; "
        f"liquid quote before reserves ${liquid_quote:,.2f}; max next deposit "
        f"${plan.max_new_deposit_usd:,.2f} after ${plan.gas_reserve_usd:,.2f} gas "
        f"reserve + ${plan.next_position_rent_usd:,.2f} next-position rent. "
        f"Sleeve ceilings: {sleeves}."
    )

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary)
    except Exception:
        return summary
