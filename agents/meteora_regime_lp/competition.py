"""Deterministic competition-clock policy for the 48-hour finals run."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class CompetitionPolicy:
    new_entry_cutoff_min: int = 135
    winddown_buffer_min: int = 15


@dataclass(frozen=True)
class CompetitionObservation:
    enabled: bool
    now_utc: float
    end_at_utc: str
    open_positions: int


@dataclass(frozen=True)
class CompetitionDecision:
    action: str
    allow_new_entries: bool
    close_all: bool
    minutes_remaining: float | None
    reason: str


def _blocked(reason: str) -> CompetitionDecision:
    return CompetitionDecision(
        action="BLOCKED",
        allow_new_entries=False,
        close_all=False,
        minutes_remaining=None,
        reason=reason,
    )


def _parse_end(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("end_at_utc must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def plan_competition_tick(
    observation: CompetitionObservation,
    policy: CompetitionPolicy = CompetitionPolicy(),
) -> CompetitionDecision:
    """Return the finals-clock action without touching positions or configuration."""

    if not observation.enabled:
        return CompetitionDecision(
            action="DISABLED",
            allow_new_entries=True,
            close_all=False,
            minutes_remaining=None,
            reason="competition clock is disabled outside the finals run",
        )
    if (
        policy.winddown_buffer_min <= 0
        or policy.new_entry_cutoff_min < policy.winddown_buffer_min
    ):
        return _blocked("invalid competition clock policy")
    if observation.open_positions < 0 or not math.isfinite(observation.now_utc):
        return _blocked("invalid competition observation")
    try:
        end = _parse_end(observation.end_at_utc)
    except (TypeError, ValueError):
        return _blocked("a valid end_at_utc is required when finals mode is enabled")

    minutes_remaining = (end.timestamp() - observation.now_utc) / 60.0
    if minutes_remaining <= policy.winddown_buffer_min:
        if observation.open_positions > 0:
            return CompetitionDecision(
                action="EXIT_ALL_NOW",
                allow_new_entries=False,
                close_all=True,
                minutes_remaining=minutes_remaining,
                reason=(
                    "the deterministic wind-down buffer has started; close and verify "
                    "all positions before organizer-forced settlement"
                ),
            )
        return CompetitionDecision(
            action="HOLD_CASH",
            allow_new_entries=False,
            close_all=False,
            minutes_remaining=minutes_remaining,
            reason="all positions are closed for the end-of-race settlement window",
        )
    if minutes_remaining <= policy.new_entry_cutoff_min:
        return CompetitionDecision(
            action="WIND_DOWN",
            allow_new_entries=False,
            close_all=False,
            minutes_remaining=minutes_remaining,
            reason=(
                "new entries are disabled; monitor existing positions and honor their "
                "normal exits until the final close buffer"
            ),
        )
    return CompetitionDecision(
        action="ACTIVE",
        allow_new_entries=True,
        close_all=False,
        minutes_remaining=minutes_remaining,
        reason="competition clock permits normal guarded operation",
    )
