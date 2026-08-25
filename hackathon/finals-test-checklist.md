# Finals validation and capital-graduation checklist

Run this checklist from a clean public clone before increasing live position sizes or
recording the final demo. Save the relevant Condor reports and transaction/position links;
never include credentials, Telegram identifiers, private RPC URLs, or local paths.

## 1. Clean-install proof

- Clone the public fork into a new directory and switch to the submission branch.
- Run `make install`, configure Hummingbot API/Gateway, then run `make run`.
- Confirm the agent and `regime_lp_operator` appear without copying runtime folders.
- Run the agent-specific suite from the README and the full `uv run pytest` suite.
- Run `cd frontend && npm test && npm run build`.

## 2. Startup profiles

For `guardian`, `balanced`, and `hunter`:

- Open **Start New Session** and confirm all three profile cards show their sleeve split.
- Select the profile, start a dry run, and verify the session config records that profile.
- Confirm the session still contains `min_wallet_sol_reserve`, daily-loss, drawdown, and
  open-slot limits after the dialog's generic overrides are applied.
- Confirm an agent without a `profiles` block still gets the normal generic start dialog.

## 3. Chain truth and lifecycle

- Start with a known Meteora LP and record its address, token composition, and wallet value.
- Confirm `orphan_guard` reports equal on-chain positions and RUNNING executors.
- Confirm `wallet_audit` plus `capital_guard` explain wallet + LP equity within rounding.
- From a flat wallet, confirm the first/core entry leaves the configured minimum deposit,
  rent, gas floor, and execution buffer available for both remaining target slots.
- Seed a small non-quote residual, confirm `inventory_cleanup_guard` emits one exact-mint
  SOL cleanup plan, and confirm the following tick verifies source-token decrease plus SOL
  increase before the proceeds become deployable. Confirm USDC/USDT remain untouched.
- Exercise one create and one close at the smallest practical size; verify each in both the
  executor registry and Meteora/on-chain state before accepting the reported result.
- Confirm a satellite/runner has an exact UTC deadline and that lifecycle expiry produces a
  chain-verified close or a loud unresolved state.

## 4. Crash recovery

- With one known LP open, record the active session and executor IDs.
- Kill only the validated Condor process ungracefully, then start Condor again.
- Confirm the old session becomes interrupted and exactly one replacement session starts.
- Confirm the first replacement tick adopts the existing LP and does not create a duplicate.
- Confirm a deliberate stop remains stopped and does not auto-relaunch.
- Never recreate/restart Hummingbot API while an executor is RUNNING. The current API marks
  those executors `SYSTEM_CLEANUP` but can leave their LP positions open on-chain. If an API
  restart is unavoidable, first close and chain-verify every LP, then restart and reconcile.

## 5. Runner scanner

- Run `runner_scanner` directly and confirm valid timestamps no longer become `ageUnknown`.
- Confirm the 17 public feed requests are paced, HTTP 429 is retried with bounded backoff, and a
  partial result starts with `SOURCE DEGRADED` rather than presenting itself as full market reach.
- Record the full reach/gate breakdown. A zero-candidate result must name the real binding
  gate and leave the sleeve paused.
- Confirm every returned candidate is Meteora, SOL-paired, inside the configured age/TVL
  windows, above the five-minute volume and acceleration gates, and not already held.
- Run `token_safety_check` before any runner dry-run recommendation.

## 6. Hedge readiness

- With no perpetual credential, run `hedge_guard` and confirm it is read-only and reports
  `READINESS ONLY`.
- Confirm the SOL input equals actual SOL inside OPEN LPs plus pending SOL fees—not LP value,
  wallet gas reserve, or configured capital.
- Confirm stale/zero prices and zero/unreadable equity produce `BLOCKED`.
- Confirm targets are capped at 25% of actual equity and adjustments below $10 are held.
- After a venue is configured, test at the smallest supported notional: increase the short,
  re-read the position, reduce LP SOL exposure, then verify the hedge reduces rather than
  becoming a naked short. Keep `hedge.enabled: false` until this entire sequence passes.

## 7. Micro quick-in/out experiment

- Keep `quick_in_out.enabled: false` during the normal three-profile validation.
- In dry run, prove guardian and balanced always block the experiment.
- With hunter and synthetic/recorded market inputs, prove every signal, source, safety,
  sell-route, slot, attempt, and capital gate fails closed independently.
- Confirm a sub-$800 test book blocks whenever its capped deposit is below $20.
- At adequate test equity, allow exactly one $20–$50 dry-run plan; verify the executor is
  tagged as micro and lifecycle exits fire at −3%, +5%, or 15 minutes.
- Prove `quick_in_out_guard(mode="monitor")` exits on m5 decay, sell dominance, or unreadable
  flow. Do not enable live mode until create/close and residual-token recovery are verified.

## 8. Capital graduation

Increase capital only after the preceding checks pass on the same commit:

1. Run at the current size for at least one full discovery/lifecycle cycle.
2. Increase one position ceiling at a time; do not change profile and capital simultaneously.
3. At each stage confirm reserves, daily-loss dollars, sleeve ceilings, rent, and executor
   count are derived from observed equity.
4. Stop graduation immediately on any orphan, ghost, unexplained wallet delta, stale price,
   missed deadline, duplicate open, or safety-gate bypass.

## 9. Competition accounting and race clock

- Confirm the finals config contains the organizer-provided `competition.end_at_utc` and
  `competition.enabled: true`. A missing or malformed timestamp must block new entries.
- Prove `competition_guard` allows normal operation before the cutoff, blocks new entries for
  the final 135 minutes, emits `EXIT_ALL_NOW` in the final 15 minutes, and holds cash after
  chain/wallet-confirmed closure.
- Confirm all reported competition volume is **gross filled notional**. Never label deposited
  LP capital or whole-pool volume as the agent's scored volume.
- Include trading fees, gas, rent, slippage and cleanup in P&L decisions; fees come from the
  same $800 account.
- Confirm no code path consumes a live leaderboard, crosses the entrant's own activity, or
  coordinates volume with another agent.
- Verify every remaining position can be removed and residual inventory recovered before the
  organizer-forced end-of-run close.

## 10. Submission freeze

- Replace repository and demo placeholders with public URLs.
- Refresh the evidence table from the final run; label timestamps and disclose losses.
- Record the demo from the frozen commit and show the profile selector, recovery evidence,
  current reconciliation, and honest Meteora performance.
- Verify the public clone installs without files ignored by Git.
- Submit early for organizer feedback, but treat **August 31, 2026** as the code freeze.

## 11. Credential cutover

- Confirm the Hummingbot API has zero RUNNING executors before rotating or restarting it.
- Rotate the API password with `uv run python scripts/rotate_hummingbot_api_password.py`; never
  pass a secret on the command line. Recreate the API only after the zero-executor proof.
- In BotFather, revoke/reissue the Telegram bot token. Install it from a private terminal with
  `uv run python scripts/install_telegram_token.py`; input is hidden and the ignored `.env`
  remains mode 0600.
- Restart Condor once and verify API authentication, exactly one Telegram poll owner, clean
  chain reconciliation, and a completed supervision tick before increasing capital.
