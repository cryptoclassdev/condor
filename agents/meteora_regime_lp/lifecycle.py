"""Deterministic lifecycle checks for open Meteora LP executors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        timestamp = float(value)
        if timestamp > 1e11:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(
                timezone.utc
            )
        except ValueError:
            return None
    return None


def _pct(value) -> float:
    number = _num(value)
    return number * 100.0 if abs(number) <= 1.0 else number


@dataclass(frozen=True)
class LifecycleRow:
    position: str
    executor: str
    sleeve: str
    age_min: float
    deadline_utc: str
    minutes_remaining: float | None
    pnl_pct: float
    peak_pnl_pct: float
    fill_pct: float
    fill_change_pct: float | None
    state: str
    action: str
    reasons: tuple[str, ...]


def evaluate_lifecycle(
    executors: Iterable[dict],
    *,
    now: datetime,
    previous_state: dict[str, dict],
    satellite_max_hold_min: float,
    runner_max_hold_min: float,
    satellite_stop_loss_pct: float,
    runner_stop_loss_pct: float,
    runner_executor_ids: Iterable[str] = (),
    micro_runner_executor_ids: Iterable[str] = (),
    micro_runner_max_hold_min: float = 15.0,
    micro_runner_stop_loss_pct: float = 3.0,
    micro_runner_take_profit_pct: float = 5.0,
    material_fill_change_pct: float = 20.0,
    early_exit_buffer_pct: float = 2.0,
    out_of_range_max_sec: float = 1800.0,
    out_of_range_buffer_pct: float = 0.5,
    rebalance_cooldown_sec: float = 900.0,
) -> tuple[list[LifecycleRow], dict[str, dict]]:
    """Evaluate exact deadlines, stop proximity and material fill changes."""

    now = now.astimezone(timezone.utc)
    runner_ids = set(runner_executor_ids)
    micro_runner_ids = set(micro_runner_executor_ids)
    next_state = {key: dict(value) for key, value in previous_state.items()}
    rows: list[LifecycleRow] = []

    for executor in executors:
        custom = (
            executor.get("custom_info")
            if isinstance(executor.get("custom_info"), dict)
            else {}
        )
        config = (
            executor.get("config") if isinstance(executor.get("config"), dict) else {}
        )
        executor_id = str(
            executor.get("executor_id") or executor.get("id") or config.get("id") or "?"
        )
        position = str(custom.get("position_address") or executor_id)
        pair = str(executor.get("trading_pair") or config.get("trading_pair") or "")
        created = _timestamp(
            executor.get("created_at")
            or executor.get("timestamp")
            or config.get("timestamp")
        )
        if created is None:
            created = now

        take_profit_pct: float | None = None
        if executor_id in micro_runner_ids:
            sleeve = "runner_micro"
            hold_min = float(micro_runner_max_hold_min)
            stop_pct = float(micro_runner_stop_loss_pct)
            take_profit_pct = float(micro_runner_take_profit_pct)
        elif executor_id in runner_ids:
            sleeve = "runner"
            hold_min = float(runner_max_hold_min)
            stop_pct = float(runner_stop_loss_pct)
        elif pair.endswith(f"-{USDC_MINT}"):
            sleeve = "core"
            hold_min = 0.0
            stop_pct = float(satellite_stop_loss_pct)
        else:
            sleeve = "satellite"
            hold_min = float(satellite_max_hold_min)
            stop_pct = float(satellite_stop_loss_pct)

        base_value = _num(custom.get("base_amount")) * _num(custom.get("current_price"))
        quote_value = _num(custom.get("quote_amount"))
        total_value = base_value + quote_value
        fill_pct = 100.0 * base_value / total_value if total_value > 0 else 0.0
        fill_pct = round(fill_pct, 2)

        prior = previous_state.get(position) or {}
        prior_fill = prior.get("fill_pct")
        fill_change = (
            round(fill_pct - float(prior_fill), 2) if prior_fill is not None else None
        )
        pnl_pct = round(_pct(executor.get("net_pnl_pct")), 3)
        peak = round(max(_num(prior.get("peak_pnl_pct"), pnl_pct), pnl_pct), 3)

        deadline = created + timedelta(minutes=hold_min) if hold_min > 0 else None
        remaining = (
            round((deadline - now).total_seconds() / 60.0, 1)
            if deadline is not None
            else None
        )
        reasons: list[str] = []
        if deadline is not None and remaining is not None and remaining <= 0:
            reasons.append("max-hold")
        if pnl_pct <= -stop_pct:
            reasons.append("stop-loss")
        if take_profit_pct is not None and pnl_pct >= take_profit_pct:
            reasons.append("take-profit")

        state = str(custom.get("state") or "UNKNOWN").upper()
        current_price = _num(custom.get("current_price"))
        lower_price = _num(custom.get("lower_price"))
        upper_price = _num(custom.get("upper_price"))
        edge_distance_pct = 0.0
        if current_price > 0 and lower_price > 0 and current_price < lower_price:
            edge_distance_pct = 100.0 * (lower_price - current_price) / lower_price
        elif current_price > 0 and upper_price > 0 and current_price > upper_price:
            edge_distance_pct = 100.0 * (current_price - upper_price) / upper_price
        age_sec = max(0.0, (now - created).total_seconds())
        if (
            state == "OUT_OF_RANGE"
            and _num(custom.get("out_of_range_seconds")) >= out_of_range_max_sec
            and edge_distance_pct > out_of_range_buffer_pct
            and age_sec >= rebalance_cooldown_sec
        ):
            reasons.append("out-of-range-timeout")

        if reasons:
            action = "EXIT_NOW"
        elif (
            sleeve != "core"
            and fill_pct >= 10.0
            and pnl_pct <= -(stop_pct - float(early_exit_buffer_pct))
        ):
            action = "EARLY_EXIT_REVIEW"
            reasons.append("within-stop-buffer")
        elif fill_change is not None and abs(fill_change) >= material_fill_change_pct:
            action = "REGIME_RECHECK"
            reasons.append("material-fill-change")
        else:
            action = "MONITOR"

        next_state[position] = {
            "fill_pct": fill_pct,
            "peak_pnl_pct": peak,
            "last_seen_utc": now.isoformat(),
            "sleeve": sleeve,
        }
        rows.append(
            LifecycleRow(
                position=position,
                executor=executor_id,
                sleeve=sleeve,
                age_min=round((now - created).total_seconds() / 60.0, 1),
                deadline_utc=(
                    deadline.strftime("%Y-%m-%d %H:%M:%S UTC")
                    if deadline is not None
                    else "none"
                ),
                minutes_remaining=remaining,
                pnl_pct=pnl_pct,
                peak_pnl_pct=peak,
                fill_pct=fill_pct,
                fill_change_pct=fill_change,
                state=state,
                action=action,
                reasons=tuple(reasons),
            )
        )

    return rows, next_state
