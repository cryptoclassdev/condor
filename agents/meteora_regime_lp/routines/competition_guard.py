"""Read-only finals clock for entry cutoff and deterministic wind-down."""

import importlib
import time

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from agents.meteora_regime_lp import competition as competition_policy

CATEGORY = "Risk Management"

competition_policy = importlib.reload(competition_policy)


class Config(BaseModel):
    enabled: bool = False
    end_at_utc: str = ""
    now_utc: float = Field(default=0, ge=0)
    open_positions: int = Field(default=0, ge=0)
    new_entry_cutoff_min: int = Field(default=135, gt=0)
    winddown_buffer_min: int = Field(default=15, gt=0)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    del context
    now_utc = config.now_utc or time.time()
    decision = competition_policy.plan_competition_tick(
        competition_policy.CompetitionObservation(
            enabled=config.enabled,
            now_utc=now_utc,
            end_at_utc=config.end_at_utc,
            open_positions=config.open_positions,
        ),
        competition_policy.CompetitionPolicy(
            new_entry_cutoff_min=config.new_entry_cutoff_min,
            winddown_buffer_min=config.winddown_buffer_min,
        ),
    )
    remaining = (
        "unknown"
        if decision.minutes_remaining is None
        else f"{decision.minutes_remaining:.1f}m"
    )
    return (
        f"competition_guard: {decision.action} — remaining={remaining}; "
        f"allow_new_entries={str(decision.allow_new_entries).lower()}; "
        f"close_all={str(decision.close_all).lower()}. {decision.reason}"
    )
