"""Plan one safe residual-token cleanup into native SOL.

This guard is deliberately read-only.  It turns the authoritative on-chain wallet
inventory into an exact, mint-addressed order-executor instruction.  The strategy
may execute at most one returned plan per tick, then must wait for a fresh wallet
read before reusing the proceeds or planning another cleanup.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from agents.meteora_regime_lp.wallet_truth import authoritative_wallet_state
from config_manager import get_client

CATEGORY = "Analysis"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD1XhBGQEc9vM2nN3n5sZ8"


class Config(BaseModel):
    """Identify one stranded balance that can be sold back to SOL."""

    enabled: bool = True
    target_mint: str = SOL_MINT
    min_value_usd: float = Field(default=0.01, ge=0)
    # USDC/USDT remain directly deployable quote and are not stranded inventory.
    # A separate, sleeve-aware funding decision may convert them when needed.
    protected_mints: list[str] = Field(default=[SOL_MINT, USDC_MINT, USDT_MINT])
    protected_quote_dust_max_usd: float = Field(default=1.0, ge=0)
    max_actions: int = Field(default=1, ge=0, le=1)
    rpc_url: str = ""


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rows(state: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(state, dict):
        return result
    for account in state.values():
        if not isinstance(account, dict):
            continue
        for balances in account.values():
            result.extend(row for row in balances or [] if isinstance(row, dict))
    return result


def _executor_pair(executor: dict[str, Any]) -> str:
    config = executor.get("config")
    if not isinstance(config, dict):
        config = {}
    return str(config.get("trading_pair") or executor.get("trading_pair") or "")


def _executor_type(executor: dict[str, Any]) -> str:
    return str(executor.get("executor_type") or executor.get("type") or "")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    if not config.enabled or config.max_actions == 0:
        return "inventory_cleanup_guard: DISABLED — no cleanup action allowed."

    client = await get_client(context._chat_id, context=context)
    if not client:
        return "inventory_cleanup_guard: BLOCKED — no server available."

    try:
        truth = await authoritative_wallet_state(client, rpc_url=config.rpc_url)
    except Exception as exc:
        return (
            "inventory_cleanup_guard: BLOCKED — authoritative wallet read failed "
            f"({exc}); never sell from cached or partial inventory."
        )
    if truth is None:
        return (
            "inventory_cleanup_guard: BLOCKED — direct chain inventory is unavailable; "
            "never sell from a portfolio-cache-only balance."
        )

    protected = {mint.strip() for mint in config.protected_mints if mint.strip()}
    candidates: list[dict[str, Any]] = []
    unidentified: list[str] = []
    for row in _rows(truth.state):
        units = _num(row.get("units"))
        value = _num(row.get("value"))
        if units <= 0 or value < config.min_value_usd:
            continue
        mint = str(row.get("mint") or "").strip()
        symbol = str(row.get("token") or "UNKNOWN").upper()
        if not mint:
            unidentified.append(symbol)
            continue
        if mint == config.target_mint:
            continue
        # Material stablecoin quote stays deployable for the core. Tiny remnants
        # are normalized to SOL so a completed funding/open cycle does not leave
        # visible token clutter forever.
        if mint in protected and value > config.protected_quote_dust_max_usd:
            continue
        candidates.append(
            {"symbol": symbol, "mint": mint, "units": units, "value": value}
        )

    if unidentified:
        return (
            "inventory_cleanup_guard: BLOCKED — stranded token(s) above the dust floor "
            f"are missing mint metadata: {', '.join(sorted(set(unidentified)))}."
        )
    if not candidates:
        return (
            "inventory_cleanup_guard: CLEAN — no stranded inventory above "
            f"${config.min_value_usd:,.2f}; deployable quote assets were preserved."
        )

    try:
        response = await client.executors.search_executors(status="RUNNING", limit=50)
    except Exception as exc:
        return (
            "inventory_cleanup_guard: BLOCKED — executor registry read failed "
            f"({exc}); duplicate-cleanup status is unknown."
        )
    executors = (
        response.get("data") or response.get("executors") or []
        if isinstance(response, dict)
        else response or []
    )

    running_lp_mints: set[str] = set()
    for executor in executors:
        if not isinstance(executor, dict) or _executor_type(executor) != "lp_executor":
            continue
        running_lp_mints.update(
            mint for mint in _executor_pair(executor).split("-") if mint
        )
    eligible = [
        candidate
        for candidate in candidates
        if candidate["mint"] not in running_lp_mints
    ]
    if not eligible:
        symbols = ", ".join(sorted({row["symbol"] for row in candidates}))
        return (
            "inventory_cleanup_guard: WAIT — stranded token belongs to a RUNNING LP "
            f"({symbols}); cleanup is allowed only after that LP is closed and a fresh "
            "authoritative wallet read confirms the residual."
        )

    candidate = max(eligible, key=lambda row: row["value"])
    pair = f"{candidate['mint']}-{config.target_mint}"
    if any(
        isinstance(executor, dict)
        and _executor_type(executor) == "order_executor"
        and _executor_pair(executor) == pair
        for executor in executors
    ):
        return (
            "inventory_cleanup_guard: WAIT — cleanup executor already RUNNING for "
            f"{candidate['symbol']} ({pair}); verify the next wallet delta."
        )

    return (
        "inventory_cleanup_guard: CLEANUP_REQUIRED — execute exactly one risk-reducing "
        "order_executor MARKET sell, then wait for a fresh authoritative wallet audit "
        "before sizing or cleaning another token. "
        f"token={candidate['symbol']}; value_usd=${candidate['value']:.2f}; "
        f"trading_pair={pair}; side=2; amount={candidate['units']:.12g}; "
        "target=SOL; preserve material USDC/USDT quote but normalize sub-dollar remnants."
    )
