import json

from scripts import meteora_soak_audit as soak


def _session(tmp_path, *, tick=8, updated_at=1000.0, decision=None):
    session = tmp_path / "session_8"
    snapshots = session / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    (session / "status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 123,
                "tick": tick,
                "updated_at": updated_at,
            }
        )
    )
    (snapshots / f"snapshot_{tick}.md").write_text(
        "## Executor State\n"
        "Active Executors (2) [agent: test]:\n"
        "Realized: $+0.00 | Unrealized: $+0.02 | Total PnL: $+0.02\n\n"
        "## Risk State\n"
        "- Position Size: $100.23 / $500.00 limit\n"
        "- Status: ACTIVE\n\n"
        "## Agent Response\nhealthy\n"
    )
    decision = decision or (
        "Reconcile clean (0 orphan/0 ghost/48 phantom). Wallet has no stranded "
        "inventory. Core MONITOR."
    )
    (session / "journal.md").write_text(
        "## Decisions\n"
        f"- **#{tick}** (20:40) t{tick}: {decision}\n\n"
        "## Ticks\n"
    )
    return session


def _statuses(result):
    return {check["name"]: check["status"] for check in result["checks"]}


def test_healthy_session_creates_baseline_soak_sample(tmp_path):
    session = _session(tmp_path)
    log = session / "soak.jsonl"

    result = soak.audit(
        session, now=1050, log_path=log, pid_probe=lambda _pid: True
    )

    statuses = _statuses(result)
    assert result["overall"] == "WARN"
    assert statuses["session_state"] == "PASS"
    assert statuses["process"] == "PASS"
    assert statuses["freshness"] == "PASS"
    assert statuses["reconciliation"] == "PASS"
    assert statuses["wallet_inventory"] == "PASS"
    assert statuses["risk_engine"] == "PASS"
    assert statuses["tick_progress"] == "WARN"
    assert result["open_executors"] == 2
    assert result["pnl_usd"] == 0.02
    assert result["exposure_usd"] == 100.23
    assert len(log.read_text().splitlines()) == 1


def test_second_sample_proves_tick_progress(tmp_path):
    session = _session(tmp_path, tick=8, updated_at=1000)
    log = session / "soak.jsonl"
    soak.audit(session, now=1010, log_path=log, pid_probe=lambda _pid: True)
    session = _session(tmp_path, tick=9, updated_at=1100)

    result = soak.audit(
        session, now=1110, log_path=log, pid_probe=lambda _pid: True
    )

    assert _statuses(result)["tick_progress"] == "PASS"
    assert result["overall"] == "PASS"


def test_stale_dead_or_unreconciled_session_fails(tmp_path):
    session = _session(
        tmp_path,
        updated_at=100,
        decision="Orphan guard could not read the authority endpoint.",
    )

    result = soak.audit(session, now=1000, pid_probe=lambda _pid: False)

    statuses = _statuses(result)
    assert result["overall"] == "FAIL"
    assert statuses["process"] == "FAIL"
    assert statuses["freshness"] == "FAIL"
    assert statuses["reconciliation"] == "FAIL"


def test_scanner_degradation_and_old_unverified_close_are_warnings(tmp_path):
    session = _session(
        tmp_path,
        decision=(
            "Reconcile clean (0 orphan/0 ghost/48 phantom; 1 prior close still "
            "zero-proceeds/unverified). no stranded inventory. runner_scanner SOURCE "
            "DEGRADED, sleeve PAUSED."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    statuses = _statuses(result)
    assert result["overall"] == "WARN"
    assert statuses["runner_scanner"] == "WARN"
    assert statuses["historical_close_evidence"] == "WARN"
