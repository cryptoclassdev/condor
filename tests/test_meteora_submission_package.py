from pathlib import Path
import re

from routines.base import discover_routines_from_path


ROOT = Path(__file__).resolve().parents[1]


def test_judge_package_contains_every_required_artifact():
    required = {
        "agent": ROOT / "agents/meteora_regime_lp/AGENT.md",
        "strategy": ROOT
        / "agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md",
        "portable learning baseline": ROOT
        / "agents/meteora_regime_lp/baseline_policy.json",
        "submission copy": ROOT / "hackathon/submission.md",
        "submission preview": ROOT / "hackathon/submission.html",
        "portal form copy": ROOT / "hackathon/form-submission-copy.md",
        "portal form preview": ROOT / "hackathon/form-submission-copy.html",
        "demo script": ROOT / "hackathon/demo-script.md",
        "judge quick start": ROOT / "hackathon/JUDGE_QUICKSTART.md",
        "readiness matrix": ROOT / "hackathon/SUBMISSION_READINESS.md",
        "upstream issue drafts": ROOT / "hackathon/upstream-bug-reports.md",
        "organizer Q&A": ROOT / "hackathon/ORGANIZER_QA.md",
    }

    missing = [label for label, path in required.items() if not path.is_file()]

    assert missing == []


def test_submission_copy_matches_the_current_botcamp_rules_and_evidence_policy():
    submission = (ROOT / "hackathon/submission.md").read_text()

    assert "40% volume" in submission
    assert "40% P&L" in submission
    assert "20% HBOT vote" in submission
    assert "rank-normalized" in submission
    assert "gross filled" in submission
    assert "48 hours" in submission
    assert "August 31, 2026" in submission
    assert "https://github.com/cryptoclassdev/condor/tree/meteora-cup" in submission
    assert "<DEMO_VIDEO_URL>" in submission
    assert "$77.30" not in submission
    assert "-$2.8245" not in submission
    assert "TODO:" not in submission


def test_demo_is_sub_three_minutes_and_uses_refreshable_evidence():
    demo = (ROOT / "hackathon/demo-script.md").read_text()
    narration = " ".join(re.findall(r"\*\*Narration:\*\* (.*?)(?=\n\n|$)", demo, re.S))

    assert "2:55 target" in demo
    assert len(narration.split()) <= 420
    assert "refresh immediately before recording" in demo.lower()
    assert "$77" not in demo
    assert "-$2.82" not in demo
    assert "false-success" in demo
    assert "Guardian" in demo and "Balanced" in demo and "Hunter" in demo


def test_submission_preview_matches_current_rules_and_evidence():
    preview = (ROOT / "hackathon/submission.html").read_text()

    assert "40% Volume" in preview
    assert "40% P&amp;L" in preview
    assert "20% HBOT Vote" in preview
    assert "≈$2.71K" in preview
    assert "-$4.07" in preview
    assert "Token-2022" in preview
    assert "$77.30" not in preview
    assert "-$2.8245" not in preview


def test_meteora_routine_package_is_discoverable():
    routines = discover_routines_from_path(
        ROOT / "agents/meteora_regime_lp/routines", force_reload=True
    )
    required = {
        "capital_guard",
        "competition_guard",
        "hedge_guard",
        "inventory_cleanup_guard",
        "lifecycle_guard",
        "meteora_pool_scanner",
        "meteora_truth",
        "orphan_guard",
        "outcome_learner",
        "quick_in_out_guard",
        "regime_engine",
        "runner_scanner",
        "token_safety_check",
        "wallet_audit",
    }

    assert required <= routines.keys()


def test_makefile_exposes_one_command_judge_verification():
    makefile = (ROOT / "Makefile").read_text()

    assert "verify-meteora:" in makefile
    assert "test_meteora_submission_package.py" in makefile
    assert "npm test" in makefile
    assert "npm run build" in makefile


def test_strategy_tool_budget_covers_verified_rechase_sequence():
    strategy = (
        ROOT / "agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md"
    ).read_text()

    assert "max_tool_calls_per_tick: 16" in strategy
    assert "≤ 16 operational MCP calls" in strategy
