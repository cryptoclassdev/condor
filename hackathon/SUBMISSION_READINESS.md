# Submission readiness — morning handoff

Status: **submission package approximately 90% ready**. The public fork exists; code, strategy,
tests, judge guide, application copy, HTML preview and recording plan are prepared. The remaining
blockers are the recorded demo, a clean-clone proof from the published branch, and final live
evidence. They must not be fabricated.

## Botcamp rule coverage

| Official requirement | Status | Evidence |
|---|---|---|
| Runnable Condor agent | Ready | `agents/meteora_regime_lp/AGENT.md` and discovered routines |
| Complete strategy code | Ready | Agent-local Python policy/routines plus regression suite |
| `strategy.md` | Ready | Full unattended strategy, profiles, limits and recovery behavior |
| Unattended 48-hour operation | Restart and open-position adoption proven; extended soak remains | `restart_on_boot`, `competition_guard`, session 26 → 27 recovery |
| Demo video | Script ready; recording/upload pending | `hackathon/demo-script.md` |
| Public code | Public fork ready; branch publication pending final verification | `https://github.com/cryptoclassdev/condor` |
| Code freeze safety | Checklist ready | `hackathon/finals-test-checklist.md` |

Botcamp's scoring is **rank-normalized** within **40% gross-filled-notional volume, 40% P&L
and 20% HBOT vote**: first receives 12 category points and last receives 1. Trading fees come
from the $800 account. The submission therefore positions the product around profitable
volume, capital survival and unusually transparent failure evidence—not raw fee,
deposit-notional or whole-pool-volume claims.

## Completed internally

- Guardian, Balanced and Hunter are selectable per session without erasing nested risk config.
- Restart-on-boot reconciliation is implemented and regression-tested.
- A real ungraceful restart marked session 18 interrupted and started session 19, which is now
  beyond tick 48; the remaining drill is restart while a pre-recorded position is open.
- Chain/executor/wallet/capital/lifecycle checks run independently of market discovery.
- A finals-only competition clock blocks entries in the last 135 minutes and orders verified
  wind-down in the last 15 minutes; the exact organizer end timestamp remains external input.
- Runner discovery distinguishes venue reach from market gates and reports degraded sources.
- GeckoTerminal 429 handling has a non-zero exponential backoff floor.
- Two consecutively exhausted rate-limited feeds open a scan-local circuit, preserving the
  mandatory LP supervision cadence and labeling the result degraded.
- An unresolved or unreadable executor pool can no longer authorize ghost cleanup.
- Cache rows from unreadable pools remain unclassified instead of being called phantoms.
- SOL hedge readiness and the hunter micro quick-in/out experiment are disabled by default.
- The demo, submission copy and judge installation path avoid unsupported profitability claims.

## External morning tasks

1. Publish the verified `meteora-cup` branch to the public fork.
2. Run the clean-clone install and `make verify-meteora` from that public branch.
3. Record and upload the final demo; replace `<DEMO_VIDEO_URL>`.
4. Refresh the evidence snapshot from Meteora and Condor immediately before recording.
5. Paste the final fields into Botcamp, rank Meteora first, review, and submit before August 31.

## Remaining product work after the 90% handoff

These improve finals performance but are not reasons to weaken current safety gates:

- File the two upstream close-path reports after confirming the finals Docker versions.
- Pin finals container images by digest instead of mutable `latest` tags.
- Prove the full satellite open → max-hold/exit → residual-swap cycle.
- Wire the sponsor's scored gross-filled-notional feed into evidence reporting when its finals
  interface is exposed; until then keep it distinct from pool volume and deployed LP capital.
- Replace GeckoTerminal OHLCV with a Meteora-native source where equivalent coverage exists.
- Validate the finals `$800` sleeve economics and choose Balanced or Hunter from evidence; do
  not change profile and capital on the same run.
- Enable the micro quick-in/out experiment only after a recorded dry run and one $20–$50
  smallest-size recovery proof.
- Add a configured perpetual venue only after the hedge can be proven to shrink when LP SOL
  exposure shrinks.

## Current live caveat

Recent live runs exposed systemic false-success close/create reporting and fast-moving core
ranges that can cross before confirmation. The agent recovered the observed orphan and now
holds rather than retrying indefinitely. A forced process crash with two positions open marked
session 26 interrupted, automatically started session 27, adopted both executors, reconciled
zero orphans/ghosts, and opened no duplicate. Larger-capital use should still follow a measured
soak and credential rotation rather than a single jump.

The bounded outcome learner and append-only evidence format are implemented and tested, but
automatic host-level capture after every action is not yet wired into the live engine. The
strategy invokes the routine after reconciliation; this is useful evidence, not permission to
claim that every live action is already learned automatically.
