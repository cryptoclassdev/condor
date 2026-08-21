"""Deterministic SOL hedge sizing for Meteora LP inventory."""

import math
from dataclasses import dataclass
from typing import Literal

HedgeAction = Literal["BLOCKED", "HOLD", "INCREASE_SHORT", "REDUCE_SHORT"]


@dataclass(frozen=True)
class HedgePlan:
    action: HedgeAction
    target_short_usd: float
    adjustment_usd: float
    sol_exposure_usd: float
    hedge_cap_usd: float
    reason: str


def plan_sol_hedge(
    *,
    sol_amount: float,
    sol_price_usd: float,
    price_age_sec: float,
    max_price_age_sec: float,
    portfolio_equity_usd: float,
    current_short_usd: float,
    coverage_pct: float = 100.0,
    max_hedge_pct_equity: float = 25.0,
    min_order_usd: float = 10.0,
    rebalance_band_usd: float = 2.0,
) -> HedgePlan:
    """Return a fail-closed recommendation for offsetting measured SOL inventory."""

    if (
        not math.isfinite(sol_price_usd)
        or not math.isfinite(portfolio_equity_usd)
        or sol_price_usd <= 0
        or portfolio_equity_usd <= 0
    ):
        return HedgePlan(
            action="BLOCKED",
            target_short_usd=0.0,
            adjustment_usd=0.0,
            sol_exposure_usd=0.0,
            hedge_cap_usd=0.0,
            reason="SOL price and real portfolio equity must both be positive.",
        )

    if price_age_sec > max_price_age_sec:
        return HedgePlan(
            action="BLOCKED",
            target_short_usd=0.0,
            adjustment_usd=0.0,
            sol_exposure_usd=0.0,
            hedge_cap_usd=0.0,
            reason=(
                f"SOL price is stale ({price_age_sec:.0f}s > "
                f"{max_price_age_sec:.0f}s); hedge changes fail closed."
            ),
        )

    sol_exposure_usd = max(sol_amount, 0.0) * max(sol_price_usd, 0.0)
    hedge_cap_usd = (
        max(portfolio_equity_usd, 0.0) * max(max_hedge_pct_equity, 0.0) / 100.0
    )
    desired_short_usd = sol_exposure_usd * max(coverage_pct, 0.0) / 100.0
    target_short_usd = min(desired_short_usd, hedge_cap_usd)
    delta = target_short_usd - max(current_short_usd, 0.0)

    if abs(delta) < max(min_order_usd, rebalance_band_usd, 0.0):
        return HedgePlan(
            action="HOLD",
            target_short_usd=round(target_short_usd, 2),
            adjustment_usd=0.0,
            sol_exposure_usd=round(sol_exposure_usd, 2),
            hedge_cap_usd=round(hedge_cap_usd, 2),
            reason="Required adjustment is below the minimum order/rebalance band.",
        )

    return HedgePlan(
        action="INCREASE_SHORT" if delta > 0 else "REDUCE_SHORT",
        target_short_usd=round(target_short_usd, 2),
        adjustment_usd=round(delta, 2),
        sol_exposure_usd=round(sol_exposure_usd, 2),
        hedge_cap_usd=round(hedge_cap_usd, 2),
        reason="Target offsets measured SOL inventory within the real-equity hedge cap.",
    )
