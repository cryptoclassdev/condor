"""Read-only admission check for the micro quick-in/out runner experiment.

The routine measures a candidate pool's current five-minute flow and its recent
one-minute impulse/pullback. It never opens, closes, swaps, or resizes a position.
Token safety and sell-route results are explicit inputs so missing upstream proof
always blocks the plan.
"""

import asyncio
import importlib
from typing import Literal

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from agents.meteora_regime_lp import quick_in_out as quick_in_out_policy
from agents.meteora_regime_lp.routines.runner_scanner import (
    GECKO_NETWORK,
    GECKO_REQUEST_INTERVAL_SEC,
    SOL_MINT,
    VENUE,
    _age_hours,
    _gecko_get,
    _num,
)

CATEGORY = "Risk Management"

# Condor hot-reloads agent routines but not their imported helper modules. Reload
# this pure policy module so a running process can discover a newly added policy
# class without requiring a process restart.
quick_in_out_policy = importlib.reload(quick_in_out_policy)


class Config(BaseModel):
    """Evaluate one candidate using authoritative capital and safety inputs."""

    enabled: bool = False
    mode: Literal["entry", "monitor"] = "entry"
    pool_address: str = Field(default="")
    risk_profile: str = Field(default="balanced")
    allowed_profile: Literal["hunter"] = "hunter"
    portfolio_equity_usd: float = Field(default=0, ge=0)
    runner_budget_usd: float = Field(default=0, ge=0)
    available_deposit_usd: float = Field(default=0, ge=0)
    token_safety_passed: bool = False
    sell_route_verified: bool = False
    open_micro_slots: int = Field(default=0, ge=0)
    micro_attempts_this_session: int = Field(default=0, ge=0)
    entry_m5_volume_usd: float = Field(default=0, ge=0)
    max_attempts_per_session: int = Field(default=1, ge=1)
    max_open_slots: int = Field(default=1, ge=1)
    max_pct_equity: float = Field(default=2.5, gt=0, le=100)
    max_pct_runner_budget: float = Field(default=12.5, gt=0, le=100)
    max_deposit_usd: float = Field(default=50, gt=0)
    min_deposit_usd: float = Field(default=20, gt=0)
    min_m5_vol_usd: float = Field(default=200_000, gt=0)
    min_tvl_usd: float = Field(default=50_000, gt=0)
    min_pool_age_hours: float = Field(default=1, ge=0)
    max_pool_age_hours: float = Field(default=12, gt=0)
    min_bullish_leg_pct: float = Field(default=12, gt=0)
    min_retracement_pct: float = Field(default=2, ge=0)
    max_retracement_pct: float = Field(default=8, gt=0)
    min_peak_age_bars: int = Field(default=1, ge=0)
    max_peak_age_bars: int = Field(default=3, ge=0)
    min_buy_sell_ratio: float = Field(default=1.25, ge=0)
    max_bins: int = Field(default=15, ge=1)
    stop_loss_pct: float = Field(default=3, gt=0)
    take_profit_pct: float = Field(default=5, gt=0)
    volume_decay_exit_ratio: float = Field(default=0.6, gt=0, le=1)
    max_hold_min: float = Field(default=15, gt=0)
    lower_stop_pct: float = Field(default=4, gt=0)


def _blocked(reason: str) -> str:
    return f"quick_in_out_guard: BLOCKED — {reason}"


def _source_failure(config: Config, reason: str) -> str:
    if config.mode == "monitor":
        return f"quick_in_out_guard: EXIT_NOW — unreadable-signal; {reason}"
    return _blocked(reason)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    del context  # Read-only public market data; no wallet or server access is needed.

    if not config.enabled:
        if config.mode == "monitor":
            return "quick_in_out_guard: EXIT_NOW — experiment-disabled"
        return _blocked("experiment is disabled")
    pool = config.pool_address.strip()
    if not pool:
        return _blocked("pool_address is required")

    try:
        async with aiohttp.ClientSession() as session:
            pool_payload = await _gecko_get(
                session, f"networks/{GECKO_NETWORK}/pools/{pool}"
            )
            candle_payload = None
            if config.mode == "entry":
                await asyncio.sleep(GECKO_REQUEST_INTERVAL_SEC)
                candle_payload = await _gecko_get(
                    session,
                    f"networks/{GECKO_NETWORK}/pools/{pool}/ohlcv/minute",
                    {"aggregate": 1, "limit": 16, "currency": "usd"},
                )
    except Exception as exc:
        return _source_failure(
            config, f"market source unavailable ({type(exc).__name__})"
        )

    data = pool_payload.get("data") if isinstance(pool_payload, dict) else None
    if not isinstance(data, dict):
        return _source_failure(config, "specific-pool response was malformed")
    attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    relationships = (
        data.get("relationships") if isinstance(data.get("relationships"), dict) else {}
    )
    dex = ((relationships.get("dex") or {}).get("data") or {}).get("id") or ""
    base_id = ((relationships.get("base_token") or {}).get("data") or {}).get(
        "id"
    ) or ""
    quote_id = ((relationships.get("quote_token") or {}).get("data") or {}).get(
        "id"
    ) or ""
    mints = {base_id.split("_", 1)[-1], quote_id.split("_", 1)[-1]}
    if VENUE not in dex or SOL_MINT not in mints:
        return _source_failure(config, "candidate is not a SOL-quoted Meteora pool")

    age_hours = _age_hours(attrs.get("pool_created_at"))

    volume = (
        attrs.get("volume_usd") if isinstance(attrs.get("volume_usd"), dict) else {}
    )
    transactions = (
        attrs.get("transactions") if isinstance(attrs.get("transactions"), dict) else {}
    )
    m5_transactions = (
        transactions.get("m5") if isinstance(transactions.get("m5"), dict) else {}
    )
    buys = _num(m5_transactions.get("buys"))
    sells = _num(m5_transactions.get("sells"))
    buy_sell_ratio = buys / max(sells, 1.0)

    if config.mode == "monitor":
        decision = quick_in_out_policy.evaluate_quick_in_out_exit(
            entry_m5_volume_usd=config.entry_m5_volume_usd,
            current_m5_volume_usd=_num(volume.get("m5")),
            buy_sell_ratio=buy_sell_ratio,
            signal_age_sec=0.0,
            volume_decay_exit_ratio=config.volume_decay_exit_ratio,
        )
        return (
            f"quick_in_out_guard: {decision.action} — {decision.reason}; current m5 "
            f"${_num(volume.get('m5')):,.0f} vs entry ${config.entry_m5_volume_usd:,.0f}; "
            f"buy/sell {buy_sell_ratio:.2f}."
        )

    if age_hours is None:
        return _blocked("pool age is unknown")

    candle_data = (
        candle_payload.get("data") if isinstance(candle_payload, dict) else None
    )
    candle_attrs = (
        candle_data.get("attributes") if isinstance(candle_data, dict) else None
    )
    candles = candle_attrs.get("ohlcv_list") if isinstance(candle_attrs, dict) else None
    if not isinstance(candles, list):
        return _blocked("one-minute OHLCV response was malformed")
    try:
        signal = quick_in_out_policy.derive_pullback_signal(candles)
    except ValueError as exc:
        return _blocked(str(exc))

    policy = quick_in_out_policy.QuickInOutPolicy(
        allowed_profile=config.allowed_profile,
        max_attempts_per_session=config.max_attempts_per_session,
        max_open_slots=config.max_open_slots,
        max_pct_equity=config.max_pct_equity,
        max_pct_runner_budget=config.max_pct_runner_budget,
        max_deposit_usd=config.max_deposit_usd,
        min_deposit_usd=config.min_deposit_usd,
        min_m5_vol_usd=config.min_m5_vol_usd,
        min_tvl_usd=config.min_tvl_usd,
        min_pool_age_hours=config.min_pool_age_hours,
        max_pool_age_hours=config.max_pool_age_hours,
        min_bullish_leg_pct=config.min_bullish_leg_pct,
        min_retracement_pct=config.min_retracement_pct,
        max_retracement_pct=config.max_retracement_pct,
        min_peak_age_bars=config.min_peak_age_bars,
        max_peak_age_bars=config.max_peak_age_bars,
        min_buy_sell_ratio=config.min_buy_sell_ratio,
        max_bins=config.max_bins,
        stop_loss_pct=config.stop_loss_pct,
        take_profit_pct=config.take_profit_pct,
        volume_decay_exit_ratio=config.volume_decay_exit_ratio,
        max_hold_min=config.max_hold_min,
        lower_stop_pct=config.lower_stop_pct,
    )
    plan = quick_in_out_policy.plan_quick_in_out(
        quick_in_out_policy.QuickInOutObservation(
            enabled=config.enabled,
            risk_profile=config.risk_profile,
            portfolio_equity_usd=config.portfolio_equity_usd,
            runner_budget_usd=config.runner_budget_usd,
            available_deposit_usd=config.available_deposit_usd,
            m5_volume_usd=_num(volume.get("m5")),
            tvl_usd=_num(attrs.get("reserve_in_usd")),
            pool_age_hours=age_hours,
            bullish_leg_pct=signal.bullish_leg_pct,
            retracement_from_peak_pct=signal.retracement_from_peak_pct,
            peak_age_bars=signal.peak_age_bars,
            buy_sell_ratio=buy_sell_ratio,
            source_complete=True,
            token_safety_passed=config.token_safety_passed,
            sell_route_verified=config.sell_route_verified,
            open_micro_slots=config.open_micro_slots,
            micro_attempts_this_session=config.micro_attempts_this_session,
        ),
        policy=policy,
    )

    return (
        f"quick_in_out_guard: {plan.action}. Deposit ${plan.deposit_usd:,.2f}; "
        f"m5 ${_num(volume.get('m5')):,.0f}; TVL ${_num(attrs.get('reserve_in_usd')):,.0f}; "
        f"bull leg {signal.bullish_leg_pct:.1f}%; pullback "
        f"{signal.retracement_from_peak_pct:.1f}%; peak age {signal.peak_age_bars}m; "
        f"buy/sell {buy_sell_ratio:.2f}. {plan.reason} If opened: one-sided SOL quote "
        f"below price, <= {plan.max_bins} bins, {plan.stop_loss_pct:.0f}% stop, "
        f"{plan.take_profit_pct:.0f}% take-profit, {plan.max_hold_min:.0f}m deadline, "
        "no flip/re-chase/averaging."
    )
