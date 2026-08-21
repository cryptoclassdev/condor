"""Deterministic admission policy for a micro quick-in/out runner experiment."""

import math
from dataclasses import dataclass
from typing import Literal

QuickInOutAction = Literal["BLOCKED", "ELIGIBLE"]
QuickInOutExitAction = Literal["HOLD", "EXIT_NOW"]

MAX_HOLD_MIN = 15.0
STOP_LOSS_PCT = 3.0
TAKE_PROFIT_PCT = 5.0
VOLUME_DECAY_EXIT_RATIO = 0.6
MAX_BINS = 15
LOWER_STOP_PCT = 4.0


@dataclass(frozen=True)
class QuickInOutObservation:
    enabled: bool
    risk_profile: str
    portfolio_equity_usd: float
    runner_budget_usd: float
    available_deposit_usd: float
    m5_volume_usd: float
    tvl_usd: float
    pool_age_hours: float
    bullish_leg_pct: float
    retracement_from_peak_pct: float
    peak_age_bars: int
    buy_sell_ratio: float
    source_complete: bool
    token_safety_passed: bool
    sell_route_verified: bool
    open_micro_slots: int
    micro_attempts_this_session: int


@dataclass(frozen=True)
class QuickInOutPolicy:
    allowed_profile: str = "hunter"
    max_attempts_per_session: int = 1
    max_open_slots: int = 1
    max_pct_equity: float = 2.5
    max_pct_runner_budget: float = 12.5
    max_deposit_usd: float = 50.0
    min_deposit_usd: float = 20.0
    min_m5_vol_usd: float = 200_000.0
    min_tvl_usd: float = 50_000.0
    min_pool_age_hours: float = 1.0
    max_pool_age_hours: float = 12.0
    min_bullish_leg_pct: float = 12.0
    min_retracement_pct: float = 2.0
    max_retracement_pct: float = 8.0
    min_peak_age_bars: int = 1
    max_peak_age_bars: int = 3
    min_buy_sell_ratio: float = 1.25
    max_bins: int = MAX_BINS
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_pct: float = TAKE_PROFIT_PCT
    volume_decay_exit_ratio: float = VOLUME_DECAY_EXIT_RATIO
    max_hold_min: float = MAX_HOLD_MIN
    lower_stop_pct: float = LOWER_STOP_PCT


@dataclass(frozen=True)
class PullbackSignal:
    bullish_leg_pct: float
    retracement_from_peak_pct: float
    peak_age_bars: int


@dataclass(frozen=True)
class QuickInOutExit:
    action: QuickInOutExitAction
    reason: str


def evaluate_quick_in_out_exit(
    *,
    entry_m5_volume_usd: float,
    current_m5_volume_usd: float,
    buy_sell_ratio: float,
    signal_age_sec: float,
    volume_decay_exit_ratio: float = VOLUME_DECAY_EXIT_RATIO,
    max_signal_age_sec: float = 90.0,
) -> QuickInOutExit:
    """Exit when the short-lived volume/momentum thesis can no longer be proven."""

    values = (
        entry_m5_volume_usd,
        current_m5_volume_usd,
        buy_sell_ratio,
        signal_age_sec,
    )
    if not all(math.isfinite(value) for value in values) or entry_m5_volume_usd <= 0:
        return QuickInOutExit("EXIT_NOW", "unreadable-signal")
    if signal_age_sec > max_signal_age_sec:
        return QuickInOutExit("EXIT_NOW", "stale-signal")
    if current_m5_volume_usd < entry_m5_volume_usd * volume_decay_exit_ratio:
        return QuickInOutExit("EXIT_NOW", "volume-decay")
    if buy_sell_ratio < 1.0:
        return QuickInOutExit("EXIT_NOW", "sell-dominance")
    return QuickInOutExit("HOLD", "volume-and-buy-flow-valid")


def derive_pullback_signal(candles: list[list[float]]) -> PullbackSignal:
    """Measure the most recent impulse and pullback from one-minute OHLCV rows."""

    valid: list[list[float]] = []
    for candle in candles:
        if len(candle) < 6:
            continue
        try:
            row = [float(value) for value in candle[:6]]
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in row) and min(row[1:5]) > 0:
            valid.append(row)
    if len(valid) < 5:
        raise ValueError("at least five valid one-minute candles are required")

    recent = sorted(valid, key=lambda row: row[0])[-16:]
    peak_index = max(range(len(recent)), key=lambda index: recent[index][2])
    peak = recent[peak_index][2]
    baseline = min(row[3] for row in recent[: peak_index + 1])
    current_close = recent[-1][4]
    return PullbackSignal(
        bullish_leg_pct=round((peak / baseline - 1.0) * 100.0, 3),
        retracement_from_peak_pct=round((peak - current_close) / peak * 100.0, 3),
        peak_age_bars=len(recent) - 1 - peak_index,
    )


@dataclass(frozen=True)
class QuickInOutPlan:
    action: QuickInOutAction
    deposit_usd: float
    max_hold_min: float
    stop_loss_pct: float
    take_profit_pct: float
    volume_decay_exit_ratio: float
    max_bins: int
    lower_stop_pct: float
    reason: str


def _blocked(reason: str, policy: QuickInOutPolicy) -> QuickInOutPlan:
    return QuickInOutPlan(
        action="BLOCKED",
        deposit_usd=0.0,
        max_hold_min=policy.max_hold_min,
        stop_loss_pct=policy.stop_loss_pct,
        take_profit_pct=policy.take_profit_pct,
        volume_decay_exit_ratio=policy.volume_decay_exit_ratio,
        max_bins=policy.max_bins,
        lower_stop_pct=policy.lower_stop_pct,
        reason=reason,
    )


def plan_quick_in_out(
    observation: QuickInOutObservation,
    policy: QuickInOutPolicy = QuickInOutPolicy(),
) -> QuickInOutPlan:
    """Return a fail-closed entry plan; this function never executes a position."""

    def block(reason: str) -> QuickInOutPlan:
        return _blocked(reason, policy)

    if not observation.enabled:
        return block("Quick-in/out experiment is disabled.")

    policy_values = (
        policy.max_pct_equity,
        policy.max_pct_runner_budget,
        policy.max_deposit_usd,
        policy.min_deposit_usd,
        policy.min_m5_vol_usd,
        policy.min_tvl_usd,
        policy.min_pool_age_hours,
        policy.max_pool_age_hours,
        policy.min_bullish_leg_pct,
        policy.min_retracement_pct,
        policy.max_retracement_pct,
        policy.min_buy_sell_ratio,
        policy.stop_loss_pct,
        policy.take_profit_pct,
        policy.volume_decay_exit_ratio,
        policy.max_hold_min,
        policy.lower_stop_pct,
    )
    policy_invalid = (
        not all(math.isfinite(value) for value in policy_values)
        or policy.max_attempts_per_session < 1
        or policy.max_open_slots < 1
        or not 0 < policy.max_pct_equity <= 100
        or not 0 < policy.max_pct_runner_budget <= 100
        or not 0 < policy.min_deposit_usd <= policy.max_deposit_usd
        or not 0 <= policy.min_pool_age_hours <= policy.max_pool_age_hours
        or not 0 <= policy.min_retracement_pct <= policy.max_retracement_pct
        or not 0 <= policy.min_peak_age_bars <= policy.max_peak_age_bars
        or policy.max_bins < 1
        or policy.stop_loss_pct <= 0
        or policy.take_profit_pct <= 0
        or not 0 < policy.volume_decay_exit_ratio <= 1
        or policy.max_hold_min <= 0
        or policy.lower_stop_pct <= 0
    )
    if policy_invalid:
        return block("Invalid policy bounds; entry fails closed.")

    if observation.risk_profile != policy.allowed_profile:
        return block(
            f"Quick-in/out is restricted to the more-risky {policy.allowed_profile} profile."
        )
    numeric_inputs = (
        observation.portfolio_equity_usd,
        observation.runner_budget_usd,
        observation.available_deposit_usd,
        observation.m5_volume_usd,
        observation.tvl_usd,
        observation.pool_age_hours,
        observation.bullish_leg_pct,
        observation.retracement_from_peak_pct,
        observation.buy_sell_ratio,
    )
    if not all(math.isfinite(value) for value in numeric_inputs):
        return block("A required input is non-finite; entry fails closed.")
    if not observation.source_complete:
        return block("Source coverage is incomplete; entry fails closed.")
    if not observation.token_safety_passed:
        return block("Token safety did not pass every configured gate.")
    if not observation.sell_route_verified:
        return block("A live sell route was not verified before entry.")
    if observation.open_micro_slots >= policy.max_open_slots:
        return block("The single quick-in/out slot is already occupied.")
    if observation.micro_attempts_this_session >= policy.max_attempts_per_session:
        return block("Only one attempt is allowed in a strategy session.")
    if observation.m5_volume_usd < policy.min_m5_vol_usd:
        return block(
            f"Five-minute volume is below the ${policy.min_m5_vol_usd:,.0f} entry gate."
        )
    if observation.tvl_usd < policy.min_tvl_usd:
        return block(f"TVL is below the ${policy.min_tvl_usd:,.0f} liquidity floor.")
    if not (
        policy.min_pool_age_hours
        <= observation.pool_age_hours
        <= policy.max_pool_age_hours
    ):
        return block(
            f"Pool age must be between {policy.min_pool_age_hours:g} and "
            f"{policy.max_pool_age_hours:g} hours."
        )
    if observation.bullish_leg_pct < policy.min_bullish_leg_pct:
        return block(
            f"The measured bullish leg is below {policy.min_bullish_leg_pct:g}%."
        )
    if not (
        policy.min_retracement_pct
        <= observation.retracement_from_peak_pct
        <= policy.max_retracement_pct
    ):
        return block(
            f"Retracement must be between {policy.min_retracement_pct:g}% and "
            f"{policy.max_retracement_pct:g}% from the local peak."
        )
    if not (
        policy.min_peak_age_bars
        <= observation.peak_age_bars
        <= policy.max_peak_age_bars
    ):
        return block("The signal is not the first retracement after the local peak.")
    if observation.buy_sell_ratio < policy.min_buy_sell_ratio:
        return block(
            "The five-minute buy/sell transaction ratio is below "
            f"{policy.min_buy_sell_ratio:g}."
        )

    deposit_usd = min(
        observation.portfolio_equity_usd * policy.max_pct_equity / 100.0,
        observation.runner_budget_usd * policy.max_pct_runner_budget / 100.0,
        observation.available_deposit_usd,
        policy.max_deposit_usd,
    )
    if deposit_usd < policy.min_deposit_usd:
        return block(
            f"The capped micro deposit is below the ${policy.min_deposit_usd:,.0f} "
            "economic floor."
        )

    return QuickInOutPlan(
        action="ELIGIBLE",
        deposit_usd=round(deposit_usd, 2),
        max_hold_min=policy.max_hold_min,
        stop_loss_pct=policy.stop_loss_pct,
        take_profit_pct=policy.take_profit_pct,
        volume_decay_exit_ratio=policy.volume_decay_exit_ratio,
        max_bins=policy.max_bins,
        lower_stop_pct=policy.lower_stop_pct,
        reason="Qualified micro signal within the runner and real-equity caps.",
    )
