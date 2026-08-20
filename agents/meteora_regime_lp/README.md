# Meteora Regime LP Operator

An autonomous Meteora DLMM operator for Condor that separates the book into core,
satellite, and runner sleeves while treating Solana chain state—not executor status or a
position cache—as the source of truth.

## Why it exists

An LP strategy can be directionally correct and still fail operationally. During live
mainnet testing, we observed creates reported failed after landing on-chain, closes reported
successful without sending a transaction, zero-proceeds close records, stale OPEN cache
rows, base-token proceeds hidden from quote-only wallet views, and mixed-quote exposure
reported in incompatible units.

This agent makes those failure modes visible and fails closed when authority data is
missing. It does not claim that fees equal profit; performance is sourced independently
from Meteora and benchmarked against holding when the API exposes enough fields.

## Safety loop

Every minute the strategy runs:

1. `orphan_guard` — reconcile RUNNING executors against on-chain owned positions.
2. `wallet_audit` — inventory liquid quote and stranded base tokens.
3. `capital_guard` — value wallet + chain positions in USD, apply gas/rent reserves, and
   cap sizing to actual equity.
4. `lifecycle_guard` — enforce exact UTC deadlines, stop proximity, and material fill-change
   regime rechecks.

Every fifth tick it runs market discovery. `meteora_pool_scanner` serves core/satellites;
`runner_scanner` independently searches young Meteora SOL pools with accelerating five-minute
volume. Candidates still require a regime classification and token safety check.

## Routines

| Routine | Purpose |
|---|---|
| `capital_guard` | Authoritative effective equity, reserve-adjusted entry capacity, sleeve and loss budgets |
| `lifecycle_guard` | Exact max-hold deadlines, fill transitions, stop and recheck actions |
| `orphan_guard` | Chain ↔ executor reconciliation, ghost/orphan handling, suspect-close detection |
| `wallet_audit` | Full wallet inventory, including stranded base-token proceeds |
| `meteora_truth` | Meteora-native PnL and vs-HODL evidence |
| `regime_engine` | CALM/RANGING/TRENDING/HOT/CHAOTIC classification |
| `runner_scanner` | Fresh-pool reach and five-minute volume acceleration |
| `token_safety_check` | Mint/freeze/holder and sellability gates |

## Verification

```bash
uv run pytest \
  tests/test_meteora_capital_guard.py \
  tests/test_meteora_lifecycle_guard.py \
  tests/test_meteora_runner_scanner.py \
  tests/test_risk_gate.py
```

The strategy currently targets `solana-mainnet-beta`. Treat all execution as live unless the
runtime connector itself proves otherwise.

Hackathon materials are in [`hackathon/`](../../hackathon/).

