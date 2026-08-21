"""Persist and evaluate one chain-reconciled LP execution outcome.

This routine does not execute transactions or edit strategy configuration.  It
returns the bounded effective policy that the next entry may use and records an
append-only audit event under the agent's ignored runtime store.
"""

import importlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from agents.meteora_regime_lp import outcome_learning as outcome_policy

CATEGORY = "Risk Management"
STORE_ROOT = Path(__file__).resolve().parents[1] / "store" / "outcome_learning"

# Agent routines hot-reload independently from their imported pure modules.
outcome_policy = importlib.reload(outcome_policy)


class Config(BaseModel):
    mode: Literal["record", "entry_check"] = "record"
    attempt_id: str = Field(min_length=1)
    action: Literal["create", "close", "monitor"]
    pool_address: str = Field(min_length=1)
    sleeve: Literal["core", "satellite", "runner", "runner_micro"]
    executor_status: str = "UNKNOWN"
    chain_position_found: bool | None = None
    wallet_delta_usd: float | None = None
    error_message: str = ""
    confirmation_age_ticks: int = Field(default=0, ge=0)
    exit_reason: str = ""
    pnl_pct: float | None = None
    vs_hodl_pct: float | None = None
    observed_tick: int = Field(default=0, ge=0)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    del context
    store = outcome_policy.FileLearningStore(STORE_ROOT)
    try:
        prior = store.load_state()
    except ValueError as exc:
        return (
            "outcome_learner: BLOCKED — persistent learning state is unreadable; "
            f"no retry or policy change is authorized ({exc})."
        )

    if config.mode == "entry_check":
        permission = outcome_policy.OutcomeLearner().entry_permission(
            config.pool_address, config.observed_tick, prior
        )
        store.save_state(permission.state)
        verdict = "ENTRY_ALLOWED" if permission.allowed else "ENTRY_BLOCKED"
        policy = permission.state.policy
        return (
            f"outcome_learner: {verdict} — {permission.reason}; "
            f"entry_haircut_bps={policy.entry_haircut_bps}; "
            f"rpc_backoff_scale={policy.rpc_backoff_scale:.2f}; "
            f"range_width_scale={policy.range_width_scale:.2f}."
        )

    attempt_data = config.model_dump()
    attempt_data.pop("mode", None)
    attempt = outcome_policy.PositionAttempt(**attempt_data)
    result = outcome_policy.OutcomeLearner().observe(attempt, prior)
    store.record(attempt, result)
    policy = result.state.policy
    adjustment = result.state.last_adjustment or "none"
    return (
        f"outcome_learner: {result.outcome} — next={result.next_action}; "
        f"retry_allowed={str(result.retry_allowed).lower()}; "
        f"entry_haircut_bps={policy.entry_haircut_bps}; "
        f"rpc_backoff_scale={policy.rpc_backoff_scale:.2f}; "
        f"range_width_scale={policy.range_width_scale:.2f}; "
        f"adjustment={adjustment}. {result.explanation}"
    )
