# Project Learnings

> Managed by `/learn`. Append-only — latest entry wins on conflicts.

## Patterns

## Pitfalls

### startup-dialog-drops-domain-risk-config
- **Insight:** The generic Start New Session dialog exposes no `risk_profile` selector and shallowly replaces the entire strategy `risk_limits` mapping with generic fields, so starting this agent through that dialog can drop its reserve, daily-loss, and shutdown limits unless the UI merge behavior is fixed.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** frontend/src/components/agent/AgentControls.tsx, agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md
- **Date:** 2026-08-21

### startup-dialog-drops-domain-risk-config
- **Insight:** Resolved: the start dialog now presents configured risk profiles and sends the selected `risk_profile`; recursive backend merging preserves strategy-specific nested risk limits.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** frontend/src/components/agent/AgentControls.tsx, frontend/src/components/agent/startSessionConfig.ts, condor/agents/config.py
- **Date:** 2026-08-21

### runner-age-parser-unreachable
- **Insight:** The repeated `ageUnknown` result was caused by `_age_hours()` returning early because its timestamp parser had been stranded after another function's return; after restoring the parser, a live scan reported ageUnknown 0/157 and produced real young/old/volume gate counts.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** agents/meteora_regime_lp/routines/runner_scanner.py, tests/test_meteora_runner_scanner.py
- **Date:** 2026-08-21

### runner-source-burst-hides-market-reach
- **Insight:** Launching all 17 GeckoTerminal discovery requests concurrently can exhaust the shared public rate limit and make partial reach look like a quiet market; pace requests, retry HTTP 429 with bounded backoff, and label degraded coverage explicitly.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** agents/meteora_regime_lp/routines/runner_scanner.py, tests/test_meteora_runner_scanner.py
- **Date:** 2026-08-21

### agent-routine-hot-reload-can-skew-helper-versions
- **Insight:** Condor hot-reloads an edited agent routine without necessarily reloading its imported helper module; adding new helper parameters/classes can therefore break the running loop even when the files and tests are correct. New routines must tolerate the prior helper signature or explicitly reload a pure helper module.
- **Confidence:** 10/10
- **Source:** implementation
- **Files:** agents/meteora_regime_lp/routines/lifecycle_guard.py, agents/meteora_regime_lp/routines/quick_in_out_guard.py
- **Date:** 2026-08-21

## Preferences

### hackathon-demo-deferred
- **Insight:** The user will record and upload the hackathon demo later; keep the 2:50 demo script and recording checklist ready but do not treat the entry as submission-complete until a public video URL exists.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** hackathon/demo-script.md, hackathon/submission.md
- **Date:** 2026-08-21

### final-validation-before-publication
- **Insight:** The user plans to run additional clarity and functional tests tomorrow before publishing the fork or freezing the finals candidate.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** agents/meteora_regime_lp/README.md
- **Date:** 2026-08-21

### volume-scoring-research-pending
- **Insight:** The user will ask in the Hummingbot Discord how LP-attributed volume is calculated for the Agent Builders Cup; do not invent or infer that metric in submission claims.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** hackathon/submission.md, agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md
- **Date:** 2026-08-21

### age-unknown-relaxation-pending
- **Insight:** Keep the runner `ageUnknown` relaxation pending; the intended future direction is to allow a tightly sized candidate with unknown age only when strong current volume and all remaining safety gates pass.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** agents/meteora_regime_lp/routines/runner_scanner.py
- **Date:** 2026-08-21

### age-unknown-relaxation-pending
- **Insight:** Superseded: do not relax unknown-age entries; the parser bug is fixed and the scanner now receives usable ages, so unknown age remains a fail-closed rejection.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** agents/meteora_regime_lp/routines/runner_scanner.py
- **Date:** 2026-08-21

### hedge-cost-secondary
- **Insight:** For the next cross-venue hedge proof, the user prioritizes validating hedge behavior over minimizing transaction cost, while deterministic exposure and loss limits still remain mandatory.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md
- **Date:** 2026-08-21

### micro-quick-in-out-capital-floor
- **Insight:** The LP Army first-retracement idea is only suitable here as a hunter-only, one-attempt micro experiment. A 2.5%-of-equity cap correctly blocks the current ~$82 book because the resulting deposit is below the $20 rent-aware floor; enablement must wait for adequate test equity and dry-run exit proof.
- **Confidence:** 10/10
- **Source:** implementation
- **Files:** agents/meteora_regime_lp/quick_in_out.py, agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md
- **Date:** 2026-08-21

## Architecture

## Tools
