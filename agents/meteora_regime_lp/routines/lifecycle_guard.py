"""Minute-level deadlines, stop proximity and fill-change triggers for open LPs."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from agents.meteora_regime_lp.lifecycle import evaluate_lifecycle
from config_manager import get_client

CATEGORY = "Analysis"
_STATE_KEY = "_meteora_regime_lp_lifecycle"


class Config(BaseModel):
    satellite_max_hold_min: float = 120.0
    runner_max_hold_min: float = 90.0
    satellite_stop_loss_pct: float = 8.0
    runner_stop_loss_pct: float = 6.0
    micro_runner_max_hold_min: float = 15.0
    micro_runner_stop_loss_pct: float = 3.0
    micro_runner_take_profit_pct: float = 5.0
    material_fill_change_pct: float = 20.0
    early_exit_buffer_pct: float = 2.0
    out_of_range_max_sec: float = 1800.0
    out_of_range_buffer_pct: float = 0.5
    rebalance_cooldown_sec: float = 900.0
    runner_executor_ids: list[str] = Field(
        default=[],
        description="Explicit runner executor ids; all other non-core LPs are satellites",
    )
    micro_runner_executor_ids: list[str] = Field(
        default=[],
        description="Quick-in/out executor ids with their own shorter exits",
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "lifecycle_guard: UNKNOWN — no server available."
    try:
        response = await client.executors.search_executors(status="RUNNING", limit=50)
    except Exception as exc:
        return f"lifecycle_guard: UNKNOWN — executor read failed ({exc})."

    executors = (
        response.get("data") or response.get("executors") or []
        if isinstance(response, dict)
        else response or []
    )
    executors = [
        row
        for row in executors
        if isinstance(row, dict)
        and str(row.get("executor_type") or row.get("type") or "") == "lp_executor"
    ]

    user_data = getattr(context, "user_data", None)
    if user_data is None:
        user_data = getattr(context, "_user_data", None)
    if user_data is None:
        user_data = {}
    prior = (
        user_data.get(_STATE_KEY) if isinstance(user_data.get(_STATE_KEY), dict) else {}
    )

    lifecycle_kwargs = {
        "now": datetime.now(timezone.utc),
        "previous_state": prior,
        "satellite_max_hold_min": config.satellite_max_hold_min,
        "runner_max_hold_min": config.runner_max_hold_min,
        "satellite_stop_loss_pct": config.satellite_stop_loss_pct,
        "runner_stop_loss_pct": config.runner_stop_loss_pct,
        "runner_executor_ids": config.runner_executor_ids,
        "material_fill_change_pct": config.material_fill_change_pct,
        "early_exit_buffer_pct": config.early_exit_buffer_pct,
    }
    # Agent routines hot-reload without restarting the Condor process. During a
    # rollout, this routine can briefly see the old imported lifecycle module;
    # omit the new keys until the next process restart instead of losing all
    # lifecycle coverage to an unexpected-keyword TypeError.
    if "micro_runner_executor_ids" in inspect.signature(evaluate_lifecycle).parameters:
        lifecycle_kwargs.update(
            {
                "micro_runner_executor_ids": config.micro_runner_executor_ids,
                "micro_runner_max_hold_min": config.micro_runner_max_hold_min,
                "micro_runner_stop_loss_pct": config.micro_runner_stop_loss_pct,
                "micro_runner_take_profit_pct": config.micro_runner_take_profit_pct,
            }
        )
    if "out_of_range_max_sec" in inspect.signature(evaluate_lifecycle).parameters:
        lifecycle_kwargs.update(
            {
                "out_of_range_max_sec": config.out_of_range_max_sec,
                "out_of_range_buffer_pct": config.out_of_range_buffer_pct,
                "rebalance_cooldown_sec": config.rebalance_cooldown_sec,
            }
        )
    rows, next_state = evaluate_lifecycle(executors, **lifecycle_kwargs)
    user_data[_STATE_KEY] = next_state

    if not rows:
        return "lifecycle_guard: no RUNNING LP executors."

    urgent = [row for row in rows if row.action == "EXIT_NOW"]
    rechecks = [row for row in rows if row.action == "REGIME_RECHECK"]
    summary = (
        f"lifecycle_guard: {len(rows)} LP(s); {len(urgent)} EXIT_NOW; "
        f"{len(rechecks)} regime recheck(s). Deadlines and actions below are deterministic."
    )
    table = []
    for row in rows:
        change = (
            "first read"
            if row.fill_change_pct is None
            else f"{row.fill_change_pct:+.1f}pp"
        )
        remaining = (
            "n/a" if row.minutes_remaining is None else f"{row.minutes_remaining:.1f}m"
        )
        table.append(
            {
                "Position": row.position[:8] + "…",
                "Sleeve": row.sleeve,
                "Age": f"{row.age_min:.1f}m",
                "Deadline": row.deadline_utc,
                "Remaining": remaining,
                "PnL": f"{row.pnl_pct:+.2f}%",
                "Fill": f"{row.fill_pct:.1f}% ({change})",
                "State": row.state,
                "Action": row.action,
                "Reason": ", ".join(row.reasons) or "none",
            }
        )

    if urgent:
        summary += (
            " EXIT_NOW is a hard rule: stop the listed executor, then verify both "
            "RUNNING absence and the on-chain/wallet delta before calling it closed."
        )
    if rechecks:
        summary += (
            " REGIME_RECHECK means fill changed by at least the configured threshold; "
            "run regime_engine before deciding monitor/flip/exit."
        )

    columns = [
        "Position",
        "Sleeve",
        "Age",
        "Deadline",
        "Remaining",
        "PnL",
        "Fill",
        "State",
        "Action",
        "Reason",
    ]
    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary, table_data=table, table_columns=columns)
    except Exception:
        return summary + "\n" + "\n".join(str(row) for row in table)
