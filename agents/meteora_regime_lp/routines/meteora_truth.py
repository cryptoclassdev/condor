"""Authoritative P&L straight from Meteora, independent of Hummingbot entirely.

Agent-local routine for meteora_regime_lp.

Every performance figure this agent has reported so far comes from the executor
layer, and that layer has now been caught lying four distinct ways:

  - "volume" was the quote DEPOSITED, fixed at open, never traded          (Aug 19)
  - the position cache reported 9 OPEN positions when the chain held 1     (Aug 19)
  - an executor reported RUNNING, with PnL, for a position that did not exist (Aug 20)
  - a closed position's proceeds returned as base tokens the wallet read could
    not see, hiding 28% of the book                                        (Aug 20)

Meteora publishes its own DLMM data API, which computes position P&L on the fly
from chain state and knows nothing about our executors. It is a genuinely
independent third source, and it is the sponsor's own accounting:

  GET /portfolio/total?user=<wallet>            whole book, one call
  GET /positions/<pool>/pnl?user=<wallet>       per position, with lifecycle totals

Use it as the SOURCE OF RECORD for anything reported as performance. The
executor layer remains useful for *operating* positions; it is not evidence of
what they earned.

It also makes the honest LP benchmark computable for the first time. Fees are
not profit: the question is whether providing liquidity beat simply holding the
tokens you deposited. This routine computes both from `allTimeDeposits` priced
at current spot, and reports the difference as VS_HODL.

NOTE ON SCHEMA: field names here follow Meteora's published API reference. The
first live run should be treated as a schema check — every field is extracted
defensively and anything missing is reported as UNAVAILABLE rather than silently
becoming zero. A zero that means "not found" is exactly the class of bug this
routine exists to eliminate.
"""

import asyncio
import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

PROD_HOST = "https://dlmm.datapi.meteora.ag"

_POOL_KEYS = ("pool_address", "pool", "pool_id", "poolAddress")


class Config(BaseModel):
    """Authoritative position/portfolio P&L from Meteora's own DLMM data API."""

    wallet: str = Field(
        description="Wallet address to query. REQUIRED — read it from your wallet check."
    )
    pools: list[str] = Field(
        default=[],
        description="Pool addresses for per-position detail. Empty = derive from RUNNING executors.",
    )
    status: str = Field(default="all", description="open | closed | all")
    host: str = Field(default=PROD_HOST, description="Data API base host")
    timeout_s: float = Field(default=20.0, description="Per-request timeout")


def _num(v, default=None):
    """Parse a number. Returns ``default`` (None) when absent — never 0.0."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fmt(v, prefix="$", dp=4):
    return "UNAVAILABLE" if v is None else f"{prefix}{v:,.{dp}f}"


def _pick(rec: dict, keys):
    for k in keys:
        v = rec.get(k)
        if isinstance(v, str) and len(v) > 30:
            return v
    return ""


def _usd_of(bucket) -> float | None:
    """USD total out of an allTimeDeposits/Withdrawals/Fees bucket."""
    if not isinstance(bucket, dict):
        return None
    for k in ("usd", "totalUsd", "usdTotal", "totalUSD", "amountUsd", "valueUsd"):
        v = _num(bucket.get(k))
        if v is not None:
            return v
    return None


def _pair_amounts(bucket) -> tuple[float | None, float | None]:
    """(tokenX, tokenY) amounts out of a lifecycle bucket."""
    if not isinstance(bucket, dict):
        return None, None
    x = _num(bucket.get("tokenX") if not isinstance(bucket.get("tokenX"), dict)
             else bucket.get("tokenX", {}).get("amount"))
    y = _num(bucket.get("tokenY") if not isinstance(bucket.get("tokenY"), dict)
             else bucket.get("tokenY", {}).get("amount"))
    return x, y


async def _get(session, url, params, timeout):
    async with session.get(
        url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} from {url}: {body[:200]}")
        import json as _json
        return _json.loads(body)


async def _running_pools(client) -> set:
    """Pool addresses referenced by RUNNING executors."""
    pools: set = set()
    if not client:
        return pools
    try:
        res = await client.executors.search_executors(status="RUNNING", limit=50)
        rows = (res.get("data") or res.get("executors") or []) if isinstance(res, dict) else (res or [])
        for ex in rows:
            if not isinstance(ex, dict):
                continue
            cfg = ex.get("config") if isinstance(ex.get("config"), dict) else ex
            ci = ex.get("custom_info") if isinstance(ex.get("custom_info"), dict) else {}
            for src in (cfg, ci, ex):
                p = _pick(src, _POOL_KEYS)
                if p:
                    pools.add(p)
                    break
    except Exception as e:
        logger.info(f"meteora_truth: could not read executors for pool discovery: {e}")
    return pools


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    wallet = (config.wallet or "").strip()
    if len(wallet) < 30:
        return (
            "meteora_truth: a valid `wallet` address is required. Read it from your wallet "
            "check and pass it explicitly — this routine will not guess."
        )

    client = await get_client(context._chat_id, context=context)
    pools = set(p for p in config.pools if p) or await _running_pools(client)

    host = config.host.rstrip("/")
    notes, rows = [], []
    total_line = "UNAVAILABLE"

    async with aiohttp.ClientSession() as session:
        # ── whole-book truth ──
        try:
            tot = await _get(session, f"{host}/portfolio/total", {"user": wallet}, config.timeout_s)
            pnl_usd = _num(tot.get("totalPnlUsd"))
            pnl_sol = _num(tot.get("totalPnlSol"))
            pct = _num(tot.get("totalPnlPctChange"))
            pct_sol = _num(tot.get("totalPnlSolPctChange"))
            total_line = (
                f"BOOK P&L (Meteora, authoritative): {_fmt(pnl_usd, '$', 4)} USD "
                f"({_fmt(pct, '', 3)}%) · {_fmt(pnl_sol, '', 6)} SOL ({_fmt(pct_sol, '', 3)}%)"
            )
        except Exception as e:
            notes.append(
                f"❌ /portfolio/total failed ({e}). Book-level P&L is UNKNOWN this run — "
                f"do NOT fall back to the executor layer's figure and report it as P&L."
            )

        # ── per-position detail ──
        if not pools:
            notes.append(
                "No pools to detail (none supplied and no RUNNING executors referenced one). "
                "Book total above still stands."
            )
        for pool in sorted(pools):
            try:
                data = await _get(
                    session, f"{host}/positions/{pool}/pnl",
                    {"user": wallet, "status": config.status, "page_size": 100},
                    config.timeout_s,
                )
            except Exception as e:
                notes.append(f"❓ pool {pool[:8]}… detail unavailable ({e}) — unclassified, not empty.")
                continue

            px = _num(data.get("tokenXPrice"))
            py = _num(data.get("tokenYPrice"))
            for p in (data.get("positions") or []):
                if not isinstance(p, dict):
                    continue
                dep = p.get("allTimeDeposits")
                wdr = p.get("allTimeWithdrawals")
                fee = p.get("allTimeFees")
                dep_usd, wdr_usd, fee_usd = _usd_of(dep), _usd_of(wdr), _usd_of(fee)
                unreal = _usd_of(p.get("unrealizedPnl"))

                # VS_HODL: what the deposited basket would be worth at today's spot,
                # against what the position actually returned. Fees are income, not
                # profit — this is the only benchmark that answers "was providing
                # liquidity better than just holding?"
                dx, dy = _pair_amounts(dep)
                hodl = None
                vs_hodl = None
                if None not in (dx, dy, px, py):
                    hodl = dx * px + dy * py
                    realised = sum(v for v in (wdr_usd, fee_usd, unreal) if v is not None)
                    if wdr_usd is not None or unreal is not None:
                        vs_hodl = realised - hodl

                rows.append({
                    "Position": (p.get("positionAddress") or "?")[:8] + "…",
                    "Status": "closed" if p.get("isClosed") else
                              ("OUT_OF_RANGE" if p.get("isOutOfRange") else "in-range"),
                    "PnL($)": _fmt(_num(p.get("pnlUsd")), "$", 4),
                    "PnL(%)": _fmt(_num(p.get("pnlPctChange")), "", 3),
                    "Deposited($)": _fmt(dep_usd, "$", 2),
                    "Withdrawn($)": _fmt(wdr_usd, "$", 2),
                    "Fees($)": _fmt(fee_usd, "$", 4),
                    "Unrealized($)": _fmt(unreal, "$", 4),
                    "HODL($)": _fmt(hodl, "$", 2),
                    "vsHODL($)": _fmt(vs_hodl, "$", 4),
                    "Fee/TVL24h": _fmt(_num(p.get("feePerTvl24h")), "", 4),
                })

    if not rows and total_line == "UNAVAILABLE":
        return "meteora_truth: no data returned. " + " ".join(notes)

    beat = [r for r in rows if r["vsHODL($)"] != "UNAVAILABLE" and not r["vsHODL($)"].startswith("$-")]
    scored = [r for r in rows if r["vsHODL($)"] != "UNAVAILABLE"]

    parts = [
        total_line,
        f"{len(rows)} position(s) across {len(pools)} pool(s).",
        "This is Meteora's own on-the-fly calculation from chain state and owes nothing to "
        "the executor layer. **Report performance from THIS, not from CORE DATA.** The "
        "executor layer is for operating positions, not for evidence of what they earned.",
    ]
    if scored:
        parts.append(
            f"VS_HODL: {len(beat)}/{len(scored)} position(s) beat simply holding the deposited "
            f"basket at today's spot. Fees are income, not profit — a position that earned fees "
            f"and still lost to HODL did not work."
        )
    if any(r["vsHODL($)"] == "UNAVAILABLE" for r in rows):
        parts.append(
            "Some vsHODL figures are UNAVAILABLE because a deposit/price field was absent — "
            "that is 'not computed', never zero. Treat it as missing, and say so if you quote it."
        )
    parts.extend(notes)
    summary = " ".join(parts)

    columns = ["Position", "Status", "PnL($)", "PnL(%)", "Deposited($)", "Withdrawn($)",
               "Fees($)", "Unrealized($)", "HODL($)", "vsHODL($)", "Fee/TVL24h"]

    try:
        from condor.reports import ReportBuilder
        b = ReportBuilder("Meteora Truth — authoritative P&L from the DLMM data API")
        b.source("routine", "meteora_truth").tags(["lp", "meteora", "pnl", "ground-truth"])
        b.kpi("Positions", str(len(rows)))
        b.kpi("Pools", str(len(pools)))
        if scored:
            b.kpi("Beat HODL", f"{len(beat)}/{len(scored)}")
        b.markdown(summary)
        if rows:
            b.table(rows, columns)
        b.manual_order()
        await b.save()
    except Exception as e:
        logger.info(f"meteora_truth: report save skipped: {e}")

    try:
        from routines.base import RoutineResult
        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(" | ".join(f"{c} {r[c]}" for c in columns))
        return "\n".join(lines)
