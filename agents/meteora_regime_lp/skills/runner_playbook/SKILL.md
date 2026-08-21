---
name: runner_playbook
description: The RISK sleeve — bot-feasible adaptation of the LP Army Heart-Attack /
  Rabbit / Ape-In momentum plays. Fresh Meteora pools on exploding 5-minute volume,
  tight SOL-side bid-ask entries, volume-scaled sizing, fast mechanical exits.
when_to_use: When the active risk profile allocates a runner sleeve and a
  runner_scanner candidate has passed all gates — for sizing, entering, managing,
  and exiting runner positions. Never for core or satellite slots.
created: '2026-08-19T00:00:00Z'
source: agent:meteora_regime_lp (LP Army library synthesis — see project doc lparmy-strategy-database.md)
---

# Runner Playbook — fast fees on fresh volume, mechanically

The human versions (Beefman's heart attack, CryptoWhiskers' Rabbit, HMC's Ape In) run on
15-second reads and reflexes. We keep the EDGE — a young token with accelerating 5-minute
volume prints outsized fees into tight ranges — and replace reflexes with hard mechanics.
**Volume is everything: it decides entry, size, hold, and exit.** (Rabbit rule.)

## Entry (only from a gated runner_scanner candidate)
1. `token_safety_check` on BaseMint must PASS + sellability sanity. Reject on any doubt —
   "there is always another graduation."
2. **SOL-quoted, single-sided quote bid-ask JUST below price** (side=1, strategyType 2,
   15–25 bins). NEVER double-sided on a runner — that's buying the top. NEVER above price.
3. **Size by volume tier** (scanner's SizeTier × the profile's runner unit):
   FULL (m5 ≥ $50k) · 2/3 ($20–50k) · 1/3 probe ($8–20k). High volume → bigger + longer;
   low volume → tiny + quick.
4. On EVERY runner open add an on-chain `lower_limit_price` stopgap ~8% below the range
   floor — protection between ticks. Record **at-entry m5 volume** in the journal (the
   decay exit keys on it).
5. controller_id in BOTH places; verify-after-create (search RUNNING) — as always.

## Manage (every tick; runner sleeve prefers frequency_sec 120–180)
Exit (stop, keep_position=false, then verify swap-back) on ANY:
- **Volume death:** current m5 < 40% of at-entry m5. The volume IS the product; no
  negotiation, exit at any PnL.
- **Hard SL:** net PnL ≤ −6%.
- **Ran away:** price above the range upper for a full tick while <20% filled → one
  re-chase maximum (fast re-chase rule), then drop the token for ≥2h.
- **Max hold:** 90 minutes. Runners are trades, not positions.
Flip option: if >60% filled AND m5 decaying but price holding → flip token-side above
(side=2) to distribute into the remaining momentum, then the same exits apply.

## Sleeve discipline (cannot be overridden by conviction)
- The runner budget is the PROFILE's % — losses never borrow from core/satellite sleeves.
- One runner per token, max concurrent per profile; never re-enter a dropped runner <2h.
- Scanner returns no gated candidate → sleeve PAUSES. Never loosen gates to find action.
- `SOURCE DEGRADED` or an open rate-limit circuit also means PAUSE until the next scheduled
  deep scan; never launch overlapping scanners to compensate for incomplete reach.
- Every cycle journals: token, at-entry m5, exit trigger, PnL, fees, volume booked —
  runner cycles are the volume engine for the Cup's 40% volume axis (~2× size per cycle).

## Risk profiles (set in config `risk_profile`; sleeve %s of total_amount_quote)
| Profile | SAFE core | MEDIUM satellites | RISK runners | Runner unit |
|---|---|---|---|---|
| `guardian` | 80% | 10% (1 slot) | 10% | half-size probes only |
| `balanced` | 60% | 20% (1–2 slots) | 20% | 1 runner |
| `hunter` | 40% | 40% (2 slots) | 20% | up to 2 runners |
Shared: ~10% USDC reserve lives inside SAFE; daily loss limit + drawdown killswitch on the
whole book; satellites keep their own gates (TVL≥$25k, sustained volume, regime≠CHAOTIC).

## Micro quick-in/out experiment

This is a separate, feature-flagged admission path inside the runner sleeve. It is disabled
by default and allowed only under `hunter`. `quick_in_out_guard` must return `ELIGIBLE` after
complete market sources, `token_safety_check` PASS, and a verified sell route. It requires
the first 2–8% retracement after a ≥12% bullish leg, ≥$200k m5 volume, ≥$50k TVL, pool age
1–12h, and buy/sell ratio ≥1.25.

One attempt per session, one slot, ≤2.5% of real equity, ≤12.5% of runner budget, ≤$50, and
never below the $20 economic floor. Enter SOL-quote single-sided below price in ≤15 bins.
Exit at −3%, +5%, 15 minutes, m5 below 60% of entry, sell dominance, or unreadable flow.
There is no flip, re-chase, averaging down, or second entry. These tighter terms override
the normal runner rules only for explicitly journalled micro executor ids.
