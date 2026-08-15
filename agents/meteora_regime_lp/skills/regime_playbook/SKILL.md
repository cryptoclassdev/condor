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
| Regime | `extra_params.strategyType` | Total width target | Placement |
|---|---|---|---|
| CALM | `1` (Curve — liquidity concentrated at center) | 10–20 bins | centered on P |
| RANGING | `0` (Spot — uniform) | 20–40 bins | centered on P |
| TRENDING | `2` (Bid-Ask — liquidity at edges) | 40–60 bins | skewed: ~70% of width on the side the trend is moving TOWARD |
| CHAOTIC | do not open | — | — |

Width in bins → price bounds: `upper/lower = P × (1 ± half_width)` where the TOTAL width
satisfies `bins = ln(Pu/Pl) / ln(1 + bin_step/10000)`. **Hard clamp: bins < 69** — compute
before every open, shrink until it fits (the scanner's MaxWidth% is the ceiling for this
pool). Bounds MUST bracket the live price from `get_pool_info`: `lower < P < upper`.

## 2. Sizing & side (USDC-quoted pools)
Default double-sided `side=3`, 50/50 unless TRENDING:
- TRENDING up: hold more base (memecoin/SOL) — base_pct ≈ 60–70, range skewed up.
- TRENDING down: hold more USDC — base_pct ≈ 30–40, range skewed down. Single-sided
  (`side=1`, quote-only, range below P) is allowed on a pullback entry.
- `quote_amount = capital × (1 − base_pct/100)`; acquire base worth `capital × base_pct/100`
  via order_executor MARKET swap on the **MintPair**.
- **Haircut the reported fill ×0.995** before `base_amount` (or read the true wallet balance).
- Always `keep_position=false`; PnL/TP/SL measured in USDC.

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
