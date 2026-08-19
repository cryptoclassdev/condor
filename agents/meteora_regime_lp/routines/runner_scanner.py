"""Scan NEW Meteora pools for runner plays ranked by 5-MINUTE volume.

Agent-local routine for meteora_regime_lp — the RISK ("runner") sleeve scanner.
Bot-feasible adaptation of the LP Army Heart-Attack / Rabbit / Ape-In family:
the edge is a fresh token with exploding short-horizon volume; the discipline
is hard gates and volume-scaled sizing.

Sources GeckoTerminal new_pools + trending (raw API, m5/h1 volume windows),
filters to Meteora SOL-quoted pools with:
  - pool age within [min_age_hours, max_age_hours]  (skip the launch candle,
    skip the dead)
  - m5 volume >= min_m5_vol_usd AND m5 >= accel_ratio * (h1/12)
    (accelerating flow — Rabbit rule: volume is everything)
  - TVL within [min_tvl_usd, max_tvl_usd]  (real pool, but small enough to run)
then enriches survivors with on-chain pool-info (bin_step, price, mints) and
ranks by m5 turnover (m5 volume / TVL). Returns MintPair/BaseMint plus a
suggested SIZE TIER per the volume ladder:
  m5 >= full_size_m5   -> FULL runner unit
  m5 >= 0.4*full       -> 2/3 unit
  else                 -> 1/3 probe

Run token_safety_check on the BaseMint BEFORE any entry — this scanner does
liquidity/volume gates only, not honeypot/authority gates.
"""

import logging
import asyncio
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
GECKO_NETWORK = "solana"
CLMM_NETWORK = "solana-mainnet-beta"
VENUE = "meteora"
SOL_MINT = "So11111111111111111111111111111111111111112"


class Config(BaseModel):
    """Rank fresh Meteora pools by 5-minute volume for the runner sleeve."""

    top_n: int = Field(default=5, description="Ranked candidates to return")
    min_age_hours: float = Field(default=1.0, description="Min pool age (skip launch candle)")
    max_age_hours: float = Field(default=48.0, description="Max pool age (runners are young)")
    min_m5_vol_usd: float = Field(default=8000.0, description="Min 5-minute volume in USD")
    accel_ratio: float = Field(
        default=3.0,
        description="m5 must be >= this multiple of the average 5-min slice of h1 (m5 >= ratio*h1/12)",
    )
    min_tvl_usd: float = Field(default=10000.0, description="Min TVL")
    max_tvl_usd: float = Field(default=500000.0, description="Max TVL (small enough to run)")
    full_size_m5: float = Field(default=50000.0, description="m5 USD volume that earns a FULL runner unit")
    exclude_pools: list[str] = Field(default=[], description="Held/blocked pool addresses")
    exclude_mints: list[str] = Field(default=[], description="Held/blocked base mints")


async def _gecko_get(session, path, params=None):
    headers = {"Accept": "application/json;version=20230302"}
    async with session.get(
        f"{GECKO_BASE}/{path}", headers=headers, params=params,
        timeout=aiohttp.ClientTimeout(total=25),
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GeckoTerminal {path} -> HTTP {resp.status}")
        return await resp.json()


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _age_hours(created_at: str | None) -> float | None:
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except ValueError:
        return None


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)

    raw = []
    try:
        async with aiohttp.ClientSession() as session:
            # Fresh Meteora pools are sparse in network-wide feeds.
            # new_pools is the ONLY genuinely young feed, but it is network-wide,
            # so most of it is not Meteora — page it as deep as the API allows.
            # The venue list is sorted two ways on purpose: by 24h volume (which
            # skews old and large) and by 24h tx count, where a young, busy pool
            # ranks even when its 24h volume is still small because it has only
            # existed for two hours.
            tasks = [
                _gecko_get(session, f"networks/{GECKO_NETWORK}/new_pools", {"page": p})
                for p in range(1, 11)
            ] + [
                _gecko_get(session, f"networks/{GECKO_NETWORK}/dexes/{VENUE}/pools",
                           {"page": p, "sort": "h24_volume_usd_desc"})
                for p in range(1, 4)
            ] + [
                _gecko_get(session, f"networks/{GECKO_NETWORK}/dexes/{VENUE}/pools",
                           {"page": p, "sort": "h24_tx_count_desc"})
                for p in range(1, 4)
            ] + [_gecko_get(session, f"networks/{GECKO_NETWORK}/trending_pools")]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"runner_scanner: source failed: {r}")
                continue
            raw.extend(r.get("data", []) or [])
    except Exception as e:
        return f"runner_scanner: failed to reach GeckoTerminal: {e}"

    excl_pools = set(config.exclude_pools)
    excl_mints = set(config.exclude_mints)
    seen, candidates = set(), []
    stats = {"not_meteora": 0, "age_old": 0, "age_young": 0, "age_unknown": 0,
             "vol": 0, "accel": 0, "tvl": 0, "not_sol": 0, "excluded": 0}

    for p in raw:
        attrs = p.get("attributes", {}) or {}
        rel = p.get("relationships", {}) or {}
        addr = attrs.get("address") or ""
        if not addr or addr in seen or addr in excl_pools:
            continue
        dex = ((rel.get("dex") or {}).get("data") or {}).get("id") or ""
        if VENUE not in dex:
            stats["not_meteora"] += 1
            continue
        # Split three ways deliberately. A single "age" counter conflated "this
        # pool is too old", "too new" and "the feed did not tell us when it was
        # created" — and the third is a data problem masquerading as a market
        # observation. If age_unknown dominates, the venue feed is not returning
        # pool_created_at and the age gate is rejecting pools it never measured.
        age = _age_hours(attrs.get("pool_created_at"))
        if age is None:
            stats["age_unknown"] += 1
            continue
        if age < config.min_age_hours:
            stats["age_young"] += 1
            continue
        if age > config.max_age_hours:
            stats["age_old"] += 1
            continue
        vol = attrs.get("volume_usd") or {}
        m5, h1 = _num(vol.get("m5")), _num(vol.get("h1"))
        if m5 < config.min_m5_vol_usd:
            stats["vol"] += 1
            continue
        if h1 > 0 and m5 < config.accel_ratio * (h1 / 12.0):
            stats["accel"] += 1
            continue
        tvl = _num(attrs.get("reserve_in_usd"))
        if not (config.min_tvl_usd <= tvl <= config.max_tvl_usd):
            stats["tvl"] += 1
            continue
        base_id = (((rel.get("base_token") or {}).get("data") or {}).get("id") or "")
        quote_id = (((rel.get("quote_token") or {}).get("data") or {}).get("id") or "")
        base_mint = base_id.split("_", 1)[-1]
        quote_mint = quote_id.split("_", 1)[-1]
        # SOL must be one side; base = the non-SOL side. Counted, not silent: an
        # uncounted reject reads as "nothing was out there" when the truth may be
        # "everything was out there and this gate ate it".
        if SOL_MINT not in (base_mint, quote_mint):
            stats["not_sol"] += 1
            continue
        runner_mint = quote_mint if base_mint == SOL_MINT else base_mint
        if runner_mint in excl_mints:
            stats["excluded"] += 1
            continue
        seen.add(addr)
        candidates.append({
            "pool": addr,
            "name": attrs.get("name") or "?",
            "age_h": round(age, 1),
            "m5": m5,
            "h1": h1,
            "tvl": tvl,
            "turnover_m5": m5 / max(tvl, 1.0),
            "base_mint": runner_mint,
            # Mint-mint, never mint-symbol: a trading_pair with a symbol on either
            # side is accepted by the tool layer and then silently fails on-chain.
            "mint_pair": f"{runner_mint}-{SOL_MINT}",
            "price_usd": _num(attrs.get("base_token_price_usd")),
        })

    if not candidates:
        return (
            f"runner_scanner: NO candidates passed the gates "
            f"(scanned:{len(raw)} rejects — venue:{stats['not_meteora']} "
            f"ageOld:{stats['age_old']} ageYoung:{stats['age_young']} "
            f"ageUnknown:{stats['age_unknown']} m5vol:{stats['vol']} accel:{stats['accel']} "
            f"tvl:{stats['tvl']} notSOL:{stats['not_sol']} held:{stats['excluded']}). "
            f"Runner sleeve should PAUSE — never force entries. "
            f"If one gate dominates the rejects tick after tick, that gate is mis-set, "
            f"not the market — say so in the journal rather than pausing silently forever."
        )

    candidates.sort(key=lambda c: c["turnover_m5"], reverse=True)
    shortlist = candidates[: config.top_n * 2]

    async def _enrich(c):
        if client is None:
            return c
        try:
            info = await client.gateway_clmm.get_pool_info(
                connector=VENUE, network=CLMM_NETWORK, pool_address=c["pool"]
            )
            c["bin_step"] = info.get("bin_step") or info.get("tick_spacing")
            price = info.get("price")
            if price is not None:
                c["price"] = _num(price)
        except Exception as e:
            logger.info(f"runner_scanner: pool-info failed for {c['pool']}: {e}")
        return c

    enriched = await asyncio.gather(*[_enrich(c) for c in shortlist])
    ranked = list(enriched)[: config.top_n]

    def _tier(m5):
        if m5 >= config.full_size_m5:
            return "FULL"
        if m5 >= 0.4 * config.full_size_m5:
            return "2/3"
        return "1/3 probe"

    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append({
            "#": i,
            "Pair": c["name"],
            "Age(h)": c["age_h"],
            "m5Vol": f"${c['m5']:,.0f}",
            "h1Vol": f"${c['h1']:,.0f}",
            "TVL": f"${c['tvl']:,.0f}",
            "m5/TVL": f"{c['turnover_m5']*100:.1f}%",
            "SizeTier": _tier(c["m5"]),
            "BinStep": c.get("bin_step"),
            "Price": f"{c.get('price', c['price_usd']):.6g}",
            "Pool": c["pool"],
            "BaseMint": c["base_mint"],
            "MintPair": c["mint_pair"],
        })
    columns = ["#", "Pair", "Age(h)", "m5Vol", "h1Vol", "TVL", "m5/TVL", "SizeTier",
               "BinStep", "Price", "Pool", "BaseMint", "MintPair"]

    summary = (
        f"runner_scanner: {len(ranked)} runner candidate(s) from {len(candidates)} gated "
        f"(rejects — ageOld:{stats['age_old']} ageYoung:{stats['age_young']} "
        f"ageUnknown:{stats['age_unknown']} m5vol:{stats['vol']} accel:{stats['accel']} "
        f"tvl:{stats['tvl']} notSOL:{stats['not_sol']}). "
        f"Top: {rows[0]['Pair']} m5 {rows[0]['m5Vol']} ({rows[0]['m5/TVL']} of TVL, tier {rows[0]['SizeTier']}). "
        f"MANDATORY before entry: token_safety_check on BaseMint + sellability. Record at-entry m5 for the decay exit."
    )

    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder("Runner Scanner — fresh Meteora pools by 5-min volume")
        builder.source("routine", "runner_scanner").tags(["lp", "runner", "meteora", "m5"])
        builder.kpi("Candidates", str(len(candidates)))
        builder.kpi("Ranked", str(len(ranked)))
        builder.kpi("Top m5", rows[0]["m5Vol"])
        builder.markdown(summary)
        builder.table(rows, columns)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.info(f"runner_scanner: report save skipped: {e}")

    try:
        from routines.base import RoutineResult
        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(f"{r['#']}. {r['Pair']} | m5 {r['m5Vol']} | TVL {r['TVL']} | "
                         f"tier {r['SizeTier']} | pool {r['Pool']} | mint {r['BaseMint']}")
        return "\n".join(lines)
