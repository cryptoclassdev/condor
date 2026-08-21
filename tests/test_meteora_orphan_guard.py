import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents.meteora_regime_lp.routines import orphan_guard
from condor.reports import ReportBuilder


POOL = "P" * 44
UNREADABLE_POOL = "U" * 44
POSITION = "X" * 44
UNREADABLE_POSITION = "Y" * 44


@pytest.fixture(autouse=True)
def _keep_routine_tests_out_of_the_live_report_index(monkeypatch):
    async def no_save(_builder):
        return None

    monkeypatch.setattr(ReportBuilder, "save", no_save)


class _Executors:
    def __init__(self):
        self.stop_calls: list[str] = []

    async def search_executors(self, *, status, limit, cursor=None):
        if status == "RUNNING":
            return {"data": [{"id": "executor-without-pool"}]}
        return {"data": []}

    async def stop_executor(self, executor_id):
        self.stop_calls.append(executor_id)


class _GatewayClmm:
    async def search_positions(self, **kwargs):
        return {
            "data": [
                {
                    "position_address": POSITION,
                    "pool_address": POOL,
                }
            ]
        }

    async def get_positions_owned(self, **kwargs):
        return {"data": []}


def test_unresolved_executor_is_never_stopped_as_a_ghost(monkeypatch):
    client = SimpleNamespace(
        executors=_Executors(),
        gateway_clmm=_GatewayClmm(),
    )

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(orphan_guard, "get_client", fake_get_client)

    result = asyncio.run(
        orphan_guard.run(
            orphan_guard.Config(
                clear_ghosts=True,
                suspect_close_lookback_min=0,
            ),
            SimpleNamespace(_chat_id=1),
        )
    )
    text = result.text if hasattr(result, "text") else str(result)

    assert "0 ghost(s)" in text
    assert "NOT classified" in text
    assert client.executors.stop_calls == []


class _PartiallyReadableGateway:
    async def search_positions(self, **kwargs):
        return {
            "data": [
                {"position_address": POSITION, "pool_address": POOL},
                {
                    "position_address": UNREADABLE_POSITION,
                    "pool_address": UNREADABLE_POOL,
                },
            ]
        }

    async def get_positions_owned(self, *, pool_address, **kwargs):
        if pool_address == UNREADABLE_POOL:
            raise RuntimeError("authority unavailable")
        return {"data": []}


def test_unreadable_pool_cache_rows_are_not_called_phantoms(monkeypatch):
    client = SimpleNamespace(
        executors=_Executors(),
        gateway_clmm=_PartiallyReadableGateway(),
    )
    client.executors.search_executors = _no_running_executors

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(orphan_guard, "get_client", fake_get_client)

    result = asyncio.run(
        orphan_guard.run(
            orphan_guard.Config(
                suspect_close_lookback_min=0,
                wallet_wide_authority=False,
            ),
            SimpleNamespace(_chat_id=1),
        )
    )
    text = result.text if hasattr(result, "text") else str(result)

    assert "1 phantom cache record(s)" in text
    assert "anything there is unclassified" in text


def test_wallet_wide_authority_is_read_once_for_many_candidate_pools(monkeypatch):
    class CountingGateway(_PartiallyReadableGateway):
        def __init__(self):
            self.owned_calls = 0

        async def get_positions_owned(self, **kwargs):
            self.owned_calls += 1
            return {"data": []}

    gateway = CountingGateway()
    client = SimpleNamespace(executors=_Executors(), gateway_clmm=gateway)
    client.executors.search_executors = _no_running_executors

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(orphan_guard, "get_client", fake_get_client)

    asyncio.run(
        orphan_guard.run(
            orphan_guard.Config(suspect_close_lookback_min=0),
            SimpleNamespace(_chat_id=1),
        )
    )

    assert gateway.owned_calls == 1


async def _no_running_executors(*, status, limit, cursor=None):
    return {"data": []}


def test_fresh_executor_is_not_stopped_during_position_index_lag(monkeypatch):
    class FreshExecutors(_Executors):
        async def search_executors(self, *, status, limit, cursor=None):
            if status == "RUNNING":
                return {
                    "data": [
                        {
                            "id": "fresh-executor",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "config": {
                                "pool_address": POOL,
                                "position_address": POSITION,
                            },
                        }
                    ]
                }
            return {"data": []}

    executors = FreshExecutors()
    client = SimpleNamespace(executors=executors, gateway_clmm=_GatewayClmm())

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(orphan_guard, "get_client", fake_get_client)

    result = asyncio.run(
        orphan_guard.run(
            orphan_guard.Config(
                clear_ghosts=True,
                min_ghost_age_min=10,
                suspect_close_lookback_min=0,
            ),
            SimpleNamespace(_chat_id=1),
        )
    )
    text = result.text if hasattr(result, "text") else str(result)

    assert "0 ghost(s)" in text
    assert "indexing grace" in text
    assert executors.stop_calls == []
