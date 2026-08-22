import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from agents.meteora_regime_lp.lifecycle import evaluate_lifecycle
from agents.meteora_regime_lp.routines import lifecycle_guard

NOW = datetime(2026, 8, 20, 17, 10, tzinfo=timezone.utc)


def _satellite(*, pnl=-0.01, base=100.0, quote=0.1, price=0.0004):
    return {
        "executor_id": "sat-executor",
        "created_at": "2026-08-20T15:47:22.053708+00:00",
        "trading_pair": "base-mint-So11111111111111111111111111111111111111112",
        "net_pnl_pct": pnl,
        "custom_info": {
            "position_address": "sat-position",
            "base_amount": base,
            "quote_amount": quote,
            "current_price": price,
            "state": "IN_RANGE",
        },
    }


def _core_out_of_range(*, seconds=41777, price=94.46):
    return {
        "executor_id": "core-executor",
        "created_at": "2026-08-20T05:00:00+00:00",
        "trading_pair": (
            "So11111111111111111111111111111111111111112-"
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        ),
        "net_pnl_pct": -0.043,
        "custom_info": {
            "position_address": "core-position",
            "base_amount": 0.644,
            "quote_amount": 0.0,
            "current_price": price,
            "lower_price": 98.59,
            "upper_price": 100.63,
            "out_of_range_seconds": seconds,
            "state": "OUT_OF_RANGE",
        },
    }


def test_core_out_of_range_past_timeout_is_exit_now():
    rows, _ = evaluate_lifecycle(
        [_core_out_of_range()],
        now=NOW,
        previous_state={},
        satellite_max_hold_min=120,
        runner_max_hold_min=90,
        satellite_stop_loss_pct=8,
        runner_stop_loss_pct=6,
        out_of_range_max_sec=1800,
        out_of_range_buffer_pct=0.5,
        rebalance_cooldown_sec=900,
    )

    assert rows[0].sleeve == "core"
    assert rows[0].action == "EXIT_NOW"
    assert "out-of-range-timeout" in rows[0].reasons


def test_out_of_range_hysteresis_does_not_exit_inside_time_or_price_buffer():
    for executor in (
        _core_out_of_range(seconds=1799),
        _core_out_of_range(seconds=41777, price=98.30),
    ):
        rows, _ = evaluate_lifecycle(
            [executor],
            now=NOW,
            previous_state={},
            satellite_max_hold_min=120,
            runner_max_hold_min=90,
            satellite_stop_loss_pct=8,
            runner_stop_loss_pct=6,
            out_of_range_max_sec=1800,
            out_of_range_buffer_pct=0.5,
            rebalance_cooldown_sec=900,
        )

        assert rows[0].action == "MONITOR"
        assert "out-of-range-timeout" not in rows[0].reasons


def test_satellite_has_exact_deadline_and_minutes_remaining():
    rows, state = evaluate_lifecycle(
        [_satellite()],
        now=NOW,
        previous_state={},
        satellite_max_hold_min=120,
        runner_max_hold_min=90,
        satellite_stop_loss_pct=8,
        runner_stop_loss_pct=6,
    )

    assert rows[0].sleeve == "satellite"
    assert rows[0].deadline_utc == "2026-08-20 17:47:22 UTC"
    assert rows[0].minutes_remaining == 37.4
    assert rows[0].action == "MONITOR"
    assert state["sat-position"]["fill_pct"] == 28.57


def test_large_fill_change_forces_regime_recheck():
    rows, _ = evaluate_lifecycle(
        [_satellite(base=300, quote=0.04)],
        now=NOW,
        previous_state={"sat-position": {"fill_pct": 13.0, "peak_pnl_pct": 0.0}},
        satellite_max_hold_min=120,
        runner_max_hold_min=90,
        satellite_stop_loss_pct=8,
        runner_stop_loss_pct=6,
    )

    assert rows[0].fill_pct == 75.0
    assert rows[0].fill_change_pct == 62.0
    assert rows[0].action == "REGIME_RECHECK"


def test_deadline_and_stop_are_exit_now_not_soft_hints():
    after_deadline = datetime(2026, 8, 20, 17, 50, tzinfo=timezone.utc)
    rows, _ = evaluate_lifecycle(
        [_satellite(pnl=-0.09)],
        now=after_deadline,
        previous_state={},
        satellite_max_hold_min=120,
        runner_max_hold_min=90,
        satellite_stop_loss_pct=8,
        runner_stop_loss_pct=6,
    )

    assert rows[0].action == "EXIT_NOW"
    assert "max-hold" in rows[0].reasons
    assert "stop-loss" in rows[0].reasons


def test_micro_runner_has_its_own_take_profit_stop_and_deadline():
    profitable = _satellite(pnl=0.05)
    profitable["executor_id"] = "micro-profit"
    profitable["created_at"] = "2026-08-20T17:05:00+00:00"
    stopped = _satellite(pnl=-0.03)
    stopped["executor_id"] = "micro-stop"
    stopped["created_at"] = "2026-08-20T17:05:00+00:00"
    expired = _satellite(pnl=0.0)
    expired["executor_id"] = "micro-expired"
    expired["created_at"] = "2026-08-20T16:54:00+00:00"

    rows, _ = evaluate_lifecycle(
        [profitable, stopped, expired],
        now=NOW,
        previous_state={},
        satellite_max_hold_min=120,
        runner_max_hold_min=90,
        satellite_stop_loss_pct=8,
        runner_stop_loss_pct=6,
        micro_runner_executor_ids=["micro-profit", "micro-stop", "micro-expired"],
        micro_runner_max_hold_min=15,
        micro_runner_stop_loss_pct=3,
        micro_runner_take_profit_pct=5,
    )

    assert [row.sleeve for row in rows] == ["runner_micro"] * 3
    assert rows[0].action == "EXIT_NOW"
    assert rows[0].reasons == ("take-profit",)
    assert rows[1].reasons == ("stop-loss",)
    assert rows[2].reasons == ("max-hold",)


def test_hot_reloaded_guard_supports_a_legacy_lifecycle_module(monkeypatch):
    class Executors:
        async def search_executors(self, **_kwargs):
            return {"data": []}

    class Client:
        executors = Executors()

    async def fake_get_client(*_args, **_kwargs):
        return Client()

    def legacy_evaluate(
        executors,
        *,
        now,
        previous_state,
        satellite_max_hold_min,
        runner_max_hold_min,
        satellite_stop_loss_pct,
        runner_stop_loss_pct,
        runner_executor_ids=(),
        material_fill_change_pct=20,
        early_exit_buffer_pct=2,
    ):
        del (
            executors,
            now,
            previous_state,
            satellite_max_hold_min,
            runner_max_hold_min,
            satellite_stop_loss_pct,
            runner_stop_loss_pct,
            runner_executor_ids,
            material_fill_change_pct,
            early_exit_buffer_pct,
        )
        return [], {}

    monkeypatch.setattr(lifecycle_guard, "get_client", fake_get_client)
    monkeypatch.setattr(lifecycle_guard, "evaluate_lifecycle", legacy_evaluate)
    context = SimpleNamespace(_chat_id=1, user_data={})

    result = asyncio.run(
        lifecycle_guard.run(
            lifecycle_guard.Config(micro_runner_executor_ids=["micro"]), context
        )
    )

    assert result == "lifecycle_guard: no RUNNING LP executors."
