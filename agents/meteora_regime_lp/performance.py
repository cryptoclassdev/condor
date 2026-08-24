"""USD-normalized performance accounting for a mixed-quote Meteora book."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from agents.meteora_regime_lp.lifecycle import SOL_MINT, USDC_MINT
from condor.fetchers.executors import executor_quote

_STABLE_QUOTES = {"USD", "USDC", "USDT", USDC_MINT.upper()}
_ALIASES = {
    SOL_MINT.upper(): "SOL",
    USDC_MINT.upper(): "USDC",
    "WSOL": "SOL",
}


@dataclass(frozen=True)
class NormalizedBook:
    known: bool
    realized_pnl_usd: float | None
    unrealized_pnl_usd: float | None
    total_pnl_usd: float | None
    deployed_usd: float | None
    fees_usd: float | None
    by_quote: dict[str, dict[str, float | None]]
    missing_quotes: tuple[str, ...]


def _portfolio_rates(state: Any) -> dict[str, float]:
    rates: dict[str, float] = {quote: 1.0 for quote in _STABLE_QUOTES}
    if not isinstance(state, dict):
        return rates
    for account in state.values():
        if not isinstance(account, dict):
            continue
        for balances in account.values():
            for balance in balances or []:
                if not isinstance(balance, dict):
                    continue
                token = str(balance.get("token") or "").upper()
                if not token:
                    continue
                try:
                    price = float(balance.get("price") or 0)
                    units = float(balance.get("units") or 0)
                    value = float(balance.get("value") or 0)
                except (TypeError, ValueError):
                    continue
                rate = price if price > 0 else abs(value / units) if units else 0.0
                if rate > 0:
                    rates[token] = rate
    for raw, alias in _ALIASES.items():
        if alias in rates:
            rates[raw] = rates[alias]
    return rates


def normalize_executor_book(
    rows: Iterable[dict[str, Any]], portfolio_state: Any
) -> NormalizedBook:
    """Convert every executor metric from its own quote into USD.

    Missing conversion evidence makes all USD totals unknown.  Raw per-quote
    buckets remain available for diagnosis; incompatible currency units are
    never silently added.
    """
    rates = _portfolio_rates(portfolio_state)
    buckets: dict[str, dict[str, float | None]] = {}
    realized = unrealized = deployed = fees = 0.0
    missing: set[str] = set()

    for row in rows:
        quote_raw = executor_quote(str(row.get("pair") or ""))
        quote = _ALIASES.get(quote_raw, quote_raw)
        rate = rates.get(quote_raw) or rates.get(quote)
        bucket = buckets.setdefault(
            quote,
            {
                "pnl": 0.0,
                "deployed": 0.0,
                "fees": 0.0,
                "rate_usd": rate,
            },
        )
        pnl = float(row.get("pnl") or 0)
        volume = float(row.get("volume") or 0)
        fee = float(row.get("fees") or 0)
        bucket["pnl"] = float(bucket["pnl"] or 0) + pnl
        bucket["deployed"] = float(bucket["deployed"] or 0) + volume
        bucket["fees"] = float(bucket["fees"] or 0) + fee
        if not rate:
            missing.add(quote)
            continue
        converted_pnl = pnl * rate
        if str(row.get("status") or "").upper() == "RUNNING":
            unrealized += converted_pnl
        else:
            realized += converted_pnl
        deployed += volume * rate
        fees += fee * rate

    if missing:
        return NormalizedBook(
            known=False,
            realized_pnl_usd=None,
            unrealized_pnl_usd=None,
            total_pnl_usd=None,
            deployed_usd=None,
            fees_usd=None,
            by_quote=buckets,
            missing_quotes=tuple(sorted(missing)),
        )
    return NormalizedBook(
        known=True,
        realized_pnl_usd=realized,
        unrealized_pnl_usd=unrealized,
        total_pnl_usd=realized + unrealized,
        deployed_usd=deployed,
        fees_usd=fees,
        by_quote=buckets,
        missing_quotes=(),
    )
