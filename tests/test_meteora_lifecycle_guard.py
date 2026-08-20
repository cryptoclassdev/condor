from datetime import datetime, timezone

from agents.meteora_regime_lp.lifecycle import evaluate_lifecycle

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
