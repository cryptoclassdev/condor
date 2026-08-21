import asyncio

import pytest

from agents.meteora_regime_lp.outcome_learning import (
    FileLearningStore,
    LearningState,
    OutcomeLearner,
    PositionAttempt,
)
from agents.meteora_regime_lp.routines import outcome_learner


def test_failed_create_with_chain_footprint_blocks_duplicate_retry():
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id="create-1",
            action="create",
            pool_address="pool-1",
            sleeve="core",
            executor_status="FAILED",
            chain_position_found=True,
            wallet_delta_usd=-22.0,
        ),
        LearningState(),
    )

    assert result.outcome == "FALSE_FAILURE_ORPHAN"
    assert result.retry_allowed is False
    assert result.next_action == "ADOPT_OR_RECOVER"
    gate = result.state.pool_gates["pool-1"]
    assert gate.reason == "false-failure-orphan"
    assert gate.requires_reconciliation is True
    assert gate.release_after_tick is None


def test_reported_close_with_live_chain_position_quarantines_pool():
    """Session 25 tick 327: a successful stop report left the LP on-chain."""
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id="close-live-orphan",
            action="close",
            pool_address="pool-live",
            sleeve="satellite",
            executor_status="SUCCESS",
            chain_position_found=True,
            wallet_delta_usd=0.0,
            confirmation_age_ticks=1,
            exit_reason="out-of-range fast re-chase",
            observed_tick=327,
        ),
        LearningState(),
    )

    assert result.outcome == "FALSE_SUCCESS_ORPHAN"
    assert result.retry_allowed is False
    assert result.next_action == "RECOVER_CLOSE_ORPHAN"
    gate = result.state.pool_gates["pool-live"]
    assert gate.reason == "false-success-close-orphan"
    assert gate.requires_reconciliation is True


def test_success_without_chain_or_wallet_footprint_cleans_ghost_before_retry():
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id="create-2",
            action="create",
            pool_address="pool-2",
            sleeve="core",
            executor_status="RUNNING",
            chain_position_found=False,
            wallet_delta_usd=0.0,
            confirmation_age_ticks=1,
        ),
        LearningState(),
    )

    assert result.outcome == "FALSE_SUCCESS_GHOST"
    assert result.retry_allowed is False
    assert result.next_action == "CLEAN_GHOST_THEN_RETRY"


def test_same_tick_missing_footprint_is_pending_not_a_failure():
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id="create-3",
            action="create",
            pool_address="pool-3",
            sleeve="core",
            executor_status="RUNNING",
            chain_position_found=False,
            wallet_delta_usd=0.0,
            confirmation_age_ticks=0,
        ),
        LearningState(),
    )

    assert result.outcome == "PENDING_CONFIRMATION"
    assert result.retry_allowed is False
    assert result.next_action == "VERIFY_NEXT_TICK"
    assert "pool-3" not in result.state.pool_gates


def test_next_tick_chain_and_wallet_confirmation_releases_pool_gate():
    learner = OutcomeLearner()
    suspected = learner.observe(
        PositionAttempt(
            attempt_id="create-4",
            action="create",
            pool_address="pool-4",
            sleeve="core",
            executor_status="RUNNING",
            chain_position_found=False,
            wallet_delta_usd=0.0,
            confirmation_age_ticks=1,
        ),
        LearningState(),
    )

    confirmed = learner.observe(
        PositionAttempt(
            attempt_id="create-4",
            action="create",
            pool_address="pool-4",
            sleeve="core",
            executor_status="RUNNING",
            chain_position_found=True,
            wallet_delta_usd=-22.0,
            confirmation_age_ticks=2,
        ),
        suspected.state,
    )

    assert confirmed.outcome == "CONFIRMED_SUCCESS"
    assert confirmed.next_action == "MONITOR"
    assert "pool-4" not in confirmed.state.pool_gates


def test_repeated_reconciled_balance_failures_increase_only_funding_haircut():
    learner = OutcomeLearner()
    attempt = PositionAttempt(
        attempt_id="balance-1",
        action="create",
        pool_address="pool-5",
        sleeve="satellite",
        executor_status="FAILED",
        chain_position_found=False,
        wallet_delta_usd=0.0,
        error_message="simulation failed: insufficient funds custom program error: 0x1",
        confirmation_age_ticks=1,
    )

    first = learner.observe(attempt, LearningState())
    second = learner.observe(
        PositionAttempt(**{**attempt.__dict__, "attempt_id": "balance-2"}),
        first.state,
    )

    assert first.outcome == "INSUFFICIENT_BALANCE"
    assert first.state.policy.entry_haircut_bps == 50
    assert second.state.policy.entry_haircut_bps == 75
    assert second.state.last_adjustment == "entry_haircut_bps:50->75"
    assert second.retry_allowed is True
    assert second.next_action == "RETRY_NEXT_DEEP_TICK"


def test_repeated_rpc_failures_back_off_without_changing_entry_risk():
    learner = OutcomeLearner()
    attempt = PositionAttempt(
        attempt_id="rpc-1",
        action="monitor",
        pool_address="pool-6",
        sleeve="core",
        executor_status="ERROR",
        error_message="429 Too Many Requests: server disconnected",
    )

    first = learner.observe(attempt, LearningState())
    second = learner.observe(
        PositionAttempt(**{**attempt.__dict__, "attempt_id": "rpc-2"}),
        first.state,
    )

    assert first.outcome == "RPC_TRANSIENT"
    assert second.state.policy.rpc_backoff_scale == 1.5
    assert second.state.policy.entry_haircut_bps == 50
    assert second.retry_allowed is False
    assert second.next_action == "RETRY_RECONCILIATION"


def test_learning_state_and_evidence_survive_restart(tmp_path):
    store = FileLearningStore(tmp_path)
    learner = OutcomeLearner()
    attempt = PositionAttempt(
        attempt_id="persist-1",
        action="monitor",
        pool_address="pool-7",
        sleeve="core",
        executor_status="ERROR",
        error_message="fetch failed",
    )
    result = learner.observe(attempt, LearningState())

    store.record(attempt, result)
    restored = FileLearningStore(tmp_path).load_state()

    assert restored.outcome_counts == {"RPC_TRANSIENT": 1}
    ledger = (tmp_path / "outcomes.jsonl").read_text().splitlines()
    assert len(ledger) == 1
    assert '"attempt_id": "persist-1"' in ledger[0]


def test_outcome_routine_records_and_returns_effective_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(outcome_learner, "STORE_ROOT", tmp_path)

    text = asyncio.run(
        outcome_learner.run(
            outcome_learner.Config(
                attempt_id="routine-1",
                action="create",
                pool_address="pool-8",
                sleeve="core",
                executor_status="RUNNING",
                chain_position_found=False,
                wallet_delta_usd=0.0,
                confirmation_age_ticks=0,
            ),
            context=None,
        )
    )

    assert "PENDING_CONFIRMATION" in text
    assert "VERIFY_NEXT_TICK" in text
    assert "entry_haircut_bps=50" in text
    assert (tmp_path / "outcomes.jsonl").exists()


def test_repeated_range_failures_narrow_next_range_with_a_hard_floor():
    learner = OutcomeLearner()
    state = LearningState()
    for index in range(10):
        result = learner.observe(
            PositionAttempt(
                attempt_id=f"range-{index}",
                action="create",
                pool_address="pool-9",
                sleeve="core",
                executor_status="FAILED",
                chain_position_found=False,
                wallet_delta_usd=0.0,
                error_message="simulation failed: range exceeds maximum bin count",
                confirmation_age_ticks=1,
            ),
            state,
        )
        state = result.state

    assert result.outcome == "INVALID_RANGE"
    assert state.policy.range_width_scale == 0.6
    assert result.next_action == "REBUILD_RANGE_NEXT_DEEP_TICK"


def test_stop_loss_exit_is_attributed_and_cools_pool_before_next_entry():
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id="close-1",
            action="close",
            pool_address="pool-10",
            sleeve="runner",
            executor_status="COMPLETED",
            chain_position_found=False,
            wallet_delta_usd=18.0,
            exit_reason="hard-stop",
            pnl_pct=-6.1,
            vs_hodl_pct=-2.0,
            observed_tick=100,
        ),
        LearningState(),
    )

    assert result.outcome == "STRATEGY_STOP_LOSS"
    assert result.next_action == "COOLDOWN_AND_RESCAN"
    gate = result.state.pool_gates["pool-10"]
    assert gate.requires_reconciliation is False
    assert gate.release_after_tick == 130


def test_pool_gate_blocks_until_expiry_and_then_releases():
    learner = OutcomeLearner()
    stopped = learner.observe(
        PositionAttempt(
            attempt_id="close-2",
            action="close",
            pool_address="pool-11",
            sleeve="runner",
            executor_status="COMPLETED",
            chain_position_found=False,
            exit_reason="stop-loss",
            observed_tick=200,
        ),
        LearningState(),
    )

    blocked = learner.entry_permission("pool-11", 229, stopped.state)
    released = learner.entry_permission("pool-11", 230, blocked.state)

    assert blocked.allowed is False
    assert released.allowed is True
    assert "pool-11" not in released.state.pool_gates


def test_outcome_routine_checks_persistent_gate_before_an_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(outcome_learner, "STORE_ROOT", tmp_path)
    learner = OutcomeLearner()
    attempt = PositionAttempt(
        attempt_id="close-3",
        action="close",
        pool_address="pool-12",
        sleeve="runner",
        executor_status="COMPLETED",
        chain_position_found=False,
        exit_reason="hard-stop",
        observed_tick=300,
    )
    result = learner.observe(attempt, LearningState())
    FileLearningStore(tmp_path).record(attempt, result)

    blocked = asyncio.run(
        outcome_learner.run(
            outcome_learner.Config(
                mode="entry_check",
                attempt_id="check-1",
                action="create",
                pool_address="pool-12",
                sleeve="runner",
                observed_tick=329,
            ),
            context=None,
        )
    )

    assert "ENTRY_BLOCKED" in blocked


def test_entry_check_schema_does_not_require_record_only_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(outcome_learner, "STORE_ROOT", tmp_path)

    config = outcome_learner.Config(
        mode="entry_check",
        pool_address="pool-minimal-entry-check",
        observed_tick=1,
    )

    assert config.action == "create"
    assert config.sleeve == "satellite"


def test_replayed_terminal_observation_is_idempotent():
    learner = OutcomeLearner()
    attempt = PositionAttempt(
        attempt_id="idempotent-1",
        action="create",
        pool_address="pool-13",
        sleeve="satellite",
        executor_status="FAILED",
        chain_position_found=False,
        wallet_delta_usd=0.0,
        error_message="insufficient funds",
        confirmation_age_ticks=1,
    )

    first = learner.observe(attempt, LearningState())
    replay = learner.observe(attempt, first.state)

    assert first.state.outcome_counts["INSUFFICIENT_BALANCE"] == 1
    assert replay.outcome == "DUPLICATE_OBSERVATION"
    assert replay.state.outcome_counts["INSUFFICIENT_BALANCE"] == 1
    assert replay.state.policy.entry_haircut_bps == 50


@pytest.mark.parametrize(
    ("exit_reason", "outcome", "cooldown_ticks"),
    [
        ("volume-decay", "STRATEGY_VOLUME_DECAY_EXIT", 30),
        ("max-hold", "STRATEGY_MAX_HOLD_EXIT", 10),
        ("out-of-range", "STRATEGY_OUT_OF_RANGE_EXIT", 10),
    ],
)
def test_non_stop_exit_causes_remain_distinct(
    exit_reason, outcome, cooldown_ticks
):
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id=f"close-{exit_reason}",
            action="close",
            pool_address=f"pool-{exit_reason}",
            sleeve="satellite",
            executor_status="COMPLETED",
            chain_position_found=False,
            exit_reason=exit_reason,
            pnl_pct=-1.0,
            observed_tick=400,
        ),
        LearningState(),
    )

    assert result.outcome == outcome
    assert result.state.pool_gates[f"pool-{exit_reason}"].release_after_tick == (
        400 + cooldown_ticks
    )


def test_profitable_close_releases_pool_without_failure_cooldown():
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id="close-profit",
            action="close",
            pool_address="pool-profit",
            sleeve="core",
            executor_status="COMPLETED",
            chain_position_found=False,
            exit_reason="trailing-profit",
            pnl_pct=4.0,
            vs_hodl_pct=1.0,
            observed_tick=500,
        ),
        LearningState(),
    )

    assert result.outcome == "CONFIRMED_PROFIT_EXIT"
    assert result.next_action == "RESCAN_NEXT_DEEP_TICK"
    assert "pool-profit" not in result.state.pool_gates


def test_unexplained_underperformance_gets_short_diagnostic_cooldown():
    result = OutcomeLearner().observe(
        PositionAttempt(
            attempt_id="close-underperformance",
            action="close",
            pool_address="pool-underperformance",
            sleeve="satellite",
            executor_status="COMPLETED",
            chain_position_found=False,
            exit_reason="operator-rotation",
            pnl_pct=0.5,
            vs_hodl_pct=-2.0,
            observed_tick=600,
        ),
        LearningState(),
    )

    assert result.outcome == "STRATEGY_UNDERPERFORMANCE"
    assert result.state.pool_gates[
        "pool-underperformance"
    ].release_after_tick == 615
