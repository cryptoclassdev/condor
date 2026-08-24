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

import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

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

# GeckoTerminal's keyless API is capped at 30 calls/minute. Keep this routine
# below 24 calls/minute so the other operator routines retain some headroom.
GECKO_REQUEST_INTERVAL_SEC = 2.5
GECKO_MAX_RETRIES = 2
GECKO_RATE_LIMIT_CIRCUIT_THRESHOLD = 2
_COOLDOWN_KEY = "_meteora_runner_scanner_rate_limit_until"


class GeckoRateLimitError(RuntimeError):
    """A feed exhausted its bounded HTTP 429 retry budget."""


class Config(BaseModel):
    """Rank fresh Meteora pools by 5-minute volume for the runner sleeve."""

    top_n: int = Field(default=5, description="Ranked candidates to return")
    min_age_hours: float = Field(
        default=1.0, description="Min pool age (skip launch candle)"
    )
    max_age_hours: float = Field(
        default=48.0, description="Max pool age (runners are young)"
    )
    min_m5_vol_usd: float = Field(
        default=8000.0, description="Min 5-minute volume in USD"
    )
    accel_ratio: float = Field(
        default=3.0,
        description="m5 must be >= this multiple of the average 5-min slice of h1 (m5 >= ratio*h1/12)",
    )
    min_tvl_usd: float = Field(default=10000.0, description="Min TVL")
    max_tvl_usd: float = Field(
        default=500000.0, description="Max TVL (small enough to run)"
    )
    full_size_m5: float = Field(
        default=50000.0, description="m5 USD volume that earns a FULL runner unit"
    )
    scan_timeout_sec: float = Field(
        default=55.0,
        description="Hard wall-clock budget so discovery cannot delay safety supervision",
    )
    rate_limit_cooldown_sec: float = Field(
        default=600.0,
        description="Pause new Gecko requests after the provider-wide 429 circuit opens",
    )
    exclude_pools: list[str] = Field(
        default=[], description="Held/blocked pool addresses"
    )
    exclude_mints: list[str] = Field(default=[], description="Held/blocked base mints")


def _retry_delay(headers, attempt: int) -> float:
    """Return a bounded Retry-After/exponential delay for a 429 response."""

    exponential_floor = min(30.0, 5.0 * (2**attempt))
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after:
        try:
            return min(30.0, max(exponential_floor, float(retry_after)))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return min(
                    30.0,
                    max(
                        exponential_floor,
                        (retry_at - datetime.now(timezone.utc)).total_seconds(),
                    ),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return exponential_floor


async def _gecko_get(
    session,
    path,
    params=None,
    *,
    sleep=asyncio.sleep,
    max_retries: int = GECKO_MAX_RETRIES,
):
    headers = {"Accept": "application/json;version=20230302"}
    for attempt in range(max_retries + 1):
        async with session.get(
            f"{GECKO_BASE}/{path}",
            headers=headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=25),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            status = resp.status
            retry_headers = resp.headers

        if status != 429:
            raise RuntimeError(f"GeckoTerminal {path} -> HTTP {status}")
        if attempt >= max_retries:
            raise GeckoRateLimitError(f"GeckoTerminal {path} -> HTTP {status}")

        delay = _retry_delay(retry_headers, attempt)
        logger.warning(
            "runner_scanner: GeckoTerminal rate limited %s; retrying in %.1fs",
            path,
            delay,
        )
        await sleep(delay)

    raise RuntimeError(f"GeckoTerminal {path} -> retry budget exhausted")


async def _fetch_gecko_feeds(
    session,
    requests,
    *,
    sleep=asyncio.sleep,
    request_interval: float = GECKO_REQUEST_INTERVAL_SEC,
):
    """Fetch a feed batch without letting a provider-wide 429 consume the tick.

    A single exhausted feed may be local or transient. Two consecutive feeds that
    both exhaust their retry budgets are strong evidence of a provider-level limit,
    so the remaining feed pages are skipped and the scan is reported as degraded.
    """

    raw = []
    successful_sources = 0
    consecutive_rate_limits = 0
    circuit_open = False

    for index, (path, params) in enumerate(requests):
        if index:
            await sleep(request_interval)
        try:
            result = await _gecko_get(session, path, params, sleep=sleep)
        except GeckoRateLimitError as source_error:
            consecutive_rate_limits += 1
            logger.warning("runner_scanner: source failed: %s", source_error)
            if consecutive_rate_limits >= GECKO_RATE_LIMIT_CIRCUIT_THRESHOLD:
                circuit_open = True
                logger.warning(
                    "runner_scanner: GeckoTerminal rate-limit circuit opened after "
                    "%d consecutive exhausted feeds",
                    consecutive_rate_limits,
                )
                break
            continue
        except Exception as source_error:
            consecutive_rate_limits = 0
            logger.warning("runner_scanner: source failed: %s", source_error)
            continue

        consecutive_rate_limits = 0
        successful_sources += 1
        raw.extend(result.get("data", []) or [])

    return raw, successful_sources, circuit_open


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


def summarize_no_candidates(*, raw_count: int, stats: dict[str, int]) -> str:
    """Explain zero results without mislabeling network-wide reach as a gate."""

    pool_count = max(0, raw_count - stats.get("not_meteora", 0))
    gates = [
        ("ageUnknown", stats.get("age_unknown", 0)),
        ("ageYoung", stats.get("age_young", 0)),
        ("ageOld", stats.get("age_old", 0)),
        ("m5vol", stats.get("vol", 0)),
        ("accel", stats.get("accel", 0)),
        ("tvl", stats.get("tvl", 0)),
        ("notSOL", stats.get("not_sol", 0)),
        ("held", stats.get("excluded", 0)),
    ]
    breakdown = " ".join(f"{key}:{value}" for key, value in gates)
    top, top_count = max(gates, key=lambda item: item[1])
    verdict = (
        f"The binding gate is **{top}** ({top_count}/{pool_count} of the Meteora pools seen). "
        if pool_count and top_count
        else "No Meteora pool reached the gates at all — this is a REACH problem, not a gate "
        "problem: the feeds returned nothing from this venue. Check the scanner's sources "
        "before touching any threshold. "
    )
    return (
        "runner_scanner: NO candidates passed the gates. "
        f"REACH: scanned {raw_count} pools, of which {pool_count} were Meteora "
        f"({stats.get('not_meteora', 0)} were other venues — structural, the new_pools feed "
        "is network-wide, NOT a mis-set gate and never to be reported as one). "
        f"GATES (out of those {pool_count} Meteora pools): {breakdown}. "
        f"{verdict}"
        "Runner sleeve should PAUSE — never force entries. If the SAME gate above binds tick "
        "after tick, say so explicitly in the journal and name the threshold and its configured "
        "value, so the operator can judge whether the market is quiet or the number is wrong. "
        "Pausing silently forever is the failure mode; so is blaming the venue filter."
    )


def source_coverage_prefix(*, successful: int, total: int) -> str:
    """Make partial upstream coverage impossible to mistake for full reach."""

    if successful >= total:
        return ""
    return (
        f"SOURCE DEGRADED: {successful}/{total} GeckoTerminal feeds succeeded; "
        "treat this scan as partial and do not relax gates from it. "
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)

    user_data = getattr(context, "user_data", None)
    if user_data is None:
        user_data = getattr(context, "_user_data", None)
    if user_data is None:
        user_data = {}
    now_epoch = datetime.now(timezone.utc).timestamp()
    cooldown_until = float(user_data.get(_COOLDOWN_KEY, 0) or 0)
    if cooldown_until > now_epoch:
        remaining = cooldown_until - now_epoch
        return (
            f"runner_scanner: RATE-LIMIT COOLDOWN active for {remaining:.0f}s. "
            "Runner sleeve must PAUSE; safety supervision continues and no "
            "provider calls were made."
        )

    raw = []
    try:
        async with asyncio.timeout(max(0.01, config.scan_timeout_sec)):
            async with aiohttp.ClientSession() as session:
                # Fresh Meteora pools are sparse in network-wide feeds.
                # new_pools is the ONLY genuinely young feed, but it is network-wide,
                # so most of it is not Meteora — page it as deep as the API allows.
                # The venue list is sorted two ways on purpose: by 24h volume (which
                # skews old and large) and by 24h tx count, where a young, busy pool
                # ranks even when its 24h volume is still small because it has only
                # existed for two hours.
                requests = (
                    [
                        (f"networks/{GECKO_NETWORK}/new_pools", {"page": p})
                        for p in range(1, 11)
                    ]
                    + [
                        (
                            f"networks/{GECKO_NETWORK}/dexes/{VENUE}/pools",
                            {"page": p, "sort": "h24_volume_usd_desc"},
                        )
                        for p in range(1, 4)
                    ]
                    + [
                        (
                            f"networks/{GECKO_NETWORK}/dexes/{VENUE}/pools",
                            {"page": p, "sort": "h24_tx_count_desc"},
                        )
                        for p in range(1, 4)
                    ]
                    + [(f"networks/{GECKO_NETWORK}/trending_pools", None)]
                )
                raw, successful_sources, circuit_open = await _fetch_gecko_feeds(
                    session, requests
                )
    except TimeoutError:
        return (
            f"runner_scanner: SOURCE TIMEOUT after {config.scan_timeout_sec:g}s. "
            "Runner sleeve must PAUSE; safety supervision continues and the scan may "
            "retry on the next scheduled deep tick."
        )
    except Exception as e:
        return f"runner_scanner: failed to reach GeckoTerminal: {e}"

    total_sources = len(requests)
    if circuit_open:
        user_data[_COOLDOWN_KEY] = now_epoch + max(
            1.0, config.rate_limit_cooldown_sec
        )
    if successful_sources == 0:
        return (
            "runner_scanner: SOURCE UNAVAILABLE: 0/"
            f"{total_sources} GeckoTerminal feeds succeeded. Runner sleeve must PAUSE; "
            "do not interpret this as a lack of candidates and do not relax gates."
        )
    coverage_prefix = source_coverage_prefix(
        successful=successful_sources, total=total_sources
    )
    if circuit_open:
        coverage_prefix += (
            "RATE-LIMIT CIRCUIT OPEN: remaining feeds were skipped to protect the "
            "supervision tick; retry on the next scheduled deep scan. "
        )

    excl_pools = set(config.exclude_pools)
    excl_mints = set(config.exclude_mints)
    seen, candidates = set(), []
    stats = {
        "not_meteora": 0,
        "age_old": 0,
        "age_young": 0,
        "age_unknown": 0,
        "vol": 0,
        "accel": 0,
        "tvl": 0,
        "not_sol": 0,
        "excluded": 0,
    }

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
        base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
        quote_id = ((rel.get("quote_token") or {}).get("data") or {}).get("id") or ""
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
        candidates.append(
            {
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
            }
        )

    if not candidates:
        return coverage_prefix + summarize_no_candidates(
            raw_count=len(raw), stats=stats
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
        rows.append(
            {
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
            }
        )
    columns = [
        "#",
        "Pair",
        "Age(h)",
        "m5Vol",
        "h1Vol",
        "TVL",
        "m5/TVL",
        "SizeTier",
        "BinStep",
        "Price",
        "Pool",
        "BaseMint",
        "MintPair",
    ]

    summary = coverage_prefix + (
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
        builder.source("routine", "runner_scanner").tags(
            ["lp", "runner", "meteora", "m5"]
        )
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
            lines.append(
                f"{r['#']}. {r['Pair']} | m5 {r['m5Vol']} | TVL {r['TVL']} | "
                f"tier {r['SizeTier']} | pool {r['Pool']} | mint {r['BaseMint']}"
            )
        return "\n".join(lines)
