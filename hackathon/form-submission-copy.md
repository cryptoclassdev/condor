# Botcamp form copy — Regime LP Operator

Prepared for an early **Agent Builders Cup 1 / Meteora** review on 25 August 2026.
Use the confident technical-founder version below. Replace only the three bracketed identity
and media placeholders before submitting.

## Required portal fields

### Project / agent name

Regime LP Operator

### Tagline

Deposit SOL. Choose your risk. Let the agent run your entire Meteora LP portfolio.

### Short description

Fund one wallet and walk away from day-to-day LP management. Regime LP Operator discovers
eligible Meteora pools, allocates capital, manages ranges, protects gains, exits risk,
rebalances, and cleans residual tokens automatically—with Guardian, Balanced, and Hunter
risk profiles and on-chain verification behind every action.

### Team / sponsor preference

Meteora — first preference

### Agent type

Condor autonomous trading agent

### Venue and protocols

Solana mainnet; Meteora DLMM through Hummingbot Gateway and LP Executors, with Jupiter used
only for required inventory conversion and residual cleanup.

### Repository

https://github.com/cryptoclassdev/condor/tree/meteora-cup

### Demo video

[DEMO VIDEO URL — upload before final submission]

### Builder / team

[YOUR PUBLIC NAME] — solo builder; product strategy, agent design, live testing and operations.

### Social or profile link

[YOUR GITHUB, X, OR LINKEDIN URL]

## Main strategy description

LP yield is rarely passive. Someone still has to compare pools, judge volatility, choose a
range, monitor price, take profit, cut deteriorating positions, rebalance capital and sell the
small token balances left behind after an exit. Regime LP Operator turns all of that into one
decision: fund a Solana wallet, choose a risk level and start the agent.

From that point, the agent runs the Meteora LP portfolio. It discovers and ranks eligible
pools, rejects unsafe or chaotic opportunities, allocates real wallet equity, opens positions,
manages ranges, protects gains, exits on risk or volume decay, rebalances into stronger
opportunities and converts residual inventory back toward SOL. Users choose Guardian, Balanced
or Hunter; the profile changes how capital is split between a deep SOL/USDC core, gated
satellite pools and a small high-velocity runner sleeve.

The automation is regime-aware rather than APR-chasing. Calm markets receive tighter Curve
liquidity, ranging or rising markets can use one-sided Bid-Ask placement, and unstable markets
pause. Position size comes from wallet plus chain-confirmed LP equity after gas, rent and slot
reserves—not from an optimistic configured balance.

The user experience can be simple because the operating layer is strict. Every create and
close is verified on a later authority read. A reported success without a
matching on-chain position or wallet movement remains unverified. A reported failure that did
land is adopted rather than duplicated. An append-only outcome ledger classifies indexing lag,
false-success ghosts, false-failure orphans, insufficient exact-token balance, invalid ranges,
RPC failures and market exits. Repeated evidence may tune only bounded execution details such
as funding haircut, range width and retry backoff; it can never relax the hard risk policy or
grant transaction authority.

The agent has been exercised with real Meteora positions on Solana mainnet. That testing found
two upstream DLMM close-path defects, partial wallet reads, stale position-cache rows and
rate-limit failure modes, and a portfolio cache that omitted Token-2022 balances, all of which
became recovery logic and regression tests. It has also proven restart-on-boot adoption of live
positions without duplicate entries. The current suite passes 1,561 tests locally.

For the 48-hour finals, the agent keeps pool volume, deposited capital and scored gross filled
notional separate. It stops new entries with 135 minutes remaining, begins a verified wind-down
in the final 15 minutes, and keeps the wallet flat through organizer settlement. The goal is
profitable volume with evidence—not churn, unverifiable fee claims or a promise of risk-free
yield.

## Why Meteora?

Meteora DLMM gives the agent a useful decision surface rather than a single passive LP choice:
bin width, distribution shape, one-sided placement and rebalancing timing can all respond to
market regime. Solana makes frequent, independently verifiable position and wallet reads
practical. The project uses those properties to turn DLMM liquidity management into a
transparent autonomous process whose decisions, failures and recoveries can be audited.

## What is technically different?

Most trading agents treat a successful tool response as a successful trade. This one verifies
both directions. It catches false successes where no position exists and false failures where
capital actually landed on-chain. Missing or unreadable data is never converted to zero, and
same-tick indexing lag cannot authorize a duplicate retry. The learning layer adapts execution
mechanics only after repeated reconciled evidence and is structurally unable to weaken token
safety, reserves, loss limits, sleeve ceilings or transaction permissions.

## How the strategy addresses the scoring model

The finals score is 40% P&L, 40% gross filled notional volume and 20% HBOT vote. Regime LP
Operator keeps a stable core while bounded satellite and runner sleeves can rotate into better
fee-flow opportunities. It measures five-minute volume acceleration for selection, but never
reports whole-pool volume or LP deposits as the agent's traded volume. Fees, gas, rent,
slippage, divergence loss and residual cleanup all remain costs in net P&L. The public journal
explains each decision and verification state for the voting layer.

## Risk management and safety

- Guardian, Balanced and Hunter use deterministic sleeve and slot limits.
- Wallet plus chain-confirmed LP value—not configured capital—is the sizing authority.
- Native SOL, legacy SPL and Token-2022 balances are read directly from Solana before sizing.
- Token authority, freeze authority, holder concentration, TVL and sustained-volume gates
  apply before satellite entry.
- Stop proximity, out-of-range duration, maximum hold, volume decay and competition time can
  force an exit independently of the LLM.
- Model timeouts enter degraded mode while deterministic supervision continues.
- Restart-on-boot adopts existing live executors and blocks duplicate entries.
- No custom custodial vault or new Solana program is introduced.
- The strategy pauses on incomplete market or wallet evidence.

## Current status and honest evidence

The agent is live-tested on Solana mainnet and currently operates through Condor, Hummingbot
API, Gateway and Meteora. On 24 August, Meteora's portfolio view reported approximately
$2.71K in lifetime deposits, $21.78 in claimed fees, -$4.07 total P&L (-0.15%), a 65.93% win
rate and a $3.82 largest completed win. These are dynamic historical portfolio figures, not
promised returns and not claims about competition volume. The latest direct audit found one
chain-confirmed SOL/USDC LP, no orphan or ghost positions, and no unresolved executor attempt.

## Technical stack

Python, Condor, Hummingbot API, Hummingbot LP Executors, Hummingbot Gateway, Meteora DLMM,
Jupiter routing, Solana owned-position reads, Meteora portfolio accounting, GeckoTerminal
discovery/OHLCV with bounded rate-limit handling, Pydantic, pytest, React and TypeScript.

## Judge quick start

```bash
git clone https://github.com/cryptoclassdev/condor.git
cd condor
git switch meteora-cup
make install
make verify-meteora
```

`make verify-meteora` validates the agent, strategy and routines without connecting a wallet
or submitting a transaction. Full setup and safe live-start instructions are in
`hackathon/JUDGE_QUICKSTART.md`.

## Additional notes / anything else

Live testing produced source-level diagnoses for two upstream close-path defects: an executor
can mark a position complete after an indeterminate position read without sending a close, and
a confirmed Meteora close can be reported with fabricated zero proceeds when post-confirmation
transaction data is briefly unavailable. The submission includes reproducible explanations,
impact analysis and proposed upstream fixes. These findings directly shaped the agent's
chain-first verification doctrine.

## Optional compact versions

### 160-character pitch

Deposit SOL, choose your risk, and let one agent discover, manage, rebalance and exit Meteora
LP positions for you.

### 60-second judge summary

Regime LP Operator turns Meteora LP into a fund-and-run product. A user funds one wallet,
chooses Guardian, Balanced or Hunter, and the agent handles pool discovery, allocation, range
management, gain protection, exits, rebalancing and residual cleanup. Underneath that simple
experience, every executor result is verified against Solana, existing positions are recovered
after restart, and hard capital and token-safety controls remain deterministic. It is built for
profitable autonomous liquidity—not APR chasing or manual position babysitting.

## Final pre-submit replacements

- Replace `[YOUR PUBLIC NAME]`.
- Replace `[YOUR GITHUB, X, OR LINKEDIN URL]`.
- Replace `[DEMO VIDEO URL — upload before final submission]`.
- Confirm the public branch includes the final reviewed changes before sending the early review.
