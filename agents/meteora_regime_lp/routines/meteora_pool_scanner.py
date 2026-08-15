"""Scan & rank Meteora DLMM (CLMM) pools by fee yield for regime-aware LP entry.

Agent-local routine for meteora_regime_lp. Sources pools from GeckoTerminal
(trending + top Meteora pools), filters to the configured quote asset (default
USDC; quote matched by MINT on either pool side — GeckoTerminal orientation
varies), applies hard entry gates (TVL floor, sustained multi-window volume to
reject single-spike pools), enriches survivors with on-chain pool-info
(fee %, bin_step, live price, mints), computes fee_yield = fees(window)/TVL,
and returns a ranked shortlist with the exact fields to open an lp_executor —
including the base token MINT and a mint-based trading pair (memecoins are not
resolvable by symbol on Gateway).

Diversification: pass `exclude_pools` / `exclude_mints` (held) so the ranking
never returns pools/tokens already held.
"""

import logging
import asyncio
import math
import re
import aiohttp
from pydantic import BaseModel, Field, field_validator
from telegram.ext import ContextTypes
from config_manager import get_client
from handlers.dex.geckoterminal import _extract_pool_data

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
GECKO_NETWORK = "solana"
CLMM_NETWORK = "solana-mainnet-beta"
VENUE = "meteora"
_WINDOW_TO_FIELD = {"1h": "volume_1h", "6h": "volume_6h", "24h": "volume_24h"}
_QUOTE_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}


class Config(BaseModel):
    """Rank Meteora DLMM pools by fee yield (fees/TVL) behind hard entry gates."""

    quote_asset: str = Field(default="USDC", description="Quote token to require (USDC or SOL)")
    ranking_window: str = Field(default="24h", description="Volume window for fees/TVL: 1h, 6h, or 24h")
    top_n: int = Field(default=5, description="Number of ranked pools to return")
    min_tvl_usd: float = Field(default=25000.0, description="Minimum pool TVL (reserve) in USD")
    require_sustained_volume: bool = Field(
        default=True,
        description="Reject single-spike pools: require volume in 1h AND 6h AND 24h windows, "
        "with 1h volume <= 60% of 24h volume",
    )
    exclude_pools: list[str] = Field(default=[], description="Pool addresses to exclude (already held)")
    exclude_mints: list[str] = Field(default=[], description="Base token mints to exclude (already held)")

    @field_validator("exclude_pools", "exclude_mints", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if isinstance(v, str):
            return [s for s in re.split(r"[,\s]+", v.strip()) if s]
        return v


async def _gecko_get(session: aiohttp.ClientSession, path: str, params: dict | None = None) -> dict:
    url = f"{GECKO_BASE}/{path}"
    headers = {"Accept": "application/json;version=20230302"}
    async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GeckoTerminal {path} -> HTTP {resp.status}")
        return await resp.json()


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available — cannot fetch pool-info."

    quote = config.quote_asset.strip().upper()
    quote_mint = _QUOTE_MINTS.get(quote)
    if not quote_mint:
        return f"meteora_pool_scanner: unsupported quote_asset '{quote}' — supported: {', '.join(_QUOTE_MINTS)}."
    vol_field = _WINDOW_TO_FIELD.get(config.ranking_window, "volume_24h")
    excl_pools = {p for p in config.exclude_pools if p}
    excl_mints = {m for m in config.exclude_mints if m}

    # 1. Source candidates: global trending + top Meteora pools (2 pages).
    raw: list[dict] = []
    try:
        async with aiohttp.ClientSession() as session:
            tasks = [
                _gecko_get(session, f"networks/{GECKO_NETWORK}/trending_pools"),
                _gecko_get(session, f"networks/{GECKO_NETWORK}/dexes/{VENUE}/pools", {"page": 1}),
                _gecko_get(session, f"networks/{GECKO_NETWORK}/dexes/{VENUE}/pools", {"page": 2}),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"meteora_pool_scanner: gecko source failed: {r}")
                continue
            raw.extend(r.get("data", []) or [])
    except Exception as e:
        return f"meteora_pool_scanner: failed to reach GeckoTerminal: {e}"

    # 2. Parse, filter (venue, quote-by-mint either orientation, TVL, sustained volume, excludes).
    seen: set[str] = set()
    candidates: list[dict] = []
    rejected_spike = 0
    for p in raw:
        d = _extract_pool_data(p)
        addr = d.get("address") or ""
        if not addr or addr in excl_pools or addr in seen:
            continue
        if (d.get("dex_id") or "") != VENUE:
            continue
        base_sym = d.get("base_token_symbol") or ""
        quote_sym = d.get("quote_token_symbol") or ""
        if not base_sym or not quote_sym or "?" in (base_sym, quote_sym):
            continue
        gecko_base_mint = d.get("base_token_address") or ""
        gecko_quote_mint = d.get("quote_token_address") or ""
        price_usd = _num(d.get("base_token_price_usd"))
        if gecko_quote_mint == quote_mint:
            pass
        elif gecko_base_mint == quote_mint:
            base_sym, quote_sym = quote_sym, base_sym
            gecko_base_mint, gecko_quote_mint = gecko_quote_mint, gecko_base_mint
            price_usd = _num(d.get("quote_token_price_usd"))
        else:
            continue
        tvl = _num(d.get("reserve_usd"))
        if tvl < config.min_tvl_usd:
            continue
        vol_1h = _num(d.get("volume_1h"))
        vol_6h = _num(d.get("volume_6h"))
        vol_24h = _num(d.get("volume_24h"))
        vol_window = _num(d.get(vol_field))
        if vol_window <= 0:
            continue
        if config.require_sustained_volume:
            # Sustained two-sided flow: activity in every window, and the last hour
            # must not BE the whole day (single-spike pool → fees won't persist).
            if vol_1h <= 0 or vol_6h <= 0 or vol_24h <= 0 or (vol_24h > 0 and vol_1h / vol_24h > 0.60):
                rejected_spike += 1
                continue
        seen.add(addr)
        candidates.append(
            {
                "pool_address": addr,
                "base_symbol": base_sym,
                "quote_symbol": quote_sym,
                "trading_pair": f"{base_sym}-{quote_sym}",
                "gecko_base_mint": gecko_base_mint,
                "gecko_quote_mint": gecko_quote_mint,
                "tvl_usd": tvl,
                "vol_window": vol_window,
                "vol_1h": vol_1h,
                "vol_24h": vol_24h,
                "price_usd": price_usd,
            }
        )

    if not candidates:
        return (
            f"meteora_pool_scanner: no {quote}-quoted Meteora pools passed the gates "
            f"(TVL >= ${config.min_tvl_usd:,.0f}, window {config.ranking_window}, "
            f"{rejected_spike} rejected as single-spike, "
            f"{len(excl_pools)} pools/{len(excl_mints)} mints excluded)."
        )

    # Pre-rank by turnover so we only enrich the most promising.
    candidates.sort(key=lambda c: c["vol_window"] / max(c["tvl_usd"], 1.0), reverse=True)
    shortlist = candidates[: max(config.top_n * 2, config.top_n)]

    # 3. Enrich with on-chain pool-info (fee %, bin_step, price, mints).
    async def _enrich(c: dict) -> dict | None:
        try:
            info = await client.gateway_clmm.get_pool_info(
                connector=VENUE, network=CLMM_NETWORK, pool_address=c["pool_address"]
            )
        except Exception as e:
            logger.info(f"meteora_pool_scanner: pool-info failed for {c['pool_address']}: {e}")
            return None
        fee_pct = _num(info.get("fee_pct") or info.get("base_fee_percentage"))
        if fee_pct <= 0:
            return None
        mints = [
            info.get("base_token_address"),
            info.get("quote_token_address"),
            c.get("gecko_base_mint"),
            c.get("gecko_quote_mint"),
        ]
        mints = [m for m in mints if m]
        base_mint = next((m for m in mints if m != quote_mint), "")
        if not base_mint:
            base_mint = c.get("gecko_base_mint") or (mints[0] if mints else "")
        if base_mint and base_mint in excl_mints:
            return None
        c["fee_pct"] = fee_pct
        c["bin_step"] = info.get("bin_step") or info.get("tick_spacing")
        price = info.get("price")
        if price is not None:
            c["price"] = _num(price)
        c["base_mint"] = base_mint
        c["mint_pair"] = f"{base_mint}-{quote}" if base_mint else c["trading_pair"]
        c["fee_yield"] = (c["vol_window"] * (fee_pct / 100.0)) / max(c["tvl_usd"], 1.0)
        # Max bins < 69 → max total width this pool supports (for the regime shaper).
        try:
            step = float(c["bin_step"])
            c["max_width_pct"] = round((math.pow(1 + step / 10000.0, 68) - 1) * 100, 2)
        except (TypeError, ValueError):
            c["max_width_pct"] = None
        return c

    enriched = [r for r in await asyncio.gather(*[_enrich(c) for c in shortlist]) if r]
    if not enriched:
        return (
            f"meteora_pool_scanner: {len(candidates)} candidates but none usable after "
            f"pool-info + excludes ({len(excl_mints)} mints held)."
        )

    enriched.sort(key=lambda c: c["fee_yield"], reverse=True)
    ranked = enriched[: config.top_n]

    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append(
            {
                "#": i,
                "Pair": c["trading_pair"],
                "MintPair": c.get("mint_pair"),
                "BaseMint": c.get("base_mint"),
                "Pool": c["pool_address"],
                "TVL": f"${c['tvl_usd']:,.0f}",
                f"Vol({config.ranking_window})": f"${c['vol_window']:,.0f}",
                "Fee%": f"{c['fee_pct']:.3f}",
                "FeeYield": f"{c['fee_yield'] * 100:.3f}%",
                "BinStep": c.get("bin_step"),
                "MaxWidth%": c.get("max_width_pct"),
                "Price": f"{c.get('price', c['price_usd']):.6g}",
            }
        )

    columns = [
        "#", "Pair", "MintPair", "BaseMint", "Pool", "TVL",
        f"Vol({config.ranking_window})", "Fee%", "FeeYield", "BinStep", "MaxWidth%", "Price",
    ]

    from condor.reports import ReportBuilder

    builder = ReportBuilder(f"Meteora Pool Scanner — {quote}-quoted DLMM fee-yield ranking")
    builder.source("routine", "meteora_pool_scanner").tags(["lp", "solana", "meteora", quote.lower()])
    builder.kpi("Candidates", str(len(candidates)))
    builder.kpi("Ranked", str(len(ranked)))
    builder.kpi("Top FeeYield", rows[0]["FeeYield"] if rows else "-")
    builder.kpi("Spike-rejected", str(rejected_spike))
    builder.markdown(
        f"Fee yield = fees(**{config.ranking_window}**)/TVL (fees ≈ vol × fee%). Meteora DLMM only. "
        f"Quote: **{quote}**. Min TVL: ${config.min_tvl_usd:,.0f}. Sustained-volume gate: "
        f"{'ON' if config.require_sustained_volume else 'off'} ({rejected_spike} rejected). "
        f"Excluded {len(excl_pools)} pools / {len(excl_mints)} held mints. "
        f"Use **MintPair** for the entry swap and lp_executor trading_pair; MaxWidth% is the "
        f"widest total range this pool's bin_step allows (< 69 bins)."
    )
    builder.table(rows, columns)
    builder.manual_order()
    await builder.save()

    summary = (
        f"Ranked {len(ranked)} {quote}-quoted Meteora DLMM pools by fee yield "
        f"(from {len(candidates)} gated candidates; {rejected_spike} spike-rejected). "
        f"Top: {rows[0]['Pair']} ({rows[0]['FeeYield']} yield). Use MintPair for swap + lp_executor."
    )

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(
                f"{r['#']}. {r['Pair']} | yield {r['FeeYield']} | TVL {r['TVL']} | "
                f"binStep {r['BinStep']} | pool {r['Pool']} | mintpair {r['MintPair']}"
            )
        return "\n".join(lines)
