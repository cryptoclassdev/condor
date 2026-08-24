# Judge quick start — Regime LP Operator

Regime LP Operator is a Condor agent for Meteora DLMM. It selects liquidity ranges from
market regime, divides capital into independent core/satellite/runner sleeves, and verifies
executor claims against Solana before treating a position as open or closed.

The repository contains no custom Solana program or custodial vault. It orchestrates the
existing Hummingbot API, Gateway, Jupiter and Meteora integrations.

## Five-minute code and safety check

Prerequisites: Python 3.12+, `uv`, Node.js/npm and `tmux`.

```bash
git clone https://github.com/cryptoclassdev/condor.git
cd condor
git switch meteora-cup
make install
make verify-meteora
```

`make verify-meteora` runs the Meteora agent regression suite, checks strategy/routine
discovery, runs the three-profile frontend tests and builds the production frontend. It does
not connect a wallet or submit a transaction.

The important files are:

- `agents/meteora_regime_lp/AGENT.md` — agent identity and capabilities;
- `agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md` — complete unattended
  strategy and risk policy;
- `agents/meteora_regime_lp/routines/` — deterministic measurements and guards;
- `agents/meteora_regime_lp/host_outcomes.py` — deterministic pre/post executor evidence
  adapter and durable pending reconciliation;
- `agents/meteora_regime_lp/wallet_truth.py` — direct native SOL, legacy SPL and Token-2022
  ownership adapter used for capital-critical wallet reads;
- `hackathon/submission.md` — copy-ready application;
- `hackathon/demo-script.md` — sub-three-minute recording plan.

## Run Condor

1. Start Hummingbot API and Gateway.
2. Configure Condor with `/servers` and `/gateway`.
3. Confirm the runtime exposes `solana-mainnet-beta`, `meteora/clmm`, and `jupiter/router`.
4. Start Condor:

```bash
make run
make status
```

Open **Agents → Meteora Regime LP → Regime LP Operator → Start New Session**. The start
dialog presents three risk profiles:

| Profile | Core / Satellite / Runner | Use |
|---|---:|---|
| Guardian | 80% / 10% / 10% | Safest |
| Balanced | 60% / 20% / 20% | Slightly risky; default |
| Hunter | 40% / 40% / 20% | More risky |

Choose **Loop**. The first tick adopts any already-open Meteora positions before considering
new capital. A deliberate stop stays stopped; an ungraceful process restart creates one fresh
session and repeats adoption.

## What a healthy first tick proves

- On-chain positions and RUNNING LP executors reconcile, or the book fails closed.
- Wallet and LP equity are valued in USD before sleeve and loss budgets are calculated.
- Token-2022 proceeds cannot disappear behind a legacy-token-only portfolio cache.
- Missing authority data is `UNKNOWN`, never zero or absent.
- Every satellite or runner has a UTC deadline.
- No scanner candidate means pause, never a forced entry.

The strategy targets mainnet because the official finals trade real capital. Do not fund a
judge installation merely to inspect it. To test execution, use the smallest supported amount
and complete `hackathon/finals-test-checklist.md` first.

## Optional experiments

The SOL hedge planner and hunter-only micro quick-in/out strategy are shipped disabled. Their
read-only planners can be tested without credentials or transactions. They remain unavailable
for live execution until the documented recovery and smallest-size validation gates pass.

## Common setup failures

- A missing public fork URL is a submission-packaging blocker, not a runtime failure.
- If no agent appears, rerun `make verify-meteora` and confirm every required routine is
  discovered.
- If GeckoTerminal is rate limited, runner discovery reports `SOURCE DEGRADED` or
  `SOURCE UNAVAILABLE` and pauses; do not loosen thresholds.
- Executor status and the Gateway position cache are not proof of chain state. Use
  `orphan_guard`, `wallet_audit`, and Meteora's portfolio view.
