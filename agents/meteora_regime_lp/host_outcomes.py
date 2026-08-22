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
from pathlib import Path
from typing import Any

from agents.meteora_regime_lp.outcome_learning import (
    FileLearningStore,
    OutcomeLearner,
    PositionAttempt,
)

STORE_ROOT = Path(__file__).resolve().parent / "store" / "outcome_learning"
PENDING_PATH = STORE_ROOT / "pending.json"
MAX_RECONCILIATIONS_PER_TICK = 2
RECONCILIATION_ATTEMPT_TIMEOUT_SEC = 10

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


async def prepare(*, client: Any, agent_id: str, tick: int) -> dict[str, Any]:
    """Record authority baselines before the model can submit an action."""
    del tick
    running = await _running_for_agent(client, agent_id)
    return {
        "wallet_before_usd": await _wallet_total(client),
        "executor_ids_before": sorted(filter(None, map(_executor_id, running))),
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

    wallet_now = await asyncio.wait_for(_wallet_total(client), timeout=5)
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

    for item in selected:
        try:
            async with asyncio.timeout(RECONCILIATION_ATTEMPT_TIMEOUT_SEC):
                detail = await _executor_detail(client, item.executor_id)
                pool = item.pool_address or _pick(detail, _POOL_KEYS)
                if not pool:
                    remaining.append(item)
                    continue
                owned = await _owned(client, pool)
                found = _chain_found(item, detail, owned)
                if found is None or item.wallet_before_usd is None:
                    remaining.append(item)
                    continue
                status = item.executor_status
                detail_status = str(detail.get("status") or "").upper()
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
                    wallet_delta_usd=wallet_now - item.wallet_before_usd,
                    error_message=item.error_message,
                    confirmation_age_ticks=tick - item.observed_tick,
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
        pending.append(
            PendingAttempt(
                attempt_id=attempt_id,
                action=action,
                pool_address=pool,
                sleeve=_sleeve(call_input),
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
