"""Append-only, wallet-authoritative equity observations for Meteora sessions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agents.meteora_regime_lp.performance import normalize_executor_book


@dataclass(frozen=True)
class EquitySnapshot:
    agent_id: str
    tick: int
    timestamp: str
    wallet_usd: float
    open_lp_usd: float | None
    equity_usd: float | None
    known: bool
    missing_quotes: tuple[str, ...] = ()


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _wallet_total(state: Any) -> float:
    total = 0.0
    if not isinstance(state, dict):
        return total
    for account in state.values():
        if not isinstance(account, dict):
            continue
        for balances in account.values():
            for balance in balances or []:
                if isinstance(balance, dict):
                    total += _num(balance.get("value"))
    return total


def _open_lp_rows(executors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for executor in executors:
        status = str(executor.get("status") or "").upper()
        kind = str(executor.get("executor_type") or executor.get("type") or "")
        if status != "RUNNING" or kind != "lp_executor":
            continue
        config = executor.get("config") if isinstance(executor.get("config"), dict) else {}
        custom = (
            executor.get("custom_info")
            if isinstance(executor.get("custom_info"), dict)
            else {}
        )
        value_quote = _num(custom.get("total_value_quote"))
        if value_quote <= 0:
            value_quote = _num(custom.get("base_amount")) * _num(
                custom.get("current_price")
            ) + _num(custom.get("quote_amount"))
        if value_quote <= 0:
            value_quote = _num(config.get("total_amount_quote"))
        rows.append(
            {
                "id": str(executor.get("id") or executor.get("executor_id") or ""),
                "status": "RUNNING",
                "pair": str(
                    executor.get("trading_pair")
                    or config.get("trading_pair")
                    or ""
                ),
                "pnl": 0.0,
                "volume": value_quote,
                "fees": 0.0,
            }
        )
    return rows


def build_equity_snapshot(
    *,
    agent_id: str,
    tick: int,
    portfolio_state: Any,
    executors: Iterable[dict[str, Any]],
    timestamp: str | None = None,
) -> EquitySnapshot:
    wallet_usd = _wallet_total(portfolio_state)
    open_book = normalize_executor_book(_open_lp_rows(executors), portfolio_state)
    open_lp_usd = open_book.deployed_usd if open_book.known else None
    equity = wallet_usd + open_lp_usd if open_lp_usd is not None else None
    return EquitySnapshot(
        agent_id=agent_id,
        tick=tick,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        wallet_usd=wallet_usd,
        open_lp_usd=open_lp_usd,
        equity_usd=equity,
        known=open_book.known,
        missing_quotes=open_book.missing_quotes,
    )


def record_equity_snapshot(path: Path, snapshot: EquitySnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(asdict(snapshot), sort_keys=True) + "\n")


def session_equity_change(path: Path) -> float | None:
    if not path.exists():
        return None
    known: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line).get("equity_usd")
            if value is not None:
                known.append(float(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return known[-1] - known[0] if len(known) >= 2 else None
