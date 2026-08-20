"""Deterministic capital accounting for the Meteora LP strategy.

The configured capital is a ceiling, not evidence that the wallet contains that
amount.  Sizing and dollar loss limits must use the smaller of that ceiling and
the current wallet-plus-on-chain-position value.
"""

from __future__ import annotations

from dataclasses import dataclass


def _money(value: float) -> float:
    return round(max(0.0, float(value)), 2)


@dataclass(frozen=True)
class CapitalPlan:
    effective_equity_usd: float
    risk_capital_usd: float
    daily_loss_limit_usd: float
    gas_reserve_usd: float
    next_position_rent_usd: float
    max_new_deposit_usd: float
    sleeve_budgets_usd: dict[str, float]


def build_capital_plan(
    *,
    configured_capital_usd: float,
    wallet_total_usd: float,
    liquid_quote_usd: float,
    open_lp_usd: float,
    native_price_usd: float,
    min_native_reserve: float,
    next_position_rent_native: float,
    daily_loss_limit_pct: float,
    sleeve_percentages: dict[str, float],
) -> CapitalPlan:
    """Return a conservative plan from authoritative, same-tick inputs.

    ``wallet_total_usd`` includes stranded inventory because it remains part of
    equity.  Only ``liquid_quote_usd`` funds a new position.  Gas and the next
    position's rent are deducted from that liquid balance before an entry can be
    considered.
    """

    inputs = (
        configured_capital_usd,
        wallet_total_usd,
        liquid_quote_usd,
        open_lp_usd,
        native_price_usd,
        min_native_reserve,
        next_position_rent_native,
        daily_loss_limit_pct,
    )
    if any(float(value) < 0 for value in inputs):
        raise ValueError("capital inputs cannot be negative")
    if sum(float(v) for v in sleeve_percentages.values()) > 100.000001:
        raise ValueError("sleeve percentages cannot exceed 100%")

    effective_equity = float(wallet_total_usd) + float(open_lp_usd)
    risk_capital = min(float(configured_capital_usd), effective_equity)
    gas_reserve = float(min_native_reserve) * float(native_price_usd)
    rent = float(next_position_rent_native) * float(native_price_usd)
    max_new_deposit = max(0.0, float(liquid_quote_usd) - gas_reserve - rent)

    return CapitalPlan(
        effective_equity_usd=_money(effective_equity),
        risk_capital_usd=_money(risk_capital),
        daily_loss_limit_usd=_money(risk_capital * float(daily_loss_limit_pct) / 100.0),
        gas_reserve_usd=_money(gas_reserve),
        next_position_rent_usd=_money(rent),
        max_new_deposit_usd=_money(max_new_deposit),
        sleeve_budgets_usd={
            sleeve: _money(risk_capital * float(pct) / 100.0)
            for sleeve, pct in sleeve_percentages.items()
        },
    )
