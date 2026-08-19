"""Classify each pool's volatility regime (CALM / RANGING / TRENDING / HOT / CHAOTIC).

Agent-local routine for meteora_regime_lp. For each pool address it pulls OHLCV
from GeckoTerminal at the coarsest resolution that yields >= 24 bars (1h, else
5m; never finer), computes realized volatility rescaled to a common %/hour axis,
EMA-slope trend strength, and where price sits in the sampled band, then
classifies the regime and suggests a DLMM liquidity shape:

  CALM     -> Curve (strategyType 1), tight, centered
  RANGING  -> Spot (strategyType 0), moderate, centered
  TRENDING -> Bid-Ask (strategyType 2), wide, skewed with the trend
  HOT      -> Bid-Ask (strategyType 2), TIGHT, probe size, hard SL + max-hold.
              satellite/runner only; core has no HOT band.
  CHAOTIC  -> stand aside (no position)

Thresholds are per sleeve: core abstains above 2.5%/h, while satellite and
runner treat 8-120%/h and 10-150%/h as HOT rather than untouchable — high vol is
the fee engine those sleeves exist to harvest, and size plus time is how the risk
is controlled.

Deterministic measurement for the LLM to judge on — no network calls other
than GeckoTerminal OHLCV, no Gateway dependency.
"""

import logging
import asyncio
import math
import re
import aiohttp
from pydantic import BaseModel, Field, field_validator
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
GECKO_NETWORK = "solana"

# Hourly realized-vol thresholds (std of log returns scaled to a 1h horizon, in
# %) tuned for Solana majors. Overridable via config.
DEFAULT_CALM_VOL = 0.35
DEFAULT_CHAOS_VOL = 2.5
DEFAULT_TREND_STRENGTH = 1.2  # |EMA12-EMA48| / ATR-like normalizer

# Chaos threshold by sleeve. The 2.5%/h default was calibrated on SOL-USDC and
# friends; memecoins routinely realize 3-10%/h, so applying the majors number to
# the satellite/runner sleeves stamped every candidate CHAOTIC and the sleeves
# could never open. Volume gates, token_safety_check and the hard per-sleeve stop
# are the real guardrails there — the regime gate only has to exclude the genuinely
# berserk.
SLEEVE_CHAOS_VOL = {"core": 2.5, "satellite": 8.0, "runner": 10.0}

# Above the sleeve's chaos threshold the memecoin sleeves do NOT simply stand
# aside — high realized vol is the fee engine they exist to harvest (measured
# Aug 19: pools at 42-51%/h were carrying 44-66% fee yield). Between the chaos
# threshold and the abstain ceiling the regime is reported HOT: tradeable, but
# only small, tight and time-boxed. Above the ceiling it is genuinely berserk and
# every sleeve stands aside. Core has no HOT band — it abstains at its threshold.
SLEEVE_ABSTAIN_VOL = {"core": 2.5, "satellite": 120.0, "runner": 150.0}

# Adaptive candle resolution. _classify needs >= 24 bars; 1h candles cannot supply
# that for a pool younger than a day, which is exactly the age band the runner
# sleeve targets (1-48h). Fall back to coarser-to-finer intervals so a young pool
# still gets a real verdict instead of UNKNOWN.
# The ladder STOPS at 5m. 1m bars of a thin memecoin pool are dominated by
# microstructure noise — bid-ask bounce and single trades moving price — and the
# sqrt(60/1) = 7.75x rescale amplifies exactly that noise into the hourly figure.
# Measured on the same pool within the hour (Intismeran Hup1z8): 51.25%/h on 5m
# bars vs 65.41%/h on 1m bars, a 28% inflation, where a 1h-vs-5m pair on a calmer
# pool differed by only 7%. A pool too young for 24 five-minute bars (< 2h old) is
# better reported UNKNOWN than measured wrong and sized on.
#   (timeframe, aggregate, minutes_per_bar)
OHLCV_LADDER = [("hour", 1, 60.0), ("minute", 5, 5.0)]
MIN_BARS = 24


class Config(BaseModel):
    """Classify pools into CALM/RANGING/TRENDING/CHAOTIC with suggested LP shape."""

    pool_addresses: list[str] = Field(description="Pool addresses to classify (1-6)")
    candles: int = Field(default=72, description="Candles to analyze (max 100)")
    sleeve: str = Field(
        default="core",
        description="Which sleeve this classification is for: core | satellite | runner. "
        "Sets the CHAOTIC threshold unless chaos_vol_pct is given explicitly.",
    )
    calm_vol_pct: float = Field(default=DEFAULT_CALM_VOL, description="Hourly vol %% below which regime is CALM")
    chaos_vol_pct: float | None = Field(
        default=None,
        description="Hourly-equivalent vol %% above which regime is CHAOTIC. "
        "Defaults to the sleeve's threshold (core 2.5, satellite 8, runner 10).",
    )
    trend_strength_min: float = Field(default=DEFAULT_TREND_STRENGTH, description="Normalized EMA divergence above which regime is TRENDING")
    abstain_vol_pct: float | None = Field(
        default=None,
        description="Hourly-equivalent vol %% above which EVERY sleeve stands aside. "
        "Between chaos_vol_pct and this, satellite/runner get HOT (small, tight, time-boxed) "
        "instead of CHAOTIC. Defaults per sleeve: core 2.5 (no HOT band), satellite 120, runner 150.",
    )

    @property
    def _sleeve(self) -> str:
        return self.sleeve.strip().lower()

    @property
    def chaos_threshold(self) -> float:
        if self.chaos_vol_pct is not None:
            return self.chaos_vol_pct
        return SLEEVE_CHAOS_VOL.get(self._sleeve, DEFAULT_CHAOS_VOL)

    @property
    def abstain_threshold(self) -> float:
        if self.abstain_vol_pct is not None:
            return self.abstain_vol_pct
        return max(
            SLEEVE_ABSTAIN_VOL.get(self._sleeve, DEFAULT_CHAOS_VOL), self.chaos_threshold
        )

    @field_validator("pool_addresses", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if isinstance(v, str):
            return [s for s in re.split(r"[,\s]+", v.strip()) if s]
        return v


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


async def _fetch_ohlcv(
    session: aiohttp.ClientSession,
    pool: str,
    limit: int,
    timeframe: str = "hour",
    aggregate: int = 1,
) -> list[list[float]]:
    url = f"{GECKO_BASE}/networks/{GECKO_NETWORK}/pools/{pool}/ohlcv/{timeframe}"
    headers = {"Accept": "application/json;version=20230302"}
    params = {"limit": min(limit, 100), "aggregate": aggregate}
    last_err = None
    for attempt in range(3):  # GeckoTerminal free tier rate-limits (429) under load
        async with session.get(url, headers=headers, params=params,
                               timeout=aiohttp.ClientTimeout(total=25)) as resp:
            if resp.status == 429:
                last_err = RuntimeError(f"OHLCV {pool} -> HTTP 429 (rate limited)")
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if resp.status != 200:
                raise RuntimeError(f"OHLCV {pool} -> HTTP {resp.status}")
            payload = await resp.json()
            lst = ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            # Each item: [ts, open, high, low, close, volume]; newest first per Gecko docs.
            return list(reversed([[float(x) for x in row] for row in lst]))  # oldest -> newest
    raise last_err or RuntimeError(f"OHLCV {pool} -> failed")


async def _fetch_adaptive(
    session: aiohttp.ClientSession, pool: str, limit: int
) -> tuple[list[list[float]], float, str]:
    """Fetch the coarsest OHLCV series that still yields >= MIN_BARS bars.

    Returns ``(candles, minutes_per_bar, label)``. A day-old pool has no 24 hourly
    candles to give, so asking only for hourly data returned UNKNOWN for precisely
    the young pools the satellite and runner sleeves exist to trade. Stepping down
    to 5m buys a real classification; ``minutes_per_bar`` lets the caller rescale
    per-bar volatility onto the common hourly axis the thresholds are expressed in.

    A ``!err`` suffix on the returned label means this resolution was reached
    because a coarser one errored (typically a 429), not because the pool was too
    young for it — treat that reading as degraded.
    """
    last_err: Exception | None = None
    best: tuple[list[list[float]], float, str] | None = None
    degraded = False
    for i, (timeframe, aggregate, minutes) in enumerate(OHLCV_LADDER):
        if i:
            await asyncio.sleep(1.5)  # free-tier spacing between fallback hops
        label = f"{aggregate}{'h' if timeframe == 'hour' else 'm'}"
        if degraded:
            # Say so out loud. A 429 silently dropping the measurement to a finer
            # resolution changes the number the sleeve sizes on, and the Res column
            # alone does not tell the reader it happened because of an error rather
            # than because the pool was young.
            label += "!err"
        try:
            candles = await _fetch_ohlcv(session, pool, limit, timeframe, aggregate)
        except Exception as e:  # try the next resolution rather than giving up
            last_err = e
            degraded = True
            continue
        if best is None or len(candles) > len(best[0]):
            best = (candles, minutes, label)
        if len(candles) >= MIN_BARS:
            return candles, minutes, label
    if best is not None:
        return best
    raise last_err or RuntimeError(f"OHLCV {pool} -> failed at every resolution")


def _classify(candles: list[list[float]], cfg: Config, minutes_per_bar: float = 60.0) -> dict:
    closes = [c[4] for c in candles if c[4] > 0]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    if len(closes) < MIN_BARS:
        return {
            "regime": "UNKNOWN",
            "note": f"only {len(closes)} bars @{minutes_per_bar:g}m",
        }

    # Volatility scales with the square root of the horizon, so a 5m series has to
    # be lifted onto the hourly axis the CALM/CHAOTIC thresholds are written in.
    # Without this a memecoin measured on 5m bars looks ~3.5x calmer than the same
    # coin measured hourly, and would classify CALM at the moment it is wildest.
    to_hourly = math.sqrt(60.0 / minutes_per_bar) if minutes_per_bar > 0 else 1.0

    # Realized vol: std of log returns, rescaled to %/hour.
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    vol_pct = math.sqrt(var) * 100 * to_hourly

    rets24 = rets[-24:]
    m24 = sum(rets24) / len(rets24)
    var24 = sum((r - m24) ** 2 for r in rets24) / max(len(rets24) - 1, 1)
    vol24_pct = math.sqrt(var24) * 100 * to_hourly

    # Trend: EMA12 vs EMA48 divergence normalized by an ATR-like hourly range.
    ema12 = _ema(closes, 12)
    ema48 = _ema(closes, 48)
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(candles))]
    atr = sum(trs[-24:]) / max(len(trs[-24:]), 1)
    price = closes[-1]
    trend_strength = abs(ema12 - ema48) / atr if atr > 0 else 0.0
    trend_dir = "up" if ema12 > ema48 else "down"

    # Price position within the FULL sampled band (span depends on resolution).
    band_lo, band_hi = min(lows), max(highs)
    band_pos = (price - band_lo) / (band_hi - band_lo) if band_hi > band_lo else 0.5

    # Classification: vol extremes first, then trend, else ranging.
    vol_eff = max(vol_pct, vol24_pct)
    if vol_eff >= cfg.abstain_threshold:
        regime, shape, stype, width, skew = "CHAOTIC", "none", None, "—", "—"
    elif vol_eff >= cfg.chaos_threshold:
        # Tradeable, but only on the memecoin sleeves' terms: probe size, narrow
        # range, hard stop, short hold. Size and time are the risk control here,
        # not abstention.
        regime, shape, stype = "HOT", "Bid-Ask", 2
        width = "tight (10-25 bins)"
        skew = f"asymmetric {trend_dir}, PROBE size, hard SL + max-hold"
    elif trend_strength >= cfg.trend_strength_min:
        regime, shape, stype = "TRENDING", "Bid-Ask", 2
        width = "wide (40–60 bins)"
        skew = f"asymmetric {trend_dir}"
    elif vol_eff <= cfg.calm_vol_pct:
        regime, shape, stype, width, skew = "CALM", "Curve", 1, "tight (10–20 bins)", "centered"
    else:
        regime, shape, stype, width, skew = "RANGING", "Spot", 0, "moderate (20–40 bins)", "centered"

    return {
        "regime": regime,
        "shape": shape,
        "strategyType": stype,
        "width": width,
        "skew": skew,
        # Named for what they actually measure. These were "vol72h"/"vol24h" from
        # the hourly-only era; at 5m resolution "72 bars" is 6 hours, not 72, and
        # the stale labels had readers reasoning about a 72h window that did not
        # exist.
        "vol_full_pct": round(vol_pct, 3),
        "vol_recent_pct": round(vol24_pct, 3),
        "full_span_h": round(len(closes) * minutes_per_bar / 60.0, 1),
        "recent_span_h": round(min(24, len(rets)) * minutes_per_bar / 60.0, 1),
        "trend_strength": round(trend_strength, 2),
        "trend_dir": trend_dir,
        "band_pos": round(band_pos, 2),
        "price": price,
        "bars": len(closes),
    }


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    pools = [p for p in config.pool_addresses if p][:6]
    if not pools:
        return "regime_engine: no pool_addresses given."

    results: dict[str, dict] = {}
    try:
        # Sequential with spacing — parallel OHLCV fetches trip GeckoTerminal's
        # free-tier rate limit (429), which turned every regime into UNKNOWN.
        async with aiohttp.ClientSession() as session:
            fetched = []
            for i, p in enumerate(pools):
                if i:
                    await asyncio.sleep(1.5)
                try:
                    fetched.append(await _fetch_adaptive(session, p, config.candles))
                except Exception as e:
                    fetched.append(e)
    except Exception as e:
        return f"regime_engine: failed to reach GeckoTerminal: {e}"

    for pool, data in zip(pools, fetched):
        if isinstance(data, Exception):
            results[pool] = {"regime": "UNKNOWN", "note": str(data)}
        else:
            candles, minutes, label = data
            results[pool] = _classify(candles, config, minutes)
            results[pool]["res"] = label

    columns = ["Pool", "Regime", "Shape", "strategyType", "Width", "Skew",
               "VolRecent%/h", "VolFull%/h", "SpanRecent(h)", "SpanFull(h)",
               "Trend", "BandPos", "Res", "Bars", "Price"]
    rows = []
    for pool, r in results.items():
        rows.append({
            "Pool": pool,
            "Regime": r.get("regime"),
            "Shape": r.get("shape", "—"),
            "strategyType": r.get("strategyType", "—"),
            "Width": r.get("width", "—"),
            "Skew": r.get("skew", "—"),
            "VolRecent%/h": r.get("vol_recent_pct", "—"),
            "VolFull%/h": r.get("vol_full_pct", "—"),
            "SpanRecent(h)": r.get("recent_span_h", "—"),
            "SpanFull(h)": r.get("full_span_h", "—"),
            "Trend": f"{r.get('trend_strength', '—')} {r.get('trend_dir', '')}".strip(),
            "BandPos": r.get("band_pos", "—"),
            "Res": r.get("res", "—"),
            "Bars": r.get("bars", "—"),
            "Price": f"{r.get('price'):.6g}" if r.get("price") else (r.get("note") or "—"),
        })

    n_by = {}
    for r in results.values():
        n_by[r.get("regime")] = n_by.get(r.get("regime"), 0) + 1
    summary = (
        f"Classified {len(results)} pool(s) for the {config.sleeve.upper()} sleeve "
        f"(HOT above {config.chaos_threshold:g}%/h, CHAOTIC above {config.abstain_threshold:g}%/h): "
        + ", ".join(f"{k}×{v}" for k, v in n_by.items())
        + ". CALM→Curve tight · RANGING→Spot moderate · TRENDING→Bid-Ask wide+skewed · "
          "HOT→Bid-Ask TIGHT at PROBE size with hard SL + max-hold (satellite/runner only; "
          "high vol is the fee engine, size and time are the risk control) · "
          "CHAOTIC→stand aside. Clamp all widths to < 69 bins for the pool's bin_step. "
          "VolRecent = last 24 bars, VolFull = all bars; SpanRecent/SpanFull give the real "
          "hours each covers, which is NOT 24h/72h unless Res is 1h. "
          "Res = candle resolution actually used (auto-steps 1h→5m→1m for young pools); "
          "vol is rescaled to %/hour either way, so thresholds compare like-for-like."
    )

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Regime Engine — volatility/trend classification")
        builder.source("routine", "regime_engine").tags(["lp", "regime", "meteora"])
        builder.kpi("Pools", str(len(results)))
        for k, v in n_by.items():
            builder.kpi(str(k), str(v))
        builder.markdown(summary)
        builder.table(rows, columns)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.info(f"regime_engine: report save skipped: {e}")

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(
                f"{r['Pool'][:8]}… {r['Regime']} | {r['Shape']} | "
                f"volRecent {r['VolRecent%/h']}%/h over {r['SpanRecent(h)']}h "
                f"| trend {r['Trend']} | {r['Bars']} bars @{r['Res']}"
            )
        return "\n".join(lines)
