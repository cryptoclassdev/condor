"""Deterministic host adapter for reconciled Meteora execution outcomes.

The reasoning loop still decides *what* to do.  This adapter observes completed
``manage_executors(create|stop)`` calls, persists a pending attempt, and on a
later tick reconciles it against executor detail, Solana-owned positions, and
wallet movement.  It never submits a transaction and never changes hard risk
configuration.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.meteora_regime_lp.outcome_learning import (
    FileLearningStore,
    OutcomeLearner,
    PositionAttempt,
)
from agents.meteora_regime_lp.lifecycle import evaluate_lifecycle

STORE_ROOT = Path(__file__).resolve().parent / "store" / "outcome_learning"
PENDING_PATH = STORE_ROOT / "pending.json"
EQUITY_ROOT = Path(__file__).resolve().parent / "store" / "equity"
SLEEVES_PATH = STORE_ROOT / "sleeves.json"
MAX_RECONCILIATIONS_PER_TICK = 2
RECONCILIATION_ATTEMPT_TIMEOUT_SEC = 6
PREPARE_READ_TIMEOUT_SEC = 3

_POOL_KEYS = ("pool_address", "pool", "pool_id", "poolAddress")
_POSITION_KEYS = (
    "position_address",
    "position",
    "nft_address",
    "position_nft",
)
_EXECUTOR_KEYS = ("executor_id", "executorId")
_BASE58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,50}")
_WALLET_TOTAL = re.compile(r"total wallet \$([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)


@dataclass(frozen=True)
class PendingAttempt:
    attempt_id: str
    action: str
    pool_address: str
    sleeve: str
    executor_id: str
    executor_status: str
    wallet_before_usd: float | None
    position_address: str
    error_message: str
    observed_tick: int
    exit_reason: str = ""
    pnl_pct: float | None = None
    vs_hodl_pct: float | None = None


def _load_pending() -> list[PendingAttempt]:
    if not PENDING_PATH.exists():
        return []
    payload = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    return [PendingAttempt(**item) for item in payload.get("attempts", [])]


def _save_pending(attempts: list[PendingAttempt]) -> None:
    STORE_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = PENDING_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"version": 1, "attempts": [asdict(a) for a in attempts]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(PENDING_PATH)


def pending_pool(pool_address: str, pending_path: Path | None = None) -> bool:
    """Return whether an unresolved host-observed action gates this pool."""
    path = pending_path or PENDING_PATH
    try:
        if not path.exists():
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        return any(
            item.get("pool_address") == pool_address
            for item in payload.get("attempts", [])
            if isinstance(item, dict)
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        # An unreadable pending ledger is uncertainty, never permission.
        return True


def _load_sleeves() -> dict[str, str]:
    try:
        payload = json.loads(SLEEVES_PATH.read_text(encoding="utf-8"))
        return {
            str(executor_id): str(sleeve)
            for executor_id, sleeve in payload.get("executors", {}).items()
            if executor_id and sleeve
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_sleeves(sleeves: dict[str, str]) -> None:
    SLEEVES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SLEEVES_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"version": 1, "executors": sleeves}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(SLEEVES_PATH)


def sleeve_for_executor(executor_id: str) -> str:
    return _load_sleeves().get(executor_id, "")


def _remember_sleeve(executor_id: str, sleeve: str) -> None:
    if not executor_id or not sleeve:
        return
    sleeves = _load_sleeves()
    sleeves[executor_id] = sleeve
    # The map is only an identity adapter for live/recent executors, not an
    # unbounded history ledger.
    if len(sleeves) > 500:
        sleeves = dict(list(sleeves.items())[-500:])
    _save_sleeves(sleeves)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child)


def _pick(value: Any, keys: tuple[str, ...]) -> str:
    for item in _walk(value):
        for key in keys:
            found = item.get(key)
            if isinstance(found, str) and found:
                return found
    return ""


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _wallet_before(tool_calls: list[dict[str, Any]]) -> float | None:
    for call in tool_calls:
        name = str(call.get("name") or "").lower()
        call_input = call.get("input") or {}
        if "manage_routines" not in name or call_input.get("name") != "wallet_audit":
            continue
        match = _WALLET_TOTAL.search(_text(call.get("output")))
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _is_executor_action(call: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if "manage_executors" not in str(call.get("name") or "").lower():
        return None
    call_input = call.get("input") if isinstance(call.get("input"), dict) else {}
    action = str(call_input.get("action") or "").lower()
    if action not in {"create", "stop"}:
        return None
    output = _text(call.get("output"))
    if "tool use aborted" in output.lower() or "permission" in output.lower():
        return None
    return action, call_input


def _sleeve(call_input: dict[str, Any]) -> str:
    blob = _text(call_input).lower()
    if "runner_micro" in blob or "quick_in_out" in blob:
        return "runner_micro"
    if "runner" in blob:
        return "runner"
    # Exact SOL/USDC core mints appear together only for the core route.
    if "so11111111111111111111111111111111111111112" in blob and "epjfwdd5" in blob:
        return "core"
    return "satellite"


def _executor_status(call: dict[str, Any], action: str) -> tuple[str, str]:
    output = _text(call.get("output"))
    lowered = output.lower()
    failed = str(call.get("status") or "").lower() == "failed" or any(
        marker in lowered
        for marker in ("failed", "error:", '"error"', "exception")
    )
    if failed:
        return "FAILED", output[-1200:]
    return ("RUNNING" if action == "create" else "SUCCESS"), ""


async def _executor_detail(client: Any, executor_id: str) -> dict[str, Any]:
    if not executor_id:
        return {}
    result = await client.executors.get_executor(executor_id)
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        return result["data"]
    return result if isinstance(result, dict) else {}


async def _wallet_total(client: Any) -> float:
    state = await client.portfolio.get_state(refresh=True)
    return _wallet_total_from_state(state)


def _wallet_total_from_state(state: Any) -> float:
    total = 0.0
    if not isinstance(state, dict):
        raise ValueError("portfolio state is not a mapping")
    for account in state.values():
        if not isinstance(account, dict):
            continue
        for balances in account.values():
            for balance in balances or []:
                if isinstance(balance, dict) and balance.get("value") is not None:
                    total += float(balance["value"])
    return total


async def _owned(client: Any, pool_address: str) -> list[dict[str, Any]]:
    result = await client.gateway_clmm.get_positions_owned(
        connector="meteora",
        network="solana-mainnet-beta",
        pool_address=pool_address,
    )
    rows = result.get("data", []) if isinstance(result, dict) else result
    return [row for row in rows or [] if isinstance(row, dict)]


def _rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        for key in ("data", "executors", "results"):
            value = result.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _executor_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("executor_id") or "")


async def _running_for_agent(client: Any, agent_id: str) -> list[dict[str, Any]]:
    result = await client.executors.search_executors(
        status="RUNNING",
        controller_ids=[agent_id],
        limit=50,
    )
    return _rows(result)


def _owned_by_session(row: dict[str, Any], agent_id: str) -> bool:
    controller = str(
        row.get("controller_id")
        or (row.get("config") or {}).get("controller_id")
        or ""
    )
    stem, separator, suffix = agent_id.rpartition("_")
    prefix = f"{stem}_" if separator and suffix.isdigit() else ""
    return controller == agent_id or bool(prefix and controller.startswith(prefix))


async def _running_for_session(client: Any, agent_id: str) -> list[dict[str, Any]]:
    result = await client.executors.search_executors(status="RUNNING", limit=50)
    return [row for row in _rows(result) if _owned_by_session(row, agent_id)]


async def supervise(
    *,
    client: Any,
    agent_id: str,
    tick: int,
    config: dict[str, Any],
    now: datetime | str | None = None,
) -> list[str]:
    """Enforce pre-approved lifecycle exits when the model cannot answer.

    The supervisor cannot create positions or adjust policy.  It reads the
    current executor book, evaluates the same pure lifecycle rules used by the
    routine, and stops only session-owned LP executors with ``EXIT_NOW``.  Each
    stop is written to the pending authority ledger for next-tick verification.
    """
    response = await client.executors.search_executors(status="RUNNING", limit=50)
    executors = [
        row
        for row in _rows(response)
        if _owned_by_session(row, agent_id)
        and str(row.get("executor_type") or row.get("type") or "") == "lp_executor"
    ]
    if not executors:
        return []

    instant = now
    if isinstance(instant, str):
        instant = datetime.fromisoformat(instant.replace("Z", "+00:00"))
    if instant is None:
        instant = datetime.now(timezone.utc)
    instant = instant.replace(tzinfo=instant.tzinfo or timezone.utc).astimezone(
        timezone.utc
    )

    runner_ids: list[str] = []
    micro_ids: list[str] = []
    for row in executors:
        executor_id = _executor_id(row)
        custom = row.get("custom_info") or {}
        sleeve = str(
            custom.get("sleeve") or sleeve_for_executor(executor_id) or ""
        ).lower()
        if sleeve == "runner_micro":
            micro_ids.append(executor_id)
        elif sleeve == "runner":
            runner_ids.append(executor_id)

    satellite = config.get("satellite") or {}
    runner = config.get("runner") or {}
    micro = config.get("quick_in_out") or {}
    rows, _ = evaluate_lifecycle(
        executors,
        now=instant,
        previous_state={},
        satellite_max_hold_min=float(satellite.get("max_hold_min", 120)),
        runner_max_hold_min=float(runner.get("max_hold_min", 90)),
        satellite_stop_loss_pct=float(config.get("stop_loss_pct", 8)),
        runner_stop_loss_pct=float(runner.get("stop_loss_pct", 6)),
        runner_executor_ids=runner_ids,
        micro_runner_executor_ids=micro_ids,
        micro_runner_max_hold_min=float(micro.get("max_hold_min", 15)),
        micro_runner_stop_loss_pct=float(micro.get("stop_loss_pct", 3)),
        micro_runner_take_profit_pct=float(micro.get("take_profit_pct", 5)),
        out_of_range_max_sec=float(config.get("out_of_range_max_sec", 1800)),
        out_of_range_buffer_pct=float(config.get("out_of_range_buffer_pct", 0.5)),
        rebalance_cooldown_sec=float(config.get("rebalance_cooldown_sec", 900)),
    )
    urgent = {row.executor: row for row in rows if row.action == "EXIT_NOW"}
    if not urgent:
        return []

    # A hard exit must not wait on a slow/unavailable portfolio endpoint. Chain
    # absence is authoritative for close reconciliation; wallet proceeds are a
    # useful later observation, not a prerequisite for stopping risk.
    wallet_before = None
    pending = _load_pending()
    pending_executor_ids = {item.executor_id for item in pending}
    stopped: list[str] = []
    by_id = {_executor_id(row): row for row in executors}
    for executor_id, lifecycle in urgent.items():
        if executor_id in pending_executor_ids:
            continue
        await client.executors.stop_executor(
            executor_id=executor_id, keep_position=False
        )
        source = by_id[executor_id]
        custom = source.get("custom_info") or {}
        pending.append(
            PendingAttempt(
                attempt_id=f"host:{agent_id}:{tick}:timeout-close:{executor_id}",
                action="close",
                pool_address=_pick(source, _POOL_KEYS),
                sleeve=lifecycle.sleeve,
                executor_id=executor_id,
                executor_status="SUCCESS",
                wallet_before_usd=wallet_before,
                position_address=_pick(custom, _POSITION_KEYS),
                error_message="",
                observed_tick=tick,
                exit_reason=",".join(lifecycle.reasons),
                pnl_pct=lifecycle.pnl_pct,
            )
        )
        stopped.append(executor_id)
    if stopped:
        _save_pending(pending)
    return stopped


async def prepare(*, client: Any, agent_id: str, tick: int) -> dict[str, Any]:
    """Record authority baselines before the model can submit an action."""
    from agents.meteora_regime_lp.equity_ledger import (
        build_equity_snapshot,
        record_equity_snapshot,
    )

    running_result, portfolio_result = await asyncio.gather(
        asyncio.wait_for(
            _running_for_session(client, agent_id),
            timeout=PREPARE_READ_TIMEOUT_SEC,
        ),
        asyncio.wait_for(
            client.portfolio.get_state(refresh=True),
            timeout=PREPARE_READ_TIMEOUT_SEC,
        ),
        return_exceptions=True,
    )
    running = running_result if isinstance(running_result, list) else []
    executor_ids = sorted(filter(None, map(_executor_id, running)))
    if isinstance(portfolio_result, BaseException):
        return {
            "wallet_before_usd": None,
            "equity_before_usd": None,
            "executor_ids_before": executor_ids,
        }
    portfolio_state = portfolio_result
    snapshot = build_equity_snapshot(
        agent_id=agent_id,
        tick=tick,
        portfolio_state=portfolio_state,
        executors=running,
    )
    safe_agent_id = re.sub(r"[^A-Za-z0-9_.-]", "_", agent_id)
    record_equity_snapshot(EQUITY_ROOT / f"{safe_agent_id}.jsonl", snapshot)
    return {
        "wallet_before_usd": _wallet_total_from_state(portfolio_state),
        "equity_before_usd": snapshot.equity_usd,
        "executor_ids_before": executor_ids,
    }


def _chain_found(
    pending: PendingAttempt, detail: dict[str, Any], owned: list[dict[str, Any]]
) -> bool | None:
    # Gateway currently returns wallet-wide positions even when queried with a
    # pool. Scope rows ourselves before interpreting an empty/non-empty set;
    # otherwise an unrelated core position makes a successfully closed
    # satellite look permanently ambiguous.
    rows_with_pool = [row for row in owned if _pick(row, _POOL_KEYS)]
    if rows_with_pool:
        owned = [
            row
            for row in rows_with_pool
            if _pick(row, _POOL_KEYS) == pending.pool_address
        ]
    addresses = {_pick(row, _POSITION_KEYS) for row in owned}
    addresses.discard("")
    target = pending.position_address or _pick(detail, _POSITION_KEYS)
    if target:
        return target in addresses

    # Executor schemas have moved the position address repeatedly. Match any
    # base58 address present in its detail against authority rows, but never use
    # "some position exists in the pool" as proof of this specific attempt.
    executor_addresses = set(_BASE58.findall(_text(detail)))
    hits = executor_addresses & addresses
    if hits:
        return True
    return None if addresses else False


def _event_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
            seconds = float(value)
            if seconds > 1e11:
                seconds /= 1000
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _has_newer_owned_successor(
    pending: PendingAttempt,
    detail: dict[str, Any],
    owned: list[dict[str, Any]],
    running: list[dict[str, Any]],
) -> bool:
    """Prove an ambiguous same-pool row belongs to a replacement executor.

    Some historical executor records omitted their position address.  A pool
    can meanwhile be re-entered, so pool occupancy alone cannot keep the old
    close pending forever.  We accept only a strong temporal + identity match:
    the old executor is terminal, the replacement was created after its close,
    and the replacement's position address is present in chain authority rows.
    """
    if pending.action != "close" or pending.position_address:
        return False
    if str(detail.get("status") or "").upper() not in {
        "TERMINATED",
        "CLOSED",
        "STOPPED",
    }:
        return False
    closed_at = _event_time(
        detail.get("close_timestamp")
        or detail.get("closed_at")
        or detail.get("close_time")
    )
    if closed_at is None:
        return False
    owned_addresses = {_pick(row, _POSITION_KEYS) for row in owned}
    owned_addresses.discard("")
    for row in running:
        if _executor_id(row) == pending.executor_id:
            continue
        if _pick(row, _POOL_KEYS) != pending.pool_address:
            continue
        created_at = _event_time(
            row.get("created_at")
            or row.get("timestamp")
            or (row.get("config") or {}).get("timestamp")
        )
        position = _pick(row.get("custom_info") or row, _POSITION_KEYS)
        if created_at and created_at > closed_at and position in owned_addresses:
            return True
    return False


async def reconcile(*, client: Any, agent_id: str, tick: int) -> None:
    pending = _load_pending()
    current_prefix = f"host:{agent_id}:"
    due = [
        attempt
        for attempt in pending
        if not attempt.attempt_id.startswith(current_prefix)
        or tick > attempt.observed_tick
    ]
    if not due:
        return

    wallet_now: float | None = None
    store = FileLearningStore(STORE_ROOT)
    state = store.load_state()
    remaining = [
        attempt
        for attempt in pending
        if attempt.attempt_id.startswith(current_prefix)
        and tick <= attempt.observed_tick
    ]
    selected = due[:MAX_RECONCILIATIONS_PER_TICK]
    # Deferred work goes first so a repeatedly failing attempt cannot starve
    # the rest of the append-only reconciliation queue.
    remaining.extend(due[MAX_RECONCILIATIONS_PER_TICK:])

    running_now: list[dict[str, Any]] | None = None
    for item in selected:
        try:
            async with asyncio.timeout(RECONCILIATION_ATTEMPT_TIMEOUT_SEC):
                same_session = item.attempt_id.startswith(current_prefix)
                confirmation_age = (
                    tick - item.observed_tick if same_session else tick
                )
                detail = await _executor_detail(client, item.executor_id)
                pool = item.pool_address or _pick(detail, _POOL_KEYS)
                if not pool:
                    remaining.append(item)
                    continue
                owned = await _owned(client, pool)
                found = _chain_found(item, detail, owned)
                detail_status = str(detail.get("status") or "").upper()
                if found is None and item.action == "close":
                    if running_now is None:
                        running_now = await asyncio.wait_for(
                            _running_for_session(client, agent_id), timeout=2
                        )
                    if _has_newer_owned_successor(
                        item, detail, owned, running_now
                    ):
                        found = False
                historical_terminal_create = (
                    item.action == "create"
                    and item.wallet_before_usd is None
                    and detail_status in {"TERMINATED", "CLOSED", "STOPPED"}
                    and confirmation_age >= 2
                )
                # Chain absence is authoritative for a close even when an older
                # attempt predates wallet-baseline capture. Creates still need
                # both authority and wallet movement before they can be called
                # successful, except that an old terminal executor can be
                # retired as historical (not learned as success or failure).
                if found is None or (
                    item.action == "create" and item.wallet_before_usd is None
                    and not historical_terminal_create
                ):
                    remaining.append(item)
                    continue
                if item.wallet_before_usd is not None and wallet_now is None:
                    wallet_now = await asyncio.wait_for(
                        _wallet_total(client), timeout=3
                    )
                status = item.executor_status
                if not item.executor_id and not detail:
                    status = "UNKNOWN"
                # A completed stop normally reads TERMINATED on the following tick.
                # Preserve the observed SUCCESS and let chain ownership distinguish
                # a confirmed close from the known false-success orphan case.
                if detail_status and not (
                    item.action == "close"
                    and status == "SUCCESS"
                    and detail_status == "TERMINATED"
                ):
                    status = detail_status
                attempt = PositionAttempt(
                    attempt_id=item.attempt_id,
                    action=item.action,
                    pool_address=pool,
                    sleeve=item.sleeve,
                    executor_status=status,
                    chain_position_found=found,
                    wallet_delta_usd=(
                        wallet_now - item.wallet_before_usd
                        if item.wallet_before_usd is not None
                        and wallet_now is not None
                        else None
                    ),
                    error_message=item.error_message,
                    confirmation_age_ticks=confirmation_age,
                    exit_reason=item.exit_reason,
                    pnl_pct=item.pnl_pct,
                    vs_hodl_pct=item.vs_hodl_pct,
                    observed_tick=tick,
                )
                result = OutcomeLearner().observe(attempt, state)
                if result.outcome == "UNCLASSIFIED":
                    remaining.append(item)
                    continue
                store.record(attempt, result)
                state = result.state
        except Exception:
            # An authority failure preserves the pending attempt. The next tick
            # retries; absence of evidence is never recorded as a clean result.
            remaining.append(item)

    _save_pending(remaining)


async def capture(
    *,
    client: Any,
    agent_id: str,
    tick: int,
    tool_calls: list[dict[str, Any]],
    evidence_before: dict[str, Any] | None = None,
) -> None:
    evidence_before = evidence_before or {}
    wallet_before = _wallet_before(tool_calls)
    if wallet_before is None:
        raw_wallet = evidence_before.get("wallet_before_usd")
        wallet_before = float(raw_wallet) if raw_wallet is not None else None
    executor_ids_before = {
        str(value) for value in evidence_before.get("executor_ids_before", [])
    }
    running_after: list[dict[str, Any]] | None = None
    pending = _load_pending()
    known = {item.attempt_id for item in pending}

    for index, call in enumerate(tool_calls):
        selected = _is_executor_action(call)
        if selected is None:
            continue
        raw_action, call_input = selected
        action = "create" if raw_action == "create" else "close"
        call_output = call.get("output")
        pool = _pick(call_input, _POOL_KEYS) or _pick(call_output, _POOL_KEYS)
        executor_id = _pick(call_output, _EXECUTOR_KEYS) or _pick(
            call_input, _EXECUTOR_KEYS
        )
        if raw_action == "create" and not executor_id:
            if running_after is None:
                try:
                    running_after = await _running_for_agent(client, agent_id)
                except Exception:
                    running_after = []
            candidates = [
                row
                for row in running_after
                if _executor_id(row) not in executor_ids_before
                and (_pick(row, _POOL_KEYS) == pool or _pick(row, _POOL_KEYS) == "")
            ]
            if len(candidates) == 1:
                executor_id = _executor_id(candidates[0])
        detail: dict[str, Any] = {}
        if executor_id:
            try:
                detail = await _executor_detail(client, executor_id)
            except Exception:
                detail = {}
        pool = pool or _pick(detail, _POOL_KEYS)
        if not pool:
            continue
        executor_status, error = _executor_status(call, raw_action)
        attempt_id = f"host:{agent_id}:{tick}:{call.get('id') or index}"
        if attempt_id in known:
            continue
        sleeve = _sleeve(call_input)
        if raw_action == "create" and executor_id:
            _remember_sleeve(executor_id, sleeve)
        pending.append(
            PendingAttempt(
                attempt_id=attempt_id,
                action=action,
                pool_address=pool,
                sleeve=sleeve,
                executor_id=executor_id,
                executor_status=executor_status,
                wallet_before_usd=wallet_before,
                position_address=(
                    _pick(call_output, _POSITION_KEYS)
                    or _pick(detail, _POSITION_KEYS)
                ),
                error_message=error,
                observed_tick=tick,
            )
        )
        known.add(attempt_id)

    _save_pending(pending)
