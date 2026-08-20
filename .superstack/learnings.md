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

### hedge-cost-secondary
- **Insight:** For the next cross-venue hedge proof, the user prioritizes validating hedge behavior over minimizing transaction cost, while deterministic exposure and loss limits still remain mandatory.
- **Confidence:** 10/10
- **Source:** learn
- **Files:** agents/meteora_regime_lp/strategies/regime_lp_operator/strategy.md
- **Date:** 2026-08-21

## Architecture

## Tools
