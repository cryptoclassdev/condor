"""Classify each pool's volatility regime (CALM / RANGING / TRENDING / CHAOTIC).

Agent-local routine for meteora_regime_lp. For each pool address it pulls 1h
OHLCV from GeckoTerminal, computes realized volatility over multiple windows,
EMA-slope trend strength, and where price sits in the recent band, then
classifies the regime and suggests a DLMM liquidity shape:

  CALM     -> Curve (strategyType 1), tight, centered
  RANGING  -> Spot (strategyType 0), moderate, centered
  TRENDING -> Bid-Ask (strategyType 2), wide, skewed with the trend
  CHAOTIC  -> stand aside (no position)

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

# Hourly realized-vol thresholds (std of 1h log returns, in %) tuned for Solana
# majors vs memecoins. Overridable via config.
DEFAULT_CALM_VOL = 0.35
DEFAULT_CHAOS_VOL = 2.5
DEFAULT_TREND_STRENGTH = 1.2  # |EMA12-EMA48| / ATR-like normalizer


class Config(BaseModel):
    """Classify pools into CALM/RANGING/TRENDING/CHAOTIC with suggested LP shape."""

    pool_addresses: list[str] = Field(description="Pool addresses to classify (1-6)")
    candles: int = Field(default=72, description="1h candles to analyze (max 100)")
    calm_vol_pct: float = Field(default=DEFAULT_CALM_VOL, description="Hourly vol %% below which regime is CALM")
    chaos_vol_pct: float = Field(default=DEFAULT_CHAOS_VOL, description="Hourly vol %% above which regime is CHAOTIC")
    trend_strength_min: float = Field(default=DEFAULT_TREND_STRENGTH, description="Normalized EMA divergence above which regime is TRENDING")

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


async def _fetch_ohlcv(session: aiohttp.ClientSession, pool: str, limit: int) -> list[list[float]]:
    url = f"{GECKO_BASE}/networks/{GECKO_NETWORK}/pools/{pool}/ohlcv/hour"
    headers = {"Accept": "application/json;version=20230302"}
    last_err = None
    for attempt in range(3):  # GeckoTerminal free tier rate-limits (429) under load
        async with session.get(url, headers=headers, params={"limit": min(limit, 100)},
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


def _classify(candles: list[list[float]], cfg: Config) -> dict:
    closes = [c[4] for c in candles if c[4] > 0]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    if len(closes) < 24:
        return {"regime": "UNKNOWN", "note": f"only {len(closes)} candles"}

    # Realized vol: std of hourly log returns, in %.
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    vol_pct = math.sqrt(var) * 100

    rets24 = rets[-24:]
    m24 = sum(rets24) / len(rets24)
    var24 = sum((r - m24) ** 2 for r in rets24) / max(len(rets24) - 1, 1)
    vol24_pct = math.sqrt(var24) * 100

    # Trend: EMA12 vs EMA48 divergence normalized by an ATR-like hourly range.
    ema12 = _ema(closes, 12)
    ema48 = _ema(closes, 48)
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(candles))]
    atr = sum(trs[-24:]) / max(len(trs[-24:]), 1)
    price = closes[-1]
    trend_strength = abs(ema12 - ema48) / atr if atr > 0 else 0.0
    trend_dir = "up" if ema12 > ema48 else "down"

    # Price position within the recent (72h) band.
    band_lo, band_hi = min(lows), max(highs)
    band_pos = (price - band_lo) / (band_hi - band_lo) if band_hi > band_lo else 0.5

    # Classification: vol extremes first, then trend, else ranging.
    vol_eff = max(vol_pct, vol24_pct)
    if vol_eff >= cfg.chaos_vol_pct:
        regime, shape, stype, width, skew = "CHAOTIC", "none", None, "—", "—"
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
        "vol72h_pct": round(vol_pct, 3),
        "vol24h_pct": round(vol24_pct, 3),
        "trend_strength": round(trend_strength, 2),
        "trend_dir": trend_dir,
        "band_pos": round(band_pos, 2),
        "price": price,
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
                    fetched.append(await _fetch_ohlcv(session, p, config.candles))
                except Exception as e:
                    fetched.append(e)
    except Exception as e:
        return f"regime_engine: failed to reach GeckoTerminal: {e}"

    for pool, data in zip(pools, fetched):
        if isinstance(data, Exception):
            results[pool] = {"regime": "UNKNOWN", "note": str(data)}
        else:
            results[pool] = _classify(data, config)

    columns = ["Pool", "Regime", "Shape", "strategyType", "Width", "Skew",
               "Vol24h%", "Vol72h%", "Trend", "BandPos", "Price"]
    rows = []
    for pool, r in results.items():
        rows.append({
            "Pool": pool,
            "Regime": r.get("regime"),
            "Shape": r.get("shape", "—"),
            "strategyType": r.get("strategyType", "—"),
            "Width": r.get("width", "—"),
            "Skew": r.get("skew", "—"),
            "Vol24h%": r.get("vol24h_pct", "—"),
            "Vol72h%": r.get("vol72h_pct", "—"),
            "Trend": f"{r.get('trend_strength', '—')} {r.get('trend_dir', '')}".strip(),
            "BandPos": r.get("band_pos", "—"),
            "Price": f"{r.get('price'):.6g}" if r.get("price") else (r.get("note") or "—"),
        })

    n_by = {}
    for r in results.values():
        n_by[r.get("regime")] = n_by.get(r.get("regime"), 0) + 1
    summary = (
        f"Classified {len(results)} pool(s): "
        + ", ".join(f"{k}×{v}" for k, v in n_by.items())
        + ". CALM→Curve tight · RANGING→Spot moderate · TRENDING→Bid-Ask wide+skewed · "
          "CHAOTIC→stand aside. Clamp all widths to < 69 bins for the pool's bin_step."
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
            lines.append(f"{r['Pool'][:8]}… {r['Regime']} | {r['Shape']} | vol24 {r['Vol24h%']} | trend {r['Trend']}")
        return "\n".join(lines)
