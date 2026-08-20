"""Reconcile the executor registry against the chain, in both directions.

Agent-local routine for meteora_regime_lp.

Five execution-layer failures were observed on mainnet Aug 18-19, and they point
one way: **executor state is not authoritative in either direction, and neither
is the backend's position cache.** Only the chain is.

  1. create reported FAILED, position was open        (false-FAILED)
  2. stop reported SUCCESS, position stayed open  x2  (false-SUCCESS)
  3. clean-exit report that was already stale when written
  4. executor reports RUNNING for a position that does not exist (false-RUNNING)

So this routine trusts nothing and cross-checks everything:

  DISCOVERY (candidates only, never proof)
    `search_positions(status="OPEN", refresh=True)` — wallet-wide, finds pools we
    might not otherwise look at. Measured Aug 19: reported **9 OPEN when the chain
    held 1**. It does not reconcile closed positions out of its cache, so a record
    here means "worth checking", nothing more.

  AUTHORITY (what actually exists)
    `get_positions_owned(pool_address)` — a different code path, queried per pool,
    returning what the wallet really holds there. A position is real only if this
    says so.

Four outcomes, and each has a different remedy:

  HEALTHY  on-chain, and an executor is tracking it            → nothing
  ORPHAN   on-chain, no executor tracking it                   → close it (the
           executor layer cannot: terminal executors 404 out of the registry)
  GHOST    executor RUNNING, position NOT on-chain             → stop the executor,
           never "close" it — there is nothing to close, and while the ghost
           stands the sleeve believes it is full and will not re-open
  PHANTOM  in the OPEN cache but the chain denies it           → report only.
           Closing these was the trap this routine nearly walked into.

Guards, because a wrong close is worse than the bug:
  - nothing is closed without on-chain confirmation from the authority source;
  - nothing younger than `min_orphan_age_min` is auto-closed (a fresh create is
    indistinguishable from an orphan — observed at ~3 minutes);
  - unknown age is never auto-closed;
  - every close AND every ghost-stop is verified against a fresh read afterwards;
  - if the executor list cannot be read, the routine classifies nothing at all.
"""

import asyncio
import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

_TS_KEYS = (
    "created_at",
    "opened_at",
    "open_timestamp",
    "timestamp",
    "created_timestamp",
    "open_time",
    "block_time",
)
_ADDR_KEYS = ("position_address", "position", "address", "nft_address", "position_nft")
_POOL_KEYS = ("pool_address", "pool", "pool_id", "poolAddress")
# The gateway returns NO USD/value field. Confirmed from a live record (Aug 19):
#   base_token_amount / quote_token_amount / current_price / base_fee_amount /
#   quote_fee_amount / lower_price / upper_price / lower_bin_id / upper_bin_id / in_range
# Value has to be computed in QUOTE units, and is reported as such — calling a
# SOL-denominated figure "$" is how a 29% loss read as "$0.00" in this table.
_BASE_AMT_KEYS = ("base_token_amount", "base_amount")
_QUOTE_AMT_KEYS = ("quote_token_amount", "quote_amount")
_BASE_FEE_KEYS = ("base_fee_amount", "base_fees")
_QUOTE_FEE_KEYS = ("quote_fee_amount", "quote_fees")
_PRICE_KEYS = ("current_price", "price")


class Config(BaseModel):
    """Reconcile on-chain LP positions against live executors, both directions."""

    connector: str = Field(default="meteora", description="CLMM connector")
    network: str = Field(
        default="solana-mainnet-beta", description="Gateway network id"
    )
    min_orphan_age_min: float = Field(
        default=10.0,
        description="Never auto-close an orphan younger than this (minutes)",
    )
    close_orphans: bool = Field(
        default=False, description="Arm orphan closing. Default audit-only."
    )
    clear_ghosts: bool = Field(
        default=False,
        description="Stop executors whose position is not on-chain. Frees the sleeve slot.",
    )
    max_actions: int = Field(
        default=1, description="Max closes + ghost-stops in one run"
    )
    close_address: str = Field(
        default="",
        description="Close exactly this position address (still requires on-chain confirmation)",
    )
    extra_pools: list[str] = Field(
        default=[], description="Additional pool addresses to check for owned positions"
    )
    suspect_close_lookback_min: float = Field(
        default=240.0,
        description="Scan closes terminated within this many minutes for the two known "
        "upstream false-report signatures. 0 disables.",
    )
    max_phantom_rows: int = Field(
        default=5,
        description="Maximum individual phantom-cache rows to render; the full count is preserved",
    )


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pick(rec: dict, keys) -> str:
    for k in keys:
        v = rec.get(k)
        if isinstance(v, str) and len(v) > 30:
            return v
    return ""


def _first_num(rec: dict, keys) -> float:
    for k in keys:
        if rec.get(k) is not None:
            return _num(rec.get(k))
    return 0.0


def _value_quote(rec: dict) -> float:
    """Position value in QUOTE token units: base x price + quote."""
    price = _first_num(rec, _PRICE_KEYS)
    return _first_num(rec, _BASE_AMT_KEYS) * price + _first_num(rec, _QUOTE_AMT_KEYS)


def _fees_quote(rec: dict) -> float:
    """Uncollected fees in QUOTE units. For an LP position this is the product."""
    price = _first_num(rec, _PRICE_KEYS)
    return _first_num(rec, _BASE_FEE_KEYS) * price + _first_num(rec, _QUOTE_FEE_KEYS)


def _fill_state(rec: dict) -> str:
    """Which side the position is sitting in — the risk nobody was reading.

    A quote-side bid-ask that has gone 100% base has bought the whole way down
    and holds no quote at all. Observed Aug 19: fully converted, 27% below the
    lower bound, ~29% under water with a dead stop-loss, while the table showed
    "$0.00" and said nothing about it.
    """
    b = _first_num(rec, _BASE_AMT_KEYS) * _first_num(rec, _PRICE_KEYS)
    q = _first_num(rec, _QUOTE_AMT_KEYS)
    tot = b + q
    if tot <= 0:
        return "empty"
    pct = 100.0 * b / tot
    if pct >= 99.5:
        return "100% BASE (fully filled)"
    if pct <= 0.5:
        return "100% quote (unfilled)"
    return f"{pct:.0f}% base"


# ── The two upstream close reports that are wrong by construction ────────────
# Traced to source on Aug 20 (gateway v2.16.0 / hummingbot master):
#
#  (a) ZERO-PROCEEDS CLOSE. `closePosition.ts` re-fetches its transaction with a
#      single un-retried `getTransaction` immediately after confirmation. A few
#      hundred ms of RPC lag returns null, and the route answers `status: 0`
#      (PENDING) with a valid signature and NO `data` block. The Hummingbot
#      connector never reads `status` — it sees a signature, defaults every
#      missing field to 0, and books a CONFIRMED close with zero withdrawn, zero
#      fees, zero rent. The close may well have succeeded; the NUMBERS are
#      fabricated.
#
#  (b) NEVER-SENT CLOSE. `get_position_info` returns None on ANY exception (RPC
#      error, 429, timeout, bad payload). `_close_position` reads None as
#      "already closed", emits an already-closed event, marks the executor
#      COMPLETE and NEVER SENDS A CLOSE TRANSACTION. Nothing retries after
#      COMPLETE. One transient read failure turns a live position into a silent
#      orphan — this is the false-SUCCESS stop.
#
# Neither is visible in the response. Both are visible in the executor record
# afterwards, which is what this scan reads.
_PROCEEDS_KEYS = (
    "base_amount",
    "quote_amount",
    "base_fee",
    "quote_fee",
    "position_rent_refunded",
    "base_token_amount_removed",
    "quote_token_amount_removed",
    "base_fee_amount_collected",
    "quote_fee_amount_collected",
)
_CLOSE_HASH_KEYS = (
    "close_tx_hash",
    "close_signature",
    "exchange_order_id",
    "signature",
)
_ALREADY_CLOSED_MARKS = (
    "already closed",
    "already_closed",
    "skipping close",
    "not found - marking complete",
    "position may never have been created",
)


def _proceeds_report(rec: dict) -> tuple[str, dict]:
    """Classify a terminated close: OK / ZERO / NEVER_SENT / SCHEMA_UNKNOWN.

    SCHEMA_UNKNOWN is a first-class answer and is NOT a pass. If none of the
    proceeds keys are present anywhere in the record, this routine does not know
    what it is looking at, and saying so beats inventing a verdict — treating an
    absent field as 0.0 is the exact bug class this scan exists to catch.
    """
    flat: dict = {}

    def _walk(o, depth=0):
        if depth > 6 or not isinstance(o, dict):
            return
        for k, v in o.items():
            if isinstance(v, dict):
                _walk(v, depth + 1)
            elif k not in flat:
                flat[k] = v

    _walk(rec)

    blob = " ".join(str(v) for v in flat.values() if isinstance(v, str)).lower()
    if any(m in blob for m in _ALREADY_CLOSED_MARKS):
        return "NEVER_SENT", flat

    present = [k for k in _PROCEEDS_KEYS if flat.get(k) is not None]
    if not present:
        return "SCHEMA_UNKNOWN", flat
    if all(abs(_num(flat.get(k))) == 0.0 for k in present):
        return "ZERO", flat
    return "OK", flat


async def _recent_terminated(client, lookback_min: float) -> list[dict]:
    """Executors that stopped inside the lookback window, newest first."""
    import time as _t

    cutoff = _t.time() - lookback_min * 60.0
    out, cursor = [], None
    for _ in range(20):
        res = await client.executors.search_executors(
            status="TERMINATED", limit=50, cursor=cursor
        )
        page = (
            (res.get("data") or res.get("executors") or [])
            if isinstance(res, dict)
            else (res or [])
        )
        for e in page:
            if not isinstance(e, dict):
                continue
            ts = None
            for k in (
                "close_timestamp",
                "closed_at",
                "terminated_at",
                "timestamp",
                "close_time",
            ):
                v = e.get(k)
                if v is not None:
                    ts = _num(v, 0.0)
                    break
            if ts and ts > 1e11:  # milliseconds
                ts /= 1000.0
            # No timestamp = keep it. Dropping an unstamped row would silently
            # narrow the scan, which is the failure mode being guarded against.
            if ts is None or ts == 0.0 or ts >= cutoff:
                out.append(e)
        cursor = res.get("next_cursor") if isinstance(res, dict) else None
        if not cursor or not page:
            break
    return out


def _age_min(rec: dict) -> float | None:
    for k in _TS_KEYS:
        v = rec.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)) and v > 0:
            ts = float(v)
            if ts > 1e11:
                ts /= 1000.0
            return (datetime.now(timezone.utc).timestamp() - ts) / 60.0
        if isinstance(v, str) and v:
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
            except ValueError:
                continue
    return None


def _strings_in(obj, out: set, depth: int = 0) -> None:
    """Every long string in a nested structure.

    Schema-blind on purpose: the key holding an executor's position address has
    already moved between custom_info, config and top-level, so match by value
    against known addresses instead of guessing the key.
    """
    if depth > 6:
        return
    if isinstance(obj, str):
        if len(obj) > 30:
            out.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _strings_in(v, out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _strings_in(v, out, depth + 1)


async def _cached_open(client, cfg: Config) -> list[dict]:
    """Discovery only. This source overcounted 9:1 against the chain on Aug 19."""
    # No refresh=True, and a small page. This is a CANDIDATE list, never proof, so
    # paying for a full cache refresh buys nothing — and it was the main cost in a
    # routine that timed out on 37 of 75 ticks overnight. The authority read is
    # what decides truth.
    res = await client.gateway_clmm.search_positions(
        network=cfg.network,
        connector=cfg.connector,
        status="OPEN",
        limit=50,
    )
    rows = (res.get("data") or []) if isinstance(res, dict) else (res or [])
    return [r for r in rows if isinstance(r, dict)]


async def _owned_in_pool(
    client, cfg: Config, pool: str, attempts: int = 2
) -> dict[str, dict]:
    """Authority: what the wallet actually holds in ``pool``, keyed by address.

    Retried with backoff. A single transient 429 here does not degrade gracefully
    — it empties ``confirmed``, which turns every real position into "unclassified"
    and makes the routine refuse to act on the one thing it was called for.
    Observed Aug 19: this call succeeded, then failed 90 seconds later on the same
    pool, blocking the close of a position that was 29% under water.
    """
    last: Exception | None = None
    for i in range(max(1, attempts)):
        if i:
            await asyncio.sleep(1.0 * i)
        try:
            res = await client.gateway_clmm.get_positions_owned(
                connector=cfg.connector,
                network=cfg.network,
                pool_address=pool,
            )
            break
        except Exception as e:
            last = e
    else:
        raise last or RuntimeError(f"positions_owned failed for {pool}")
    rows = (res.get("data") or []) if isinstance(res, dict) else (res or [])
    out = {}
    for r in rows:
        if isinstance(r, dict):
            a = _pick(r, _ADDR_KEYS)
            if a:
                out[a] = r
    return out


async def _running_executors(client) -> list[dict]:
    rows, cursor = [], None
    for _ in range(20):
        res = await client.executors.search_executors(
            status="RUNNING", limit=50, cursor=cursor
        )
        page = (
            (res.get("data") or res.get("executors") or [])
            if isinstance(res, dict)
            else (res or [])
        )
        rows.extend([e for e in page if isinstance(e, dict)])
        cursor = res.get("next_cursor") if isinstance(res, dict) else None
        if not cursor or not page:
            break
    return rows


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "orphan_guard: no server available — cannot reach the gateway."

    # 1. Executors first. Without them nothing can be classified, in either
    #    direction, so an unreadable list stops the routine rather than turning
    #    every position into an orphan.
    try:
        execs = await _running_executors(client)
    except Exception as e:
        return (
            f"orphan_guard: could not read RUNNING executors ({e}). REFUSING to classify "
            f"or act — an unreadable executor list makes every position look orphaned."
        )

    ex_strings: dict[str, set] = {}
    for ex in execs:
        s: set = set()
        _strings_in(ex, s)
        ex_strings[str(ex.get("id") or ex.get("executor_id") or "?")] = s

    # 2. Candidate pools: every pool an executor references, plus anything the
    #    (unreliable) cache mentions, plus operator-supplied extras.
    try:
        cached = await _cached_open(client, config)
    except Exception as e:
        logger.info(
            f"orphan_guard: cache discovery failed ({e}) — continuing with executor pools only"
        )
        cached = []

    pools: set = set(p for p in config.extra_pools if p)
    for ex in execs:
        cfgd = ex.get("config") if isinstance(ex.get("config"), dict) else ex
        ci = ex.get("custom_info") if isinstance(ex.get("custom_info"), dict) else {}
        for src in (cfgd, ci, ex):
            p = _pick(src, _POOL_KEYS)
            if p:
                pools.add(p)
                break
    for rec in cached:
        p = _pick(rec, _POOL_KEYS)
        if p:
            pools.add(p)

    if not pools:
        return (
            f"orphan_guard: {len(execs)} RUNNING executor(s), but no pool address could be "
            f"resolved from any of them or from the position cache — cannot confirm anything "
            f"on-chain. Reporting nothing rather than guessing."
        )

    # 3. Authority read, per pool.
    pool_list = sorted(pools)
    results = await asyncio.gather(
        *[_owned_in_pool(client, config, p) for p in pool_list], return_exceptions=True
    )
    confirmed: dict[str, dict] = {}
    readable: set = set()
    unreadable_pools: set = set()
    for pool, res in zip(pool_list, results):
        if isinstance(res, Exception):
            unreadable_pools.add(pool)
            logger.info(f"orphan_guard: positions_owned failed for {pool}: {res}")
        else:
            readable.add(pool)
            confirmed.update(res)
    unreadable = [f"{p[:8]}…" for p in sorted(unreadable_pools)]

    # FAIL CLOSED on the authority side too. A GHOST is "executor with no on-chain
    # position", which is only knowable if the pool that executor references was
    # actually readable. When every pool errored, `confirmed` is empty and EVERY
    # executor looks like a ghost — and with clear_ghosts armed the routine stops
    # them all. That happened live on Aug 20 tick 75: positions_owned failed for
    # all 5 pools, both healthy executors were classified as ghosts, and a real
    # position was left unmanaged. The executor-list guard existed; this mirror
    # guard did not.
    if not readable:
        return (
            f"orphan_guard: the authority read (positions_owned) FAILED for all "
            f"{len(pool_list)} pool(s). REFUSING to classify orphans or ghosts — with no "
            f"on-chain truth, every executor looks like a ghost and every cached record "
            f"looks like an orphan. {len(execs)} RUNNING executor(s) left untouched. Retry "
            f"next tick; treat every pool as UNCLASSIFIED, not clean, and open nothing new."
        )

    # 4. Classify.
    rows, orphans, ghosts, phantoms = [], [], [], []

    tracked_addrs: set = set()
    unclassified_execs = []
    for eid, strings in ex_strings.items():
        hit = strings & set(confirmed)
        if hit:
            tracked_addrs |= hit
            continue
        # No match — but "no match" only means GHOST if we could actually read the
        # pool(s) this executor points at. If any of them errored, we simply do not
        # know, and not-knowing must never authorise stopping a live executor.
        if strings & unreadable_pools:
            unclassified_execs.append(eid)
        else:
            ghosts.append({"executor_id": eid, "strings": strings})

    for addr, rec in confirmed.items():
        age = _age_min(rec)
        is_tracked = addr in tracked_addrs
        if not is_tracked:
            orphans.append({"addr": addr, "age": age, "rec": rec})
        rows.append(
            {
                "Position": f"{addr[:8]}…{addr[-4:]}",
                "State": "healthy" if is_tracked else "ORPHAN",
                "Pool": (_pick(rec, _POOL_KEYS) or "?")[:8],
                "Age(min)": "?" if age is None else round(age, 1),
                "InRange": rec.get("in_range"),
                "Value(quote)": f"{_value_quote(rec):,.6g}",
                "Fees(quote)": f"{_fees_quote(rec):,.6g}",
                "Fill": _fill_state(rec),
                "Address": addr,
            }
        )

    for rec in cached:
        addr = _pick(rec, _ADDR_KEYS)
        if addr and addr not in confirmed:
            phantoms.append(addr)
            if len(phantoms) <= max(0, config.max_phantom_rows):
                rows.append(
                    {
                        "Position": f"{addr[:8]}…{addr[-4:]}",
                        "State": "phantom-cache",
                        "Pool": (_pick(rec, _POOL_KEYS) or "?")[:8],
                        "Age(min)": (
                            "?" if _age_min(rec) is None else round(_age_min(rec), 1)
                        ),
                        "InRange": rec.get("in_range"),
                        "Value(quote)": f"{_value_quote(rec):,.6g}",
                        "Fees(quote)": f"{_fees_quote(rec):,.6g}",
                        "Fill": _fill_state(rec),
                        "Address": addr,
                    }
                )

    for g in ghosts:
        rows.append(
            {
                "Position": "—",
                "State": "GHOST(exec)",
                "Pool": "—",
                "Age(min)": "—",
                "InRange": "—",
                "Value(quote)": "—",
                "Fees(quote)": "—",
                "Fill": "—",
                "Address": g["executor_id"],
            }
        )

    head = (
        f"orphan_guard: chain says {len(confirmed)} position(s) across {len(pools)} pool(s); "
        f"{len(execs)} RUNNING executor(s) → {len(orphans)} orphan(s), {len(ghosts)} ghost(s), "
        f"{len(phantoms)} phantom cache record(s)."
    )
    notes = []

    # 4b. Suspect closes. Every one of these ALREADY reported success to the
    #     agent; the point is to catch them after the fact, because nothing in
    #     the response distinguishes them from a clean exit.
    suspect_zero, suspect_never, suspect_unknown = [], [], []
    if config.suspect_close_lookback_min > 0:
        try:
            for ex in await _recent_terminated(
                client, config.suspect_close_lookback_min
            ):
                verdict, flat = _proceeds_report(ex)
                if verdict == "OK":
                    continue
                eid = str(ex.get("id") or ex.get("executor_id") or "?")
                pair = str(flat.get("trading_pair") or flat.get("pair") or "?")
                addr = _pick(flat, _ADDR_KEYS)
                entry = {"executor_id": eid, "pair": pair, "address": addr}
                if verdict == "ZERO":
                    suspect_zero.append(entry)
                elif verdict == "NEVER_SENT":
                    suspect_never.append(entry)
                else:
                    suspect_unknown.append(entry)
        except Exception as e:
            notes.append(
                f"❓ suspect-close scan could not read terminated executors ({e}). Recent "
                f"closes are UNVERIFIED this tick, not clean."
            )

    if suspect_never:
        addrs = (
            ", ".join(e["address"][:8] + "…" for e in suspect_never if e["address"])
            or "address not recorded"
        )
        notes.append(
            f"🚨 {len(suspect_never)} recent close(s) reported 'already closed / position not "
            f"found' and were marked COMPLETE — upstream, that path NEVER SENDS A CLOSE "
            f"TRANSACTION. `get_position_info` returns None on any exception (RPC error, 429, "
            f"timeout) and the executor reads None as 'already closed'. Nothing retries after "
            f"COMPLETE. Assume the position is STILL ON-CHAIN until positions_owned says "
            f"otherwise: {addrs}. Journal as CLOSE ATTEMPTED (UNVERIFIED), never as closed."
        )
    if suspect_zero:
        pairs = ", ".join(f"{e['pair']}({e['executor_id'][:8]}…)" for e in suspect_zero)
        notes.append(
            f"🚨 {len(suspect_zero)} recent close(s) booked EVERY proceeds figure as zero — "
            f"withdrawn, fees and rent all 0. That is the gateway's `status: 0` artifact: it "
            f"returns a signature with no data when its single un-retried getTransaction "
            f"misses, and the connector defaults the missing fields to 0 without reading "
            f"`status`. The close may have succeeded; the NUMBERS ARE FABRICATED. Do not "
            f"report them as P&L — get the real figures from meteora_truth: {pairs}."
        )
    if suspect_unknown:
        notes.append(
            f"❓ {len(suspect_unknown)} recent close(s) carried none of the proceeds fields this "
            f"routine knows, so it cannot tell a clean exit from a zero-proceeds artifact. That "
            f"is 'not checked', not 'fine' — verify those closes against meteora_truth before "
            f"quoting anything about them."
        )

    if phantoms:
        omitted = max(0, len(phantoms) - max(0, config.max_phantom_rows))
        notes.append(
            f"⚠️ {len(phantoms)} record(s) appear OPEN in the position cache but the chain "
            f"does not hold them — cache artifacts, NOT orphans. Never closed. "
            + (
                f"Only {config.max_phantom_rows} shown; {omitted} repetitive rows omitted."
                if omitted
                else ""
            )
        )
    if unreadable:
        notes.append(
            f"❓ positions_owned failed for pool(s) {', '.join(unreadable)} — anything there is "
            f"unclassified, not clean. Open nothing new in those pools this tick."
        )
    if unclassified_execs:
        notes.append(
            f"🛑 {len(unclassified_execs)} executor(s) NOT classified because the pool they "
            f"reference was unreadable. They are NOT ghosts and were not touched — "
            f"'we could not check' is not 'it does not exist'."
        )

    # 5. Act — orphans (close) and ghosts (stop), bounded together.
    budget = max(0, int(config.max_actions))
    acted = []

    to_close = []
    if config.close_address:
        m = next((o for o in orphans if o["addr"] == config.close_address), None)
        if m:
            to_close = [m]
        else:
            acted.append(
                f"⚠️ close_address {config.close_address[:8]}… is not a CONFIRMED orphan "
                f"(either tracked, not on-chain, or in an unreadable pool) — refusing."
            )
    elif config.close_orphans:
        eligible = [
            o
            for o in orphans
            if o["age"] is not None and o["age"] >= config.min_orphan_age_min
        ]
        if any(o["age"] is None for o in orphans):
            acted.append(
                "❓ orphan(s) with unreadable age left alone — name one via close_address."
            )
        if any(
            o["age"] is not None and o["age"] < config.min_orphan_age_min
            for o in orphans
        ):
            acted.append(
                f"⏳ orphan(s) under {config.min_orphan_age_min:g} min left alone — a fresh create looks identical."
            )
        to_close = eligible[:budget]

    for o in to_close:
        if budget <= 0:
            break
        budget -= 1
        addr = o["addr"]
        pool = _pick(o["rec"], _POOL_KEYS)
        try:
            await client.gateway_clmm.close_position(
                connector=config.connector,
                network=config.network,
                position_address=addr,
            )
        except Exception as e:
            acted.append(
                f"❌ close FAILED for {addr[:8]}…: {e} — may still be open, re-run."
            )
            continue
        try:
            still = await _owned_in_pool(client, config, pool) if pool else {}
            acted.append(
                f"❌ close reported SUCCESS but {addr[:8]}… is STILL held on-chain — false-success, escalate."
                if addr in still
                else f"✅ closed {addr[:8]}… — confirmed gone from the pool's owned-positions read."
            )
        except Exception as e:
            acted.append(
                f"⚠️ closed {addr[:8]}… but could NOT verify ({e}) — unconfirmed, re-run."
            )

    if config.clear_ghosts:
        for g in ghosts:
            if budget <= 0:
                break
            budget -= 1
            eid = g["executor_id"]
            try:
                await client.executors.stop_executor(eid)
            except Exception as e:
                acted.append(f"❌ ghost stop FAILED for executor {eid[:8]}…: {e}")
                continue
            try:
                still_running = any(
                    str(e2.get("id") or e2.get("executor_id")) == eid
                    for e2 in await _running_executors(client)
                )
                acted.append(
                    f"❌ ghost executor {eid[:8]}… still RUNNING after stop — escalate."
                    if still_running
                    else f"✅ ghost executor {eid[:8]}… stopped; its sleeve slot is free again."
                )
            except Exception as e:
                acted.append(f"⚠️ stopped ghost {eid[:8]}… but could NOT verify ({e}).")
    elif ghosts:
        notes.append(
            f"👻 {len(ghosts)} executor(s) RUNNING with no on-chain position. Until cleared, the "
            f"sleeve believes those slots are full and will not re-open. Re-run with "
            f"clear_ghosts=true to stop them."
        )

    if not (orphans or ghosts or phantoms):
        if suspect_zero or suspect_never or suspect_unknown:
            notes.append(
                "Chain and executor registry agree on OPEN positions — but the recent closes "
                "flagged above are not clean, and a consistent live book says nothing about "
                "what the closed ones actually returned."
            )
        else:
            notes.append("Chain and executor registry agree. Book is consistent.")
    elif not (config.close_orphans or config.clear_ghosts or config.close_address):
        notes.append(
            "AUDIT ONLY — arm with close_orphans / clear_ghosts. Journal each finding first."
        )

    summary = " ".join([head] + notes + acted)
    columns = [
        "Position",
        "State",
        "Pool",
        "Age(min)",
        "InRange",
        "Value(quote)",
        "Fees(quote)",
        "Fill",
        "Address",
    ]

    try:
        from condor.reports import ReportBuilder

        b = ReportBuilder("Orphan Guard — chain vs executor registry")
        b.source("routine", "orphan_guard").tags(
            ["lp", "meteora", "orphan", "ghost", "recovery"]
        )
        b.kpi("On-chain", str(len(confirmed)))
        b.kpi("Executors", str(len(execs)))
        b.kpi("Orphans", str(len(orphans)))
        b.kpi("Ghosts", str(len(ghosts)))
        b.kpi(
            "Suspect closes",
            str(len(suspect_zero) + len(suspect_never) + len(suspect_unknown)),
        )
        b.kpi("Phantom cache", str(len(phantoms)))
        b.markdown(summary)
        b.table(rows, columns)
        b.manual_order()
        await b.save()
    except Exception as e:
        logger.info(f"orphan_guard: report save skipped: {e}")

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(
                f"{r['Position']} | {r['State']} | pool {r['Pool']} | age {r['Age(min)']} "
                f"| inRange {r['InRange']} | val {r['Value(quote)']} | fees {r['Fees(quote)']} "
                f"| {r['Fill']} | {r['Address']}"
            )
        return "\n".join(lines)
