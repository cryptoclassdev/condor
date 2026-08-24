import asyncio
from types import SimpleNamespace

from condor.agents import host_outcomes


def test_supervision_hook_is_bounded_and_receives_strategy_config():
    received = {}

    async def callback(**kwargs):
        received.update(kwargs)
        return ["stopped"]

    module = SimpleNamespace(supervise=callback)
    result = asyncio.run(
        host_outcomes.supervise(
            module,
            client="client",
            agent_id="agent_1",
            tick=9,
            config={"satellite": {"max_hold_min": 120}},
        )
    )

    assert result == ["stopped"]
    assert received["agent_id"] == "agent_1"
    assert received["tick"] == 9
    assert received["config"]["satellite"]["max_hold_min"] == 120


def test_before_prompt_enforces_safety_before_reconciliation_and_evidence():
    events = []

    async def supervise(**kwargs):
        events.append("supervise")
        return ["expired-executor"]

    async def reconcile(**kwargs):
        events.append("reconcile")

    async def prepare(**kwargs):
        events.append("prepare")
        return {"wallet_before_usd": 42.0}

    result = asyncio.run(
        host_outcomes.before_prompt(
            SimpleNamespace(
                supervise=supervise, reconcile=reconcile, prepare=prepare
            ),
            client="client",
            agent_id="agent_1",
            tick=10,
            config={},
        )
    )

    assert events == ["supervise", "reconcile", "prepare"]
    assert result == {
        "stopped_executor_ids": ["expired-executor"],
        "evidence": {"wallet_before_usd": 42.0},
    }
