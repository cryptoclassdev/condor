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
    return root


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
