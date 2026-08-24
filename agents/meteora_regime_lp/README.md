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
2. `wallet_audit` — read native SOL plus legacy SPL and Token-2022 accounts directly from
   Solana, then inventory liquid quote and stranded base tokens.
3. `capital_guard` — value wallet + chain positions in USD, apply gas/rent reserves, and
   cap sizing to actual equity.
4. `lifecycle_guard` — enforce exact UTC deadlines, stop proximity, and material fill-change
   regime rechecks.
5. `competition_guard` — during finals only, block late entries and start deterministic
   wind-down before the organizer closes every remaining position.

Condor's host observes every executor create/stop deterministically, stores it as pending, and
reconciles it on a later tick against executor detail, Solana-owned positions, and wallet
movement. The append-only event ledger survives restarts, and unresolved attempts block the
same pool before the model can retry. Repeated failures may tune only bounded execution mechanics
(funding haircut, range-width scale, and RPC backoff); portfolio risk controls are outside
the learner's interface and cannot be relaxed by it. Confirmed closes are attributed by
exit reason; a hard-stop adds a 30-tick pool cooldown, and every later entry must pass the
learner's persistent pool-gate check first.

Every fifth tick it runs market discovery. `meteora_pool_scanner` serves core/satellites;
`runner_scanner` independently searches young Meteora SOL pools with accelerating five-minute
volume. Candidates still require a regime classification and token safety check. Runner
discovery paces the public GeckoTerminal requests below its published limit, backs off on HTTP
429, opens a provider circuit after two exhausted feeds, and labels partial source coverage so
an upstream failure cannot be mistaken for a quiet market or monopolize a supervision tick.

## Quick Start

### 1. Install Condor and its dependencies

Use the public hackathon fork and its frozen submission branch; do not substitute the upstream
Condor repository because it does not contain this agent.

```bash
git clone https://github.com/cryptoclassdev/condor.git
cd condor
git switch meteora-cup
make install
make verify-meteora
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

The **Start New Session** dialog presents the configured profiles as three selectable cards:

| Profile | User-facing level | Core / Satellite / Runner | Intended use |
|---|---|---:|---|
| `guardian` | Safest | 80% / 10% / 10% | Maximum SOL/USDC core weight and one small satellite/runner slot |
| `balanced` | Slightly risky | 60% / 20% / 20% | Default mix of core yield and bounded speculative sleeves |
| `hunter` | More risky | 40% / 40% / 20% | More satellite capacity while retaining hard sleeve and loss limits |

These percentages split the risk capital reported by `capital_guard`; they do not authorize
the agent to spend the nominal configuration ceiling. The selected profile is sent as a
per-session override; recursive config merging preserves the strategy's reserve, daily-loss,
and shutdown limits. Operators can still change `default_config.risk_profile` in
`strategies/regime_lp_operator/strategy.md` to choose the preselected default.

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

Do not confuse a Condor restart with a Hummingbot API restart. Condor can safely adopt the
API's still-RUNNING executors. Recreating the API container can instead mark them
`SYSTEM_CLEANUP` while leaving their LP positions open on-chain. Prove **zero RUNNING
executors** before an API restart; otherwise stop Condor, reconcile and close each exact
on-chain position first.

### 5. Verify the first tick

Before leaving the operator unattended, confirm the session journal reports:

- on-chain positions equal RUNNING executors;
- zero unclassified authority reads, or a fail-closed hold if an authority read failed;
- effective equity and reserve-adjusted entry capacity from `capital_guard`;
- an explicit UTC deadline for every satellite or runner position;
- no forced entry when the relevant scanner returns zero candidates.

Start a read-only soak evidence log with:

```bash
.venv/bin/python scripts/meteora_soak_audit.py
```

Each invocation audits the newest session's process, tick freshness, latest 0-orphan / 0-ghost
reconciliation, wallet-inventory evidence, risk state, and scanner health, then appends one
JSON sample to that session's ignored `soak_audit.jsonl`. A dead/stale loop or reconciliation
regression exits non-zero; known source degradation and incomplete historical evidence remain
visible as warnings. Repeated samples prove that ticks continue advancing during a 24–48 hour
unattended soak.

Verify the configured Telegram bot without interfering with its long-poll owner:

```bash
.venv/bin/python scripts/install_telegram_token.py --verify-current
```

This calls only Telegram's identity and webhook-status endpoints—never `getUpdates`—and never
prints the credential. Actual token revocation/reissue still happens in BotFather; install the
replacement through the helper's hidden prompt, then restart Condor once.

`hedge.enabled` remains false until a perpetual credential is configured. The read-only
`hedge_guard` can still prove sizing: it hedges measured SOL token inventory rather than LP
notional, caps the target to real equity, blocks stale price inputs, and refuses dust orders.

`quick_in_out.enabled` also remains false. This hunter-only experiment packages the
first-retracement quick-in/out idea inside the existing runner sleeve: one attempt, one slot,
at most 2.5% of real equity and $50, with a 3% stop, 5% take-profit, 15-minute deadline and
flow-decay exit. At the current small wallet it correctly blocks because the capped deposit
falls below the $20 rent-aware testing floor.

Run the complete judge-facing regression and frontend build with:

```bash
make verify-meteora
```

## Routines

| Routine | Purpose |
|---|---|
| `capital_guard` | Authoritative effective equity, reserve-adjusted entry capacity, sleeve and loss budgets |
| `competition_guard` | Finals entry cutoff and end-of-race wind-down clock |
| `hedge_guard` | Read-only SOL inventory hedge target with stale-price, dust, credential, and real-equity caps |
| `quick_in_out_guard` | Read-only hunter micro-entry/monitor gate for first-retracement flow |
| `lifecycle_guard` | Exact max-hold deadlines, fill transitions, stop and recheck actions |
| `orphan_guard` | Chain ↔ executor reconciliation, ghost/orphan handling, suspect-close detection |
| `outcome_learner` | Durable outcome classification, duplicate-retry blocking, and bounded execution adaptation |
| `wallet_audit` | Full wallet inventory, including stranded base-token proceeds |
| `meteora_truth` | Meteora-native PnL and vs-HODL evidence |
| `regime_engine` | CALM/RANGING/TRENDING/HOT/CHAOTIC classification |
| `runner_scanner` | Fresh-pool reach and five-minute volume acceleration |
| `token_safety_check` | Mint/freeze/holder and sellability gates |

## Verification

```bash
uv run pytest \
  tests/test_meteora_capital_guard.py \
  tests/test_meteora_competition_guard.py \
  tests/test_meteora_lifecycle_guard.py \
  tests/test_meteora_runner_scanner.py \
  tests/test_meteora_outcome_learning.py \
  tests/test_meteora_host_outcomes.py \
  tests/test_meteora_quick_in_out.py \
  tests/test_meteora_hedge_plan.py \
  tests/test_risk_gate.py
```

The strategy currently targets `solana-mainnet-beta`. Treat all execution as live unless the
runtime connector itself proves otherwise.

## Finals accounting

Organizer guidance defines volume as gross filled notional and scores volume, P&L and voting
by rank (40/40/20); deployed LP capital and whole-pool volume are not the agent's scored
volume. Trading fees come from the same $800 account. Finals configuration must enable
`competition_guard` with the exact end timestamp; it blocks new entries for the final 135
minutes and orders a verified all-position wind-down in the final 15 minutes. The default is
disabled because inventing the finals timestamp would be less safe than failing closed when
the real value is supplied.

Hackathon materials are in [`hackathon/`](../../hackathon/). Start with the
[`JUDGE_QUICKSTART.md`](../../hackathon/JUDGE_QUICKSTART.md), then use the
[`finals-test-checklist.md`](../../hackathon/finals-test-checklist.md) before increasing
capital or recording the submission demo.
