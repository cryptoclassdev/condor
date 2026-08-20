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

## Quick Start

### 1. Install Condor and its dependencies

Use the public hackathon fork once it is published. The URL below is intentionally a
placeholder until that external step is complete; do not substitute the upstream Condor
repository because it does not contain this branch yet.

```bash
git clone <PUBLIC_FORK_URL>
cd condor
git switch meteora-cup
make install
```

`make install` runs Condor's interactive setup and installs the Python and frontend
dependencies. Never commit the generated `.env`, `config.yml`, wallet material, API
credentials, reports, or strategy session directories.

### 2. Connect Hummingbot API and Gateway

1. Start Hummingbot API and its Gateway service.
2. In Condor, configure the API connection with `/servers` and the Gateway wallet with
   `/gateway`.
3. Verify that the runtime exposes `solana-mainnet-beta`, the `meteora/clmm` LP provider, and
   the `jupiter/router` swap provider.
4. Fund the selected Solana wallet with SOL for gas/rent and the assets required by the
   chosen pool. The configured `$800` is only a ceiling: `capital_guard` sizes from observed
   wallet plus on-chain LP equity.

The strategy is currently mainnet-only. A different runtime network causes new entries to
pause rather than silently switching environments.

### 3. Choose a risk profile

Set `default_config.risk_profile` in
`strategies/regime_lp_operator/strategy.md` before starting the session:

| Profile | User-facing level | Core / Satellite / Runner | Intended use |
|---|---|---:|---|
| `guardian` | Safest | 80% / 10% / 10% | Maximum SOL/USDC core weight and one small satellite/runner slot |
| `balanced` | Slightly risky | 60% / 20% / 20% | Default mix of core yield and bounded speculative sleeves |
| `hunter` | More risky | 40% / 40% / 20% | More satellite capacity while retaining hard sleeve and loss limits |

These percentages split the risk capital reported by `capital_guard`; they do not authorize
the agent to spend the nominal configuration ceiling. The current Condor start dialog does
not yet expose this as a dropdown, so the profile must be selected in configuration before
launch.

### 4. Start the operator

```bash
make run
make status
```

Open the Condor dashboard, then navigate to **Agents → Meteora Regime LP → Regime LP
Operator → Start New Session**. Select **Loop**. On its first tick the operator searches for
RUNNING LP executors and adopts still-open positions before considering a new entry.

The default configuration sets `restart_on_boot: true`. After an ungraceful Condor process
failure, boot reconciliation marks the interrupted session, creates a fresh session, and
repeats the adoption checks. A deliberate `make stop` is different: it is treated as an
operator-requested stop and does not auto-relaunch the strategy.

### 5. Verify the first tick

Before leaving the operator unattended, confirm the session journal reports:

- on-chain positions equal RUNNING executors;
- zero unclassified authority reads, or a fail-closed hold if an authority read failed;
- effective equity and reserve-adjusted entry capacity from `capital_guard`;
- an explicit UTC deadline for every satellite or runner position;
- no forced entry when the relevant scanner returns zero candidates.

Run the agent-specific regression suite with:

```bash
uv run pytest \
  tests/test_meteora_restart_recovery.py \
  tests/test_meteora_capital_guard.py \
  tests/test_meteora_lifecycle_guard.py \
  tests/test_meteora_runner_scanner.py \
  tests/test_risk_gate.py
```

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
