import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agents.meteora_regime_lp.routines import runner_scanner
from agents.meteora_regime_lp.routines.runner_scanner import (
    _age_hours,
    _fetch_gecko_feeds,
    _gecko_get,
    _retry_delay,
    source_coverage_prefix,
    summarize_no_candidates,
)


class _FakeResponse:
    def __init__(self, status, *, headers=None, payload=None):
        self.status = status
        self.headers = headers or {}
        self._payload = payload or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_pool_age_is_parsed_from_geckoterminal_timestamp():
    created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    assert _age_hours(created_at) == pytest.approx(2.0, abs=0.01)


def test_network_wide_other_venues_are_reach_not_a_binding_gate():
    summary = summarize_no_candidates(
        raw_count=320,
        stats={
            "not_meteora": 182,
            "age_unknown": 2,
            "age_young": 4,
            "age_old": 70,
            "vol": 50,
            "accel": 8,
            "tvl": 4,
            "not_sol": 0,
            "excluded": 0,
        },
    )

    assert "138 were Meteora" in summary
    assert "binding gate is **ageOld** (70/138" in summary
    assert "182 were other venues — structural" in summary
    assert "binding gate is **not_meteora**" not in summary


def test_retry_delay_honors_retry_after_and_exponential_floor():
    assert _retry_delay({"Retry-After": "12"}, attempt=0) == 12
    assert _retry_delay({}, attempt=0) == 5
    assert _retry_delay({}, attempt=2) == 20


def test_zero_retry_after_cannot_disable_backoff():
    assert _retry_delay({"Retry-After": "0"}, attempt=0) == 5


def test_gecko_get_retries_429_before_returning_data():
    sleeps = []
    session = _FakeSession(
        [
            _FakeResponse(429, headers={"Retry-After": "7"}),
            _FakeResponse(200, payload={"data": [{"id": "pool"}]}),
        ]
    )

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    result = asyncio.run(
        _gecko_get(session, "networks/solana/new_pools", sleep=fake_sleep)
    )

    assert result == {"data": [{"id": "pool"}]}
    assert session.calls == 2
    assert sleeps == [7]


def test_partial_source_coverage_is_reported_as_degraded():
    assert source_coverage_prefix(successful=12, total=17) == (
        "SOURCE DEGRADED: 12/17 GeckoTerminal feeds succeeded; treat this scan as "
        "partial and do not relax gates from it. "
    )
    assert source_coverage_prefix(successful=17, total=17) == ""


def test_feed_batch_opens_circuit_after_two_exhausted_rate_limits():
    session = _FakeSession([_FakeResponse(429)] * 9)
    requests = [(f"feed/{page}", None) for page in range(3)]

    async def no_wait(_seconds):
        return None

    raw, successful, circuit_open = asyncio.run(
        _fetch_gecko_feeds(
            session,
            requests,
            sleep=no_wait,
            request_interval=0,
        )
    )

    assert raw == []
    assert successful == 0
    assert circuit_open is True
    assert session.calls == 6


def test_runner_scan_has_a_hard_wall_clock_budget(monkeypatch):
    async def fake_get_client(*_args, **_kwargs):
        return None

    async def never_finishes(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner_scanner, "get_client", fake_get_client)
    monkeypatch.setattr(runner_scanner, "_fetch_gecko_feeds", never_finishes)

    async def exercise():
        return await asyncio.wait_for(
            runner_scanner.run(
                runner_scanner.Config(
                    scan_timeout_sec=0.01, prefer_meteora_api=False
                ),
                type("Context", (), {"_chat_id": 1})(),
            ),
            timeout=0.1,
        )

    result = asyncio.run(exercise())

    assert "SOURCE TIMEOUT" in str(result)
    assert "Runner sleeve must PAUSE" in str(result)


def test_rate_limit_circuit_cools_down_future_scans(monkeypatch):
    async def fake_get_client(*_args, **_kwargs):
        return None

    calls = 0

    async def rate_limited(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [], 0, True

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    context = type("Context", (), {"_chat_id": 1, "user_data": {}})()
    monkeypatch.setattr(runner_scanner, "get_client", fake_get_client)
    monkeypatch.setattr(runner_scanner.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(runner_scanner, "_fetch_gecko_feeds", rate_limited)

    first = asyncio.run(
        runner_scanner.run(
            runner_scanner.Config(
                scan_timeout_sec=1,
                rate_limit_cooldown_sec=600,
                prefer_meteora_api=False,
            ),
            context,
        )
    )
    second = asyncio.run(
        runner_scanner.run(
            runner_scanner.Config(
                scan_timeout_sec=1,
                rate_limit_cooldown_sec=600,
                prefer_meteora_api=False,
            ),
            context,
        )
    )

    assert "SOURCE UNAVAILABLE" in str(first)
    assert "RATE-LIMIT COOLDOWN" in str(second)
    assert calls == 1
