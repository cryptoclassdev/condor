import json

from scripts import meteora_soak_audit as soak


def _session(
    tmp_path, *, tick=8, updated_at=1000.0, decision=None, agent_response="healthy"
):
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
        f"## Agent Response\n{agent_response}\n"
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


def test_compact_reconciliation_and_active_lp_dust_are_classified_correctly(tmp_path):
    session = _session(
        tmp_path,
        decision=(
            "reconcile clean (0 orphan/ghost, 48 phantom noise ignored). "
            "Wallet: $31.16 liquid SOL + $0.08 stranded CATE (dust). "
            "Cleanup WAIT — CATE LP still RUNNING."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    statuses = _statuses(result)
    assert statuses["reconciliation"] == "PASS"
    assert statuses["wallet_inventory"] == "WARN"


def test_zero_dollar_stranded_wording_is_clean_inventory(tmp_path):
    session = _session(
        tmp_path,
        decision=(
            "Reconcile clean (0 orphan/0 ghost). Wallet $32.84 liquid, "
            "$0 stranded, cleanup CLEAN."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    assert _statuses(result)["wallet_inventory"] == "PASS"


def test_current_snapshot_response_supplies_reconciliation_and_inventory_evidence(
    tmp_path,
):
    session = _session(
        tmp_path,
        decision="Opened a verified core position and recorded its three outcomes.",
        agent_response=(
            "Reconciliation clean: 0 orphans/ghosts, no stranded inventory, "
            "both prior LP slots healthy."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    statuses = _statuses(result)
    assert statuses["reconciliation"] == "PASS"
    assert statuses["wallet_inventory"] == "PASS"


def test_orphan_guard_clean_wording_is_reconciliation_evidence(tmp_path):
    session = _session(
        tmp_path,
        decision="Monitor only; all slots are full.",
        agent_response=(
            "orphan_guard: clean — 0 orphans, 0 ghosts, only phantom-cache noise. "
            "Wallet has no stranded inventory."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    assert _statuses(result)["reconciliation"] == "PASS"


def test_post_outage_orphan_guard_summary_is_reconciliation_evidence(tmp_path):
    session = _session(
        tmp_path,
        decision=(
            "All guards clean post-outage. orphan_guard 0 orphan/0 ghost. "
            "Wallet $72.82 liquid, no stranded inventory."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    assert _statuses(result)["reconciliation"] == "PASS"


def test_guard_summary_wording_is_reconciliation_evidence(tmp_path):
    session = _session(
        tmp_path,
        decision=(
            "Guards clean (0 orphan/0 ghost/49 phantom-cache, $0 stranded). "
            "Core remains healthy."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    assert _statuses(result)["reconciliation"] == "PASS"


def test_abbreviated_recon_wording_is_reconciliation_evidence(tmp_path):
    session = _session(
        tmp_path,
        decision=(
            "Recon clean: 0 orphans/ghosts (49 phantom-cache rows ignored). "
            "Wallet has no stranded inventory."
        ),
    )

    result = soak.audit(session, now=1010, pid_probe=lambda _pid: True)

    assert _statuses(result)["reconciliation"] == "PASS"


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
