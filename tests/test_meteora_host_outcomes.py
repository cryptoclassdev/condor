import asyncio
import json
from pathlib import Path

from agents.meteora_regime_lp import host_outcomes


class _Executors:
    async def get_executor(self, executor_id):
        return {
            "id": executor_id,
            "status": "RUNNING",
            "config": {"pool_address": "pool-authority-123"},
            "custom_info": {"position_address": "position-authority-456"},
        }

    async def search_executors(self, **kwargs):
        assert kwargs["status"] == "RUNNING"
        return {
            "data": [
                {
                    "id": "executor-123",
                    "status": "RUNNING",
                    "controller_id": "agent_1",
                    "config": {"pool_address": "pool-authority-123"},
                }
            ]
        }


class _Portfolio:
    async def get_state(self, refresh=True):
        assert refresh is True
        return {
            "main": {
                "solana": [
                    {"token": "SOL", "value": 50.0},
                    {"token": "USDC", "value": 25.0},
                ]
            }
        }


class _Gateway:
    async def get_positions_owned(self, **kwargs):
        assert kwargs["pool_address"] == "pool-authority-123"
        return {
            "data": [
                {
                    "pool_address": "pool-authority-123",
                    "position_address": "position-authority-456",
                }
            ]
        }


class _Client:
    executors = _Executors()
    portfolio = _Portfolio()
    gateway_clmm = _Gateway()


def _runtime_store(tmp_path, monkeypatch):
    root = tmp_path / "outcome_learning"
    monkeypatch.setattr(host_outcomes, "STORE_ROOT", root)
    monkeypatch.setattr(host_outcomes, "PENDING_PATH", root / "pending.json")
    monkeypatch.setattr(host_outcomes, "EQUITY_ROOT", tmp_path / "equity")
    monkeypatch.setattr(host_outcomes, "SLEEVES_PATH", root / "sleeves.json")
    return root


def test_prepare_records_wallet_plus_open_lp_equity(tmp_path, monkeypatch):
    _runtime_store(tmp_path, monkeypatch)

    asyncio.run(host_outcomes.prepare(client=_Client(), agent_id="agent_1", tick=4))

    path = tmp_path / "equity" / "agent_1.jsonl"
    event = json.loads(path.read_text().splitlines()[0])
    assert event["tick"] == 4
    assert event["wallet_usd"] == 75.0


def test_prepare_preserves_executor_evidence_when_wallet_read_fails(
    tmp_path, monkeypatch
):
    _runtime_store(tmp_path, monkeypatch)

    class BrokenPortfolio:
        async def get_state(self, **kwargs):
            raise RuntimeError("portfolio unavailable")

    class Client(_Client):
        portfolio = BrokenPortfolio()

    evidence = asyncio.run(
        host_outcomes.prepare(client=Client(), agent_id="agent_1", tick=5)
    )

    assert evidence["executor_ids_before"] == ["executor-123"]
    assert evidence["wallet_before_usd"] is None
    assert evidence["equity_before_usd"] is None


def test_host_capture_waits_for_a_later_authority_read(tmp_path, monkeypatch):
    root = _runtime_store(tmp_path, monkeypatch)
    calls = [
        {
            "id": "wallet",
            "name": "mcp__condor__manage_routines",
            "status": "completed",
            "input": {"action": "run", "name": "wallet_audit"},
            "output": "wallet_audit: total wallet $100.00 = $100 liquid quote",
        },
        {
            "id": "create",
            "name": "mcp__mcp-hummingbot__manage_executors",
            "status": "completed",
            "input": {
                "action": "create",
                "executor_config": {"pool_address": "pool-authority-123"},
            },
            "output": {
                "executor_id": "executor-123",
                "position_address": "position-authority-456",
            },
        },
    ]

    asyncio.run(
        host_outcomes.capture(
            client=_Client(), agent_id="agent_1", tick=10, tool_calls=calls
        )
    )

    assert not (root / "outcomes.jsonl").exists()
    pending = json.loads((root / "pending.json").read_text())["attempts"]
    assert len(pending) == 1
    assert pending[0]["wallet_before_usd"] == 100.0
    assert host_outcomes.pending_pool("pool-authority-123") is True

    asyncio.run(host_outcomes.reconcile(client=_Client(), agent_id="agent_1", tick=11))

    event = json.loads((root / "outcomes.jsonl").read_text().splitlines()[0])
    assert event["outcome"] == "CONFIRMED_SUCCESS"
    assert event["attempt"]["confirmation_age_ticks"] == 1
    assert host_outcomes.pending_pool("pool-authority-123") is False


def test_host_capture_persists_executor_sleeve_for_timeout_supervision(
    tmp_path, monkeypatch
):
    _runtime_store(tmp_path, monkeypatch)
    asyncio.run(
        host_outcomes.capture(
            client=_Client(),
            agent_id="agent_1",
            tick=8,
            tool_calls=[
                {
                    "id": "runner-create",
                    "name": "manage_executors",
                    "status": "completed",
                    "input": {
                        "action": "create",
                        "sleeve": "runner",
                        "executor_config": {
                            "pool_address": "pool-authority-123"
                        },
                    },
                    "output": {
                        "executor_id": "executor-123",
                        "position_address": "position-authority-456",
                    },
                }
            ],
            evidence_before={"wallet_before_usd": 100.0},
        )
    )

    assert host_outcomes.sleeve_for_executor("executor-123") == "runner"


def test_host_capture_preserves_actual_side_and_entry_regime(tmp_path, monkeypatch):
    root = _runtime_store(tmp_path, monkeypatch)
    calls = [
        {
            "id": "entry-check",
            "name": "manage_routines",
            "status": "completed",
            "input": {
                "action": "run",
                "name": "outcome_learner",
                "config": {
                    "mode": "entry_check",
                    "pool_address": "pool-authority-123",
                    "sleeve": "core",
                    "regime": "TRENDING_UP",
                },
            },
            "output": "ENTRY_ALLOWED; recommended_side=2",
        },
        {
            "id": "create",
            "name": "manage_executors",
            "status": "completed",
            "input": {
                "action": "create",
                "executor_config": {
                    "pool_address": "pool-authority-123",
                    "side": 2,
                },
            },
            "output": {
                "executor_id": "executor-123",
                "position_address": "position-authority-456",
            },
        },
    ]

    asyncio.run(
        host_outcomes.capture(
            client=_Client(),
            agent_id="agent_1",
            tick=15,
            tool_calls=calls,
            evidence_before={"wallet_before_usd": 100.0},
        )
    )

    pending = json.loads((root / "pending.json").read_text())["attempts"]
    assert pending[0]["entry_side"] == 2
    assert pending[0]["entry_regime"] == "TRENDING_UP"

    asyncio.run(host_outcomes.reconcile(client=_Client(), agent_id="agent_1", tick=16))
    state = json.loads((root / "state.json").read_text())["state"]
    assert state["active_side_contexts"]["pool-authority-123"] == {
        "entry_side": 2,
        "entry_regime": "TRENDING_UP",
    }


def test_host_capture_recovers_regime_from_regime_engine_when_entry_check_omits_it(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)
    calls = [
        {
            "id": "regime",
            "name": "manage_routines",
            "status": "completed",
            "input": {
                "action": "run",
                "name": "regime_engine",
                "config": {"pool_addresses": ["pool-authority-123"]},
            },
            "output": {
                "text": "Classified 1 pool(s): TRENDINGx1",
                "table_data": [
                    {
                        "Pool": "pool-authority-123",
                        "Regime": "TRENDING",
                        "Trend": "1.53 up",
                    }
                ],
            },
        },
        {
            "id": "entry-check",
            "name": "manage_routines",
            "status": "completed",
            "input": {
                "action": "run",
                "name": "outcome_learner",
                "config": {
                    "mode": "entry_check",
                    "pool_address": "pool-authority-123",
                    "sleeve": "core",
                },
            },
            "output": "ENTRY_ALLOWED; recommended_side=1",
        },
        {
            "id": "create",
            "name": "manage_executors",
            "status": "completed",
            "input": {
                "action": "create",
                "executor_config": {
                    "pool_address": "pool-authority-123",
                    "side": 1,
                },
            },
            "output": {
                "executor_id": "executor-123",
                "position_address": "position-authority-456",
            },
        },
    ]

    asyncio.run(
        host_outcomes.capture(
            client=_Client(),
            agent_id="agent_1",
            tick=20,
            tool_calls=calls,
            evidence_before={"wallet_before_usd": 100.0},
        )
    )

    pending = json.loads((root / "pending.json").read_text())["attempts"]
    assert pending[0]["entry_side"] == 1
    assert pending[0]["entry_regime"] == "TRENDING_UP"


def test_timeout_supervisor_stops_only_owned_executor_with_hard_exit(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)

    class Executors:
        stopped = []

        async def search_executors(self, **kwargs):
            assert kwargs["status"] == "RUNNING"
            return {
                "data": [
                    {
                        "executor_id": "expired-owned",
                        "executor_type": "lp_executor",
                        "controller_id": "meteora_regime_lp.operator_33",
                        "created_at": "2026-08-20T14:00:00+00:00",
                        "trading_pair": "token-mint-So11111111111111111111111111111111111111112",
                        "net_pnl_pct": 0.01,
                        "config": {"pool_address": "pool-expired"},
                        "custom_info": {
                            "position_address": "position-expired-authority-123456789",
                            "state": "IN_RANGE",
                        },
                    },
                    {
                        "executor_id": "foreign",
                        "executor_type": "lp_executor",
                        "controller_id": "some_other_agent_7",
                        "created_at": "2026-08-20T14:00:00+00:00",
                        "trading_pair": "token-mint-So11111111111111111111111111111111111111112",
                        "config": {"pool_address": "pool-foreign"},
                    },
                ]
            }

        async def stop_executor(self, *, executor_id, keep_position):
            self.stopped.append((executor_id, keep_position))
            return {"success": True}

    class Client(_Client):
        executors = Executors()

    result = asyncio.run(
        host_outcomes.supervise(
            client=Client(),
            agent_id="meteora_regime_lp.operator_33",
            tick=50,
            config={
                "satellite": {"max_hold_min": 120},
                "runner": {"max_hold_min": 90, "stop_loss_pct": 6},
                "quick_in_out": {"max_hold_min": 15, "stop_loss_pct": 3},
                "stop_loss_pct": 8,
                "out_of_range_max_sec": 1800,
                "out_of_range_buffer_pct": 0.5,
                "rebalance_cooldown_sec": 900,
            },
            now="2026-08-20T17:00:00+00:00",
        )
    )

    assert result == ["expired-owned"]
    assert Client.executors.stopped == [("expired-owned", False)]
    pending = json.loads((root / "pending.json").read_text())["attempts"]
    assert pending[0]["action"] == "close"
    assert pending[0]["exit_reason"] == "max-hold"
    assert pending[0]["wallet_before_usd"] is None


def test_timeout_supervisor_uses_persisted_runner_deadline(tmp_path, monkeypatch):
    _runtime_store(tmp_path, monkeypatch)
    host_outcomes._remember_sleeve("runner-owned", "runner")

    class Executors:
        stopped = []

        async def search_executors(self, **kwargs):
            return {
                "data": [
                    {
                        "executor_id": "runner-owned",
                        "executor_type": "lp_executor",
                        "controller_id": "meteora_regime_lp.operator_33",
                        "created_at": "2026-08-20T15:20:00+00:00",
                        "trading_pair": "TokenMint111111111111111111111111111111-So11111111111111111111111111111111111111112",
                        "config": {"pool_address": "runner-pool"},
                        "custom_info": {
                            "position_address": "runner-position-authority-123456789",
                            "state": "IN_RANGE",
                        },
                    }
                ]
            }

        async def stop_executor(self, *, executor_id, keep_position):
            self.stopped.append((executor_id, keep_position))

    class Client(_Client):
        executors = Executors()

    stopped = asyncio.run(
        host_outcomes.supervise(
            client=Client(),
            agent_id="meteora_regime_lp.operator_33",
            tick=9,
            config={
                "satellite": {"max_hold_min": 120},
                "runner": {"max_hold_min": 90, "stop_loss_pct": 6},
                "stop_loss_pct": 8,
            },
            now="2026-08-20T17:00:00+00:00",
        )
    )

    assert stopped == ["runner-owned"]
    assert Client.executors.stopped == [("runner-owned", False)]


def test_urgent_supervision_does_not_wait_for_wallet_read(tmp_path, monkeypatch):
    root = _runtime_store(tmp_path, monkeypatch)

    class Executors:
        stopped = []

        async def search_executors(self, **kwargs):
            return {
                "data": [
                    {
                        "executor_id": "urgent-owned",
                        "executor_type": "lp_executor",
                        "controller_id": "meteora_regime_lp.operator_33",
                        "created_at": "2026-08-20T14:00:00+00:00",
                        "trading_pair": "TokenMint111111111111111111111111111111-So11111111111111111111111111111111111111112",
                        "config": {"pool_address": "urgent-pool"},
                        "custom_info": {"state": "IN_RANGE"},
                    }
                ]
            }

        async def stop_executor(self, *, executor_id, keep_position):
            self.stopped.append((executor_id, keep_position))

    class BrokenPortfolio:
        async def get_state(self, **kwargs):
            raise AssertionError("urgent stop cannot depend on portfolio")

    class Client:
        executors = Executors()
        portfolio = BrokenPortfolio()

    stopped = asyncio.run(
        host_outcomes.supervise(
            client=Client(),
            agent_id="meteora_regime_lp.operator_33",
            tick=12,
            config={"satellite": {"max_hold_min": 120}},
            now="2026-08-20T17:00:00+00:00",
        )
    )

    assert stopped == ["urgent-owned"]
    pending = json.loads((root / "pending.json").read_text())["attempts"]
    assert pending[0]["wallet_before_usd"] is None


def test_host_capture_uses_pre_tick_baseline_when_acp_output_is_sparse(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)

    asyncio.run(
        host_outcomes.capture(
            client=_Client(),
            agent_id="agent_1",
            tick=12,
            tool_calls=[
                {
                    "id": "create",
                    "name": "manage_executors",
                    "status": "completed",
                    "input": {
                        "action": "create",
                        "executor_config": {
                            "pool_address": "pool-authority-123"
                        },
                    },
                }
            ],
            evidence_before={
                "wallet_before_usd": 100.0,
                "executor_ids_before": [],
            },
        )
    )

    pending = json.loads((root / "pending.json").read_text())["attempts"]
    assert len(pending) == 1
    assert pending[0]["executor_id"] == "executor-123"
    assert pending[0]["wallet_before_usd"] == 100.0


def test_permission_aborts_are_not_recorded_as_execution_attempts(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)

    asyncio.run(
        host_outcomes.capture(
            client=_Client(),
            agent_id="agent_1",
            tick=20,
            tool_calls=[
                {
                    "id": "blocked",
                    "name": "manage_executors",
                    "status": "failed",
                    "input": {
                        "action": "create",
                        "executor_config": {"pool_address": "pool-blocked"},
                    },
                    "output": "Tool use aborted by permission callback",
                }
            ],
        )
    )

    payload = json.loads((root / "pending.json").read_text())
    assert payload["attempts"] == []


def test_unknown_position_identity_stays_pending(tmp_path, monkeypatch):
    root = _runtime_store(tmp_path, monkeypatch)
    host_outcomes._save_pending(
        [
            host_outcomes.PendingAttempt(
                attempt_id="unknown",
                action="create",
                pool_address="pool-authority-123",
                sleeve="satellite",
                executor_id="",
                executor_status="RUNNING",
                wallet_before_usd=100.0,
                position_address="",
                error_message="",
                observed_tick=30,
            )
        ]
    )

    asyncio.run(host_outcomes.reconcile(client=_Client(), agent_id="agent_1", tick=31))

    assert not (root / "outcomes.jsonl").exists()
    assert json.loads((root / "pending.json").read_text())["attempts"]


def test_confirmed_close_does_not_require_historical_wallet_baseline(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)
    host_outcomes._save_pending(
        [
            host_outcomes.PendingAttempt(
                attempt_id="old-close-without-wallet",
                action="close",
                pool_address="pool-authority-123",
                sleeve="satellite",
                executor_id="executor-123",
                executor_status="SUCCESS",
                wallet_before_usd=None,
                position_address="position-authority-456",
                error_message="",
                observed_tick=1,
            )
        ]
    )

    class ClosedExecutors(_Executors):
        async def get_executor(self, executor_id):
            detail = await super().get_executor(executor_id)
            detail["status"] = "TERMINATED"
            return detail

    class ClosedGateway:
        async def get_positions_owned(self, **kwargs):
            return {"data": []}

    class ClosedClient(_Client):
        executors = ClosedExecutors()
        gateway_clmm = ClosedGateway()

    asyncio.run(host_outcomes.reconcile(client=ClosedClient(), agent_id="agent_1", tick=2))

    assert json.loads((root / "pending.json").read_text())["attempts"] == []
    event = json.loads((root / "outcomes.jsonl").read_text().splitlines()[0])
    assert event["outcome"] == "CONFIRMED_CLOSE"


def test_terminal_close_is_confirmed_when_same_pool_has_newer_owned_successor(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)
    host_outcomes._save_pending(
        [
            host_outcomes.PendingAttempt(
                attempt_id="host:meteora_regime_lp.operator_35:64:old-close",
                action="close",
                pool_address="pool-authority-123",
                sleeve="satellite",
                executor_id="old-executor",
                executor_status="SUCCESS",
                wallet_before_usd=None,
                position_address="",
                error_message="",
                observed_tick=64,
            )
        ]
    )

    class SuccessorExecutors:
        async def get_executor(self, executor_id):
            assert executor_id == "old-executor"
            return {
                "id": executor_id,
                "status": "TERMINATED",
                "close_timestamp": "2026-08-23T16:10:00+00:00",
                "config": {"pool_address": "pool-authority-123"},
                "custom_info": {},
            }

        async def search_executors(self, **kwargs):
            assert kwargs["status"] == "RUNNING"
            return {
                "data": [
                    {
                        "id": "new-executor",
                        "status": "RUNNING",
                        "controller_id": "meteora_regime_lp.operator_36",
                        "created_at": "2026-08-23T16:20:00+00:00",
                        "config": {"pool_address": "pool-authority-123"},
                        "custom_info": {"position_address": "new-position"},
                    }
                ]
            }

    class SuccessorGateway:
        async def get_positions_owned(self, **kwargs):
            return {
                "data": [
                    {
                        "pool_address": "pool-authority-123",
                        "position_address": "new-position",
                    }
                ]
            }

    class Client:
        executors = SuccessorExecutors()
        gateway_clmm = SuccessorGateway()

    asyncio.run(
        host_outcomes.reconcile(
            client=Client(), agent_id="meteora_regime_lp.operator_36", tick=1
        )
    )

    assert json.loads((root / "pending.json").read_text())["attempts"] == []
    event = json.loads((root / "outcomes.jsonl").read_text().splitlines()[0])
    assert event["outcome"] == "CONFIRMED_CLOSE"


def test_close_reconciliation_without_wallet_baseline_skips_portfolio_read(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)
    host_outcomes._save_pending(
        [
            host_outcomes.PendingAttempt(
                attempt_id="old-close-no-wallet-call",
                action="close",
                pool_address="pool-authority-123",
                sleeve="satellite",
                executor_id="executor-123",
                executor_status="SUCCESS",
                wallet_before_usd=None,
                position_address="position-authority-456",
                error_message="",
                observed_tick=1,
            )
        ]
    )

    class NoPortfolio:
        async def get_state(self, **kwargs):
            raise AssertionError("historical close must not read the wallet")

    class ClosedExecutors(_Executors):
        async def get_executor(self, executor_id):
            detail = await super().get_executor(executor_id)
            detail["status"] = "TERMINATED"
            return detail

    class EmptyGateway:
        async def get_positions_owned(self, **kwargs):
            return {"data": []}

    class Client:
        executors = ClosedExecutors()
        portfolio = NoPortfolio()
        gateway_clmm = EmptyGateway()

    asyncio.run(host_outcomes.reconcile(client=Client(), agent_id="agent_1", tick=2))

    assert json.loads((root / "pending.json").read_text())["attempts"] == []


def test_closed_pool_is_absent_even_when_wallet_wide_read_has_another_pool():
    pending = host_outcomes.PendingAttempt(
        attempt_id="closed",
        action="close",
        pool_address="satellite-pool",
        sleeve="satellite",
        executor_id="executor",
        executor_status="SUCCESS",
        wallet_before_usd=50.0,
        position_address="",
        error_message="",
        observed_tick=1,
    )

    found = host_outcomes._chain_found(
        pending,
        {"status": "TERMINATED", "config": {"pool_address": "satellite-pool"}},
        [
            {
                "pool_address": "core-pool",
                "position_address": "core-position-authority-123456789",
            }
        ],
    )

    assert found is False


def test_reconcile_bounds_each_tick_and_rotates_deferred_attempts(tmp_path, monkeypatch):
    root = _runtime_store(tmp_path, monkeypatch)
    attempts = [
        host_outcomes.PendingAttempt(
            attempt_id=f"close-{index}",
            action="close",
            pool_address=f"pool-{index}",
            sleeve="core",
            executor_id=f"executor-{index}",
            executor_status="SUCCESS",
            wallet_before_usd=50.0,
            position_address=f"position-{index}",
            error_message="",
            observed_tick=1,
        )
        for index in range(3)
    ]
    host_outcomes._save_pending(attempts)

    class Executors:
        calls = []

        async def get_executor(self, executor_id):
            self.calls.append(executor_id)
            index = executor_id.rsplit("-", 1)[-1]
            return {
                "id": executor_id,
                "status": "TERMINATED",
                "config": {"pool_address": f"pool-{index}"},
                "custom_info": {"position_address": f"position-{index}"},
            }

    class Gateway:
        async def get_positions_owned(self, **kwargs):
            return {"data": []}

    class Client:
        executors = Executors()
        portfolio = _Portfolio()
        gateway_clmm = Gateway()

    asyncio.run(host_outcomes.reconcile(client=Client(), agent_id="agent_1", tick=2))

    assert Client.executors.calls == ["executor-0", "executor-1"]
    pending = json.loads((root / "pending.json").read_text())["attempts"]
    assert [item["attempt_id"] for item in pending] == ["close-2"]


def test_reconcile_treats_previous_session_attempt_as_due_after_tick_reset(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)
    host_outcomes._save_pending(
        [
            host_outcomes.PendingAttempt(
                attempt_id="host:meteora_regime_lp.operator_30:201:close",
                action="close",
                pool_address="pool-authority-123",
                sleeve="core",
                executor_id="executor-123",
                executor_status="SUCCESS",
                wallet_before_usd=50.0,
                position_address="position-authority-456",
                error_message="",
                observed_tick=201,
            )
        ]
    )

    class ClosedGateway:
        async def get_positions_owned(self, **kwargs):
            return {"data": []}

    class ClosedExecutors(_Executors):
        async def get_executor(self, executor_id):
            detail = await super().get_executor(executor_id)
            detail["status"] = "TERMINATED"
            return detail

    class ClosedClient(_Client):
        executors = ClosedExecutors()
        gateway_clmm = ClosedGateway()

    asyncio.run(
        host_outcomes.reconcile(
            client=ClosedClient(),
            agent_id="meteora_regime_lp.operator_31",
            tick=1,
        )
    )

    assert json.loads((root / "pending.json").read_text())["attempts"] == []


def test_previous_session_terminal_create_uses_new_session_confirmation_age(
    tmp_path, monkeypatch
):
    root = _runtime_store(tmp_path, monkeypatch)
    host_outcomes._save_pending(
        [
            host_outcomes.PendingAttempt(
                attempt_id="host:meteora_regime_lp.operator_33:223:create",
                action="create",
                pool_address="pool-authority-123",
                sleeve="core",
                executor_id="executor-123",
                executor_status="RUNNING",
                wallet_before_usd=None,
                position_address="position-authority-456",
                error_message="",
                observed_tick=223,
            )
        ]
    )

    class TerminalExecutors(_Executors):
        async def get_executor(self, executor_id):
            detail = await super().get_executor(executor_id)
            detail["status"] = "TERMINATED"
            return detail

    class EmptyGateway:
        async def get_positions_owned(self, **kwargs):
            return {"data": []}

    class Client(_Client):
        executors = TerminalExecutors()
        gateway_clmm = EmptyGateway()

    asyncio.run(
        host_outcomes.reconcile(
            client=Client(), agent_id="meteora_regime_lp.operator_34", tick=2
        )
    )

    assert json.loads((root / "pending.json").read_text())["attempts"] == []
    event = json.loads((root / "outcomes.jsonl").read_text().splitlines()[0])
    assert event["outcome"] == "HISTORICAL_CREATE_TERMINATED"
