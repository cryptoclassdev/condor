#!/usr/bin/env python3
"""Read-only health audit for an unattended Meteora Regime LP session.

The audit consumes Condor's durable session status, journal, and snapshot files.
It never calls an exchange, wallet, or transaction endpoint.  Repeated invocations
append compact JSON samples beside the session so operators can prove tick progress,
reconciliation health, inventory hygiene, and scanner availability over a soak run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_ROOT = (
    PROJECT_ROOT
    / "agents"
    / "meteora_regime_lp"
    / "strategies"
    / "regime_lp_operator"
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _session_number(path: Path) -> int:
    match = re.fullmatch(r"session_(\d+)", path.name)
    return int(match.group(1)) if match else -1


def latest_session(strategy_root: Path = STRATEGY_ROOT) -> Path:
    sessions = [
        path
        for path in (strategy_root / "sessions").glob("session_*")
        if path.is_dir() and _session_number(path) >= 0
    ]
    if not sessions:
        raise RuntimeError(f"no sessions found under {strategy_root / 'sessions'}")
    return max(sessions, key=_session_number)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _latest_decisions(journal: str, limit: int = 5) -> list[str]:
    section = journal.partition("## Decisions\n")[2].partition("\n## Ticks")[0]
    decisions = re.findall(r"^- \*\*#\d+\*\*.*$", section, flags=re.MULTILINE)
    return decisions[-limit:]


def _snapshot_metrics(snapshot: str) -> dict[str, Any]:
    executor_section = snapshot.partition("## Executor State\n")[2].partition(
        "\n## Risk State"
    )[0]
    risk_section = snapshot.partition("## Risk State\n")[2].partition(
        "\n## Agent Response"
    )[0]
    active = re.search(r"Active Executors \((\d+)\)", executor_section)
    pnl = re.search(r"Total PnL: \$([+-]?\d+(?:\.\d+)?)", executor_section)
    exposure = re.search(r"Position Size: \$([\d.]+)", risk_section)
    risk = re.search(r"Status: ([A-Z_]+)", risk_section)
    return {
        "open_executors": int(active.group(1)) if active else None,
        "pnl_usd": float(pnl.group(1)) if pnl else None,
        "exposure_usd": float(exposure.group(1)) if exposure else None,
        "risk_state": risk.group(1) if risk else None,
    }


def _previous_sample(log_path: Path) -> dict[str, Any] | None:
    if not log_path.exists():
        return None
    for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
        try:
            return json.loads(line)
        except (TypeError, ValueError):
            continue
    return None


def audit(
    session_dir: Path,
    *,
    now: float | None = None,
    stale_after_sec: int = 600,
    log_path: Path | None = None,
    pid_probe=_pid_alive,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    status = json.loads((session_dir / "status.json").read_text(encoding="utf-8"))
    tick = int(status.get("tick") or 0)
    snapshot_path = session_dir / "snapshots" / f"snapshot_{tick}.md"
    snapshot = snapshot_path.read_text(encoding="utf-8")
    journal = (session_dir / "journal.md").read_text(encoding="utf-8")
    decisions = _latest_decisions(journal)
    newest = decisions[-1] if decisions else ""
    agent_response = snapshot.partition("## Agent Response\n")[2]
    current_evidence = "\n".join([newest, agent_response])
    recent = "\n".join([*decisions, agent_response])
    metrics = _snapshot_metrics(snapshot)
    checks: list[Check] = []

    checks.append(
        Check(
            "session_state",
            "PASS" if status.get("state") == "running" else "FAIL",
            f"state={status.get('state')}",
        )
    )
    pid = int(status.get("pid") or 0)
    process_alive = pid_probe(pid)
    checks.append(
        Check(
            "process",
            "PASS" if process_alive else "FAIL",
            f"pid={pid} {'alive' if process_alive else 'not running'}",
        )
    )
    age = max(0.0, now - float(status.get("updated_at") or 0))
    checks.append(
        Check(
            "freshness",
            "PASS" if age <= stale_after_sec else "FAIL",
            f"tick={tick}, age={age:.0f}s, limit={stale_after_sec}s",
        )
    )
    clean_reconciliation = bool(
        re.search(
            r"(?:(?:(?:recon|reconcile|reconciliation)\s+clean|"
            r"orphan_guard:\s*clean|guards?\s+clean)"
            r"[^.\n]{0,80}|orphan_guard\s*:?\s*)"
            r"0\s+orphans?(?:\s*/\s*(?:0\s*)?|\s*,\s*0\s*)ghosts?",
            current_evidence,
            re.IGNORECASE,
        )
    )
    checks.append(
        Check(
            "reconciliation",
            "PASS" if clean_reconciliation else "FAIL",
            "latest decision reports 0 orphan / 0 ghost"
            if clean_reconciliation
            else "latest decision lacks clean 0-orphan/0-ghost evidence",
        )
    )
    positive_usd = r"(?:[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]\d*)"
    stranded_bad = bool(
        re.search(
            rf"(?:\${positive_usd}\s+stranded|"
            rf"stranded(?: inventory)?[^.;]{{0,30}}\${positive_usd})",
            current_evidence,
            re.I,
        )
    )
    cleanup_safely_deferred = bool(
        re.search(
            r"cleanup\s+WAIT[^.]{0,120}(?:LP|executor)[^.]{0,80}RUNNING",
            current_evidence,
            re.I,
        )
    )
    stranded_clear = bool(
        re.search(r"(?:no|\$0(?:\.0+)?)\s+stranded", recent, re.I)
    )
    inventory_status = (
        "WARN"
        if stranded_bad and cleanup_safely_deferred
        else ("FAIL" if stranded_bad else ("PASS" if stranded_clear else "WARN"))
    )
    checks.append(
        Check(
            "wallet_inventory",
            inventory_status,
            "recent direct wallet audit reports no stranded inventory"
            if stranded_clear and not stranded_bad
            else (
                "stranded inventory cleanup is safely deferred while its LP is running"
                if stranded_bad and cleanup_safely_deferred
                else "latest decision reports stranded inventory"
                if stranded_bad
                else "no explicit inventory result in the last five decisions"
            ),
        )
    )
    risk_state = metrics.get("risk_state")
    checks.append(
        Check(
            "risk_engine",
            "PASS" if risk_state == "ACTIVE" else "FAIL",
            f"status={risk_state or 'unknown'}",
        )
    )

    scanner_issue = None
    scanner_matches = re.findall(r"runner_scanner[^.\n]*(?:SOURCE (?:TIMEOUT|DEGRADED)|PAUSED)", recent, re.I)
    if scanner_matches:
        scanner_issue = scanner_matches[-1]
    checks.append(
        Check(
            "runner_scanner",
            "WARN" if scanner_issue else "PASS",
            scanner_issue or "no recent degradation recorded",
        )
    )

    if "zero-proceeds/unverified" in newest or "unchecked-close" in newest:
        checks.append(
            Check(
                "historical_close_evidence",
                "WARN",
                "a prior close still needs meteora_truth evidence; no live orphan/ghost",
            )
        )

    if log_path is not None:
        previous = _previous_sample(log_path)
        if previous and float(previous.get("observed_at") or 0) < now:
            prior_tick = int(previous.get("tick") or 0)
            elapsed = now - float(previous.get("observed_at") or 0)
            progressed = tick > prior_tick or elapsed < stale_after_sec
            checks.append(
                Check(
                    "tick_progress",
                    "PASS" if progressed else "FAIL",
                    f"tick {prior_tick}->{tick} over {elapsed:.0f}s",
                )
            )
        else:
            checks.append(Check("tick_progress", "WARN", "baseline sample created"))

    overall = "FAIL" if any(c.status == "FAIL" for c in checks) else (
        "WARN" if any(c.status == "WARN" for c in checks) else "PASS"
    )
    result: dict[str, Any] = {
        "overall": overall,
        "observed_at": now,
        "session": session_dir.name,
        "tick": tick,
        **metrics,
        "checks": [asdict(check) for check in checks],
    }
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, help="specific session directory")
    parser.add_argument("--stale-after-sec", type=int, default=600)
    parser.add_argument("--no-log", action="store_true")
    args = parser.parse_args()
    session_dir = args.session or latest_session()
    log_path = None if args.no_log else session_dir / "soak_audit.jsonl"
    result = audit(
        session_dir,
        stale_after_sec=args.stale_after_sec,
        log_path=log_path,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(2 if result["overall"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
