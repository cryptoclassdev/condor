import asyncio
from datetime import datetime, timezone

from agents.meteora_regime_lp.competition import (
    CompetitionObservation,
    CompetitionPolicy,
    plan_competition_tick,
)
from agents.meteora_regime_lp.routines import competition_guard
from condor.agents.strategy import StrategyStore


END = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _observation(minutes_remaining: int, open_positions: int = 1):
    return CompetitionObservation(
        enabled=True,
        now_utc=END.timestamp() - minutes_remaining * 60,
        end_at_utc=END.isoformat(),
        open_positions=open_positions,
    )


def test_active_race_allows_entries_before_cutoff():
    decision = plan_competition_tick(_observation(136))

    assert decision.action == "ACTIVE"
    assert decision.allow_new_entries is True
    assert decision.close_all is False


def test_final_lifecycle_window_blocks_new_entries_but_keeps_monitoring():
    decision = plan_competition_tick(_observation(135))

    assert decision.action == "WIND_DOWN"
    assert decision.allow_new_entries is False
    assert decision.close_all is False


def test_final_buffer_exits_every_open_position():
    decision = plan_competition_tick(_observation(15, open_positions=2))

    assert decision.action == "EXIT_ALL_NOW"
    assert decision.allow_new_entries is False
    assert decision.close_all is True


def test_missing_finals_timestamp_fails_closed_for_new_entries():
    decision = plan_competition_tick(
        CompetitionObservation(
            enabled=True,
            now_utc=END.timestamp(),
            end_at_utc="",
            open_positions=1,
        )
    )

    assert decision.action == "BLOCKED"
    assert decision.allow_new_entries is False
    assert decision.close_all is False


def test_invalid_clock_policy_is_rejected():
    decision = plan_competition_tick(
        _observation(200),
        CompetitionPolicy(new_entry_cutoff_min=10, winddown_buffer_min=15),
    )

    assert decision.action == "BLOCKED"


def test_routine_exposes_exit_instruction_without_executing_it():
    text = asyncio.run(
        competition_guard.run(
            competition_guard.Config(
                enabled=True,
                end_at_utc=END.isoformat(),
                now_utc=END.timestamp() - 10 * 60,
                open_positions=2,
            ),
            context=None,
        )
    )

    assert "EXIT_ALL_NOW" in text
    assert "close_all=true" in text


def test_live_default_stays_disabled_until_exact_finals_end_is_known():
    strategy = StrategyStore().get("meteora_regime_lp", "regime_lp_operator")

    assert strategy is not None
    config = strategy.default_config["competition"]
    assert config == {
        "enabled": False,
        "end_at_utc": "",
        "new_entry_cutoff_min": 135,
        "winddown_buffer_min": 15,
    }
