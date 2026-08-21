import asyncio

import pytest

from agents.meteora_regime_lp.quick_in_out import (
    QuickInOutObservation,
    QuickInOutPolicy,
    derive_pullback_signal,
    evaluate_quick_in_out_exit,
    plan_quick_in_out,
)
from agents.meteora_regime_lp.routines.quick_in_out_guard import (
    Config as QuickInOutGuardConfig,
)
from agents.meteora_regime_lp.routines.quick_in_out_guard import (
    run as run_quick_in_out_guard,
)
from condor.agents.strategy import StrategyStore


def _qualified_observation(**overrides):
    values = {
        "enabled": True,
        "risk_profile": "hunter",
        "portfolio_equity_usd": 800.0,
        "runner_budget_usd": 160.0,
        "available_deposit_usd": 100.0,
        "m5_volume_usd": 250_000.0,
        "tvl_usd": 100_000.0,
        "pool_age_hours": 4.0,
        "bullish_leg_pct": 18.0,
        "retracement_from_peak_pct": 4.0,
        "peak_age_bars": 2,
        "buy_sell_ratio": 1.5,
        "source_complete": True,
        "token_safety_passed": True,
        "sell_route_verified": True,
        "open_micro_slots": 0,
        "micro_attempts_this_session": 0,
    }
    values.update(overrides)
    return QuickInOutObservation(**values)


def test_quick_in_out_is_blocked_when_feature_flag_is_off():
    plan = plan_quick_in_out(_qualified_observation(enabled=False))

    assert plan.action == "BLOCKED"
    assert plan.deposit_usd == 0.0
    assert "disabled" in plan.reason.lower()


def test_qualified_hunter_signal_gets_one_micro_deposit():
    plan = plan_quick_in_out(_qualified_observation())

    assert plan.action == "ELIGIBLE"
    assert plan.deposit_usd == 20.0
    assert plan.max_hold_min == 15.0
    assert plan.stop_loss_pct == 3.0
    assert plan.take_profit_pct == 5.0
    assert plan.volume_decay_exit_ratio == 0.6
    assert plan.max_bins == 15
    assert plan.lower_stop_pct == 4.0


def test_current_small_book_is_blocked_because_rent_would_dominate():
    plan = plan_quick_in_out(
        _qualified_observation(
            portfolio_equity_usd=81.84,
            runner_budget_usd=16.37,
            available_deposit_usd=16.37,
        )
    )

    assert plan.action == "BLOCKED"
    assert plan.deposit_usd == 0.0
    assert "$20 economic floor" in plan.reason


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"risk_profile": "balanced"}, "hunter profile"),
        ({"m5_volume_usd": 199_999.0}, "five-minute volume"),
        ({"tvl_usd": 49_999.0}, "tvl"),
        ({"pool_age_hours": 0.5}, "pool age"),
        ({"pool_age_hours": 12.1}, "pool age"),
        ({"bullish_leg_pct": 11.9}, "bullish leg"),
        ({"retracement_from_peak_pct": 1.9}, "retracement"),
        ({"retracement_from_peak_pct": 8.1}, "retracement"),
        ({"peak_age_bars": 4}, "first retracement"),
        ({"buy_sell_ratio": 1.24}, "buy/sell"),
        ({"source_complete": False}, "source"),
        ({"token_safety_passed": False}, "token safety"),
        ({"sell_route_verified": False}, "sell route"),
        ({"open_micro_slots": 1}, "slot"),
        ({"micro_attempts_this_session": 1}, "one attempt"),
        ({"m5_volume_usd": float("nan")}, "non-finite"),
        ({"portfolio_equity_usd": float("nan")}, "non-finite"),
    ],
)
def test_every_mandatory_quick_in_out_gate_fails_closed(override, reason):
    plan = plan_quick_in_out(_qualified_observation(**override))

    assert plan.action == "BLOCKED"
    assert plan.deposit_usd == 0.0
    assert reason in plan.reason.lower()


def test_pullback_signal_is_derived_from_recent_one_minute_candles():
    candles = []
    closes = [
        101,
        102,
        103,
        105,
        107,
        109,
        111,
        113,
        115,
        116,
        117,
        118,
        119,
        119,
        117,
        115.2,
    ]
    for index, close in enumerate(closes):
        high = 120.0 if index == 13 else close
        low = 100.0 if index == 0 else min(close, closes[max(index - 1, 0)])
        candles.append([index, close, high, low, close, 1_000.0])

    signal = derive_pullback_signal(list(reversed(candles)))

    assert signal.bullish_leg_pct == 20.0
    assert signal.retracement_from_peak_pct == 4.0
    assert signal.peak_age_bars == 2


@pytest.mark.parametrize(
    ("current_m5", "buy_sell_ratio", "signal_age_sec", "reason"),
    [
        (149_999.0, 1.5, 10.0, "volume-decay"),
        (200_000.0, 0.99, 10.0, "sell-dominance"),
        (200_000.0, 1.5, 91.0, "stale-signal"),
    ],
)
def test_micro_position_exits_as_soon_as_the_thesis_breaks(
    current_m5, buy_sell_ratio, signal_age_sec, reason
):
    decision = evaluate_quick_in_out_exit(
        entry_m5_volume_usd=250_000.0,
        current_m5_volume_usd=current_m5,
        buy_sell_ratio=buy_sell_ratio,
        signal_age_sec=signal_age_sec,
    )

    assert decision.action == "EXIT_NOW"
    assert reason in decision.reason


def test_micro_position_holds_only_while_volume_and_buy_flow_remain_valid():
    decision = evaluate_quick_in_out_exit(
        entry_m5_volume_usd=250_000.0,
        current_m5_volume_usd=150_000.0,
        buy_sell_ratio=1.0,
        signal_age_sec=90.0,
    )

    assert decision.action == "HOLD"


def test_live_strategy_keeps_micro_experiment_disabled_by_default():
    strategy = StrategyStore().get("meteora_regime_lp", "regime_lp_operator")

    assert strategy is not None
    config = strategy.default_config["quick_in_out"]
    assert config["enabled"] is False
    assert config["allowed_profile"] == "hunter"
    assert config["max_attempts_per_session"] == 1
    assert config["max_pct_equity"] == 2.5
    assert config["max_hold_min"] == 15
    routine_config = QuickInOutGuardConfig(**config).model_dump()
    for key, value in config.items():
        assert routine_config[key] == value


def test_routine_profile_gate_cannot_be_repointed_by_runtime_config():
    with pytest.raises(ValueError):
        QuickInOutGuardConfig(allowed_profile="balanced")


def test_disabling_the_experiment_while_monitoring_demands_an_exit():
    result = asyncio.run(
        run_quick_in_out_guard(
            QuickInOutGuardConfig(enabled=False, mode="monitor"), context=None
        )
    )

    assert "EXIT_NOW" in result
    assert "experiment-disabled" in result


def test_policy_parameters_are_not_silently_ignored():
    plan = plan_quick_in_out(
        _qualified_observation(
            portfolio_equity_usd=2_000.0,
            runner_budget_usd=400.0,
        ),
        policy=QuickInOutPolicy(
            max_deposit_usd=25.0,
            max_hold_min=10.0,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
        ),
    )

    assert plan.action == "ELIGIBLE"
    assert plan.deposit_usd == 25.0
    assert plan.max_hold_min == 10.0
    assert plan.stop_loss_pct == 2.0
    assert plan.take_profit_pct == 4.0


def test_invalid_policy_bounds_fail_closed():
    plan = plan_quick_in_out(
        _qualified_observation(),
        policy=QuickInOutPolicy(
            min_retracement_pct=9.0,
            max_retracement_pct=8.0,
        ),
    )

    assert plan.action == "BLOCKED"
    assert "invalid policy" in plan.reason.lower()
