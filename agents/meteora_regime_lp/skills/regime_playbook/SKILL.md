---
name: regime_playbook
description: Turn a regime_engine classification into a concrete Meteora DLMM lp_executor
  config — strategyType, bin width, skew, side, amounts — with the width clamp, price
  bracketing, haircut, and hysteresis rules applied.
when_to_use: When constructing the exact LP Executor config for a slot after the
  regime_engine has classified the pool, or when judging whether an open slot's shape
  still fits the current regime.
created: '2026-08-15T00:00:00Z'
source: agent:meteora_regime_lp
---

# Regime Playbook — from classification to executor config

Inputs: pool (from `meteora_pool_scanner`: pool_address, MintPair, BaseMint, bin_step,
MaxWidth%, price), regime row (from `regime_engine`), capital for this slot (USDC).

## 1. Shape by regime
| Regime | Entry | `strategyType` | Total width | Placement |
|---|---|---|---|---|
| CALM (majors) | double-sided `side=3` | `1` (Curve) | 10–20 bins | centered on P |
| RANGING | **single-sided quote `side=1`** | `2` (Bid-Ask) | 30–50 bins | from just under P down to the recent band low (retracement zone) |
| TRENDING up | single-sided quote `side=1` on a pullback; later FLIP | `2` | 40–60 bins | below P; after fill + higher low → reopen `side=2` token-side ABOVE P |
| TRENDING down | skip, or quote-only far below P, reduced size | `2` | wide | deep retracement zone only |
| CHAOTIC | do not open | — | — | — |

**Why bid-ask below price is the satellite default:** no entry swap (no ×0.995 haircut risk),
every bin fill is a buy at a pre-chosen level AND counts as volume, fees accrue while
filling, and single-sided quote can't be caught 50/50 in a falling knife. Spot (`0`) is only
for short-duration (<4h) positions with bullish momentum already in motion.

**Flip discipline (the second fee leg):** flip only when the reflow is in motion — higher low
on the hourly, sell volume drying, band_pos rising. Too early = you exit into continuation;
too late = price runs past your new range. You don't need the bottom, just the turn.

Width in bins → price bounds: `upper/lower = P × (1 ± half_width)` where the TOTAL width
satisfies `bins = ln(Pu/Pl) / ln(1 + bin_step/10000)`. **Hard clamp: bins < 69** — compute
before every open, shrink until it fits (the scanner's MaxWidth% is the ceiling for this
pool). Bounds MUST bracket the live price from `get_pool_info`: `lower < P < upper`.

## 2. Sizing & side
- Satellites (SOL-quoted pools): default `side=1` quote-only bid-ask below P —
  `quote_amount = capital`, `base_amount = 0`, **no swap needed**. On a flip:
  `side=2` token-side above P with the base amount actually held in the wallet.
- Core (SOL-USDC, CALM): double-sided `side=3` ~50/50. This needs an entry swap —
  **haircut the reported fill ×0.995** before `base_amount` (or read the true wallet balance).
- Size inversely to volatility and to conviction: full satellite allocation only for gated
  pools in a clean regime; anything exploratory gets half.
- Always `keep_position=false`. **PnL/TP/SL and risk limits are measured in USD** even for
  SOL-quoted pools (scoring is USD): convert with the live SOL price from `get_market_data`.

## 3. Hysteresis (anti-churn) — check before ANY rebalance/rotation
1. Price out of range beyond `out_of_range_buffer_pct` (not just touching the edge)?
2. `out_of_range_max_sec` elapsed AND `rebalance_cooldown_sec` since this slot's last action?
3. Expected fees at the new placement > estimated costs (tx fees + entry/exit swap spread
   ~0.3–0.5% + IL risk) by a clear margin? Journal the arithmetic.
4. Under `max_rebalances_per_hour`?
All four yes → act. Any no → hold and journal why.

## 4. Three-outcome test (journal before every open)
One line each: price exits ABOVE (we hold …), price STAYS in range (we earn …), price exits
BELOW (we hold …). If the "below" line is unacceptable for a satellite token, cut base_pct or
skip the pool.

## 5. Validate before create
- Capital ≥ venue minimum position size; wallet keeps `min_wallet_sol_reserve` SOL for rent
  (~0.057/position) + fees.
- If the open FAILS simulation: re-check bracketing + bin count, narrow once, retry once;
  then treat the pool as suspect. If the entry swap landed but the open failed, repair
  immediately (retry with true balance or swap back to USDC).
