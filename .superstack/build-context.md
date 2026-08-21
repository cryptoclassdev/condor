# Build Context

```yaml
defi:
  protocol_type: yield
  integration: existing Meteora DLMM execution through Condor LP Executors
  program_id: null
  security_review: self
  oracle_integration: GeckoTerminal plus on-chain Meteora position truth
  emergency_pause: true
build_status:
  milestones:
    - restart-safe Meteora regime LP operator
    - selectable guardian, balanced, and hunter profiles
    - fail-closed runner discovery and hedge-readiness guards
    - hunter-only micro quick-in/out policy and lifecycle guards (disabled by default)
  mvp_complete: false
  tests_passing: true
  devnet_deployed: false
  program_id: null
review:
  completed_at: 2026-08-22
  overall_grade: B+
  dimensions:
    security: B+
    correctness: A-
    error_handling: A-
    testing: A-
    code_organization: B+
    documentation: A-
  critical_findings_open: 0
  high_findings_fixed:
    - ACP bridge secrets removed from process arguments
    - frontend high-severity dependency advisories resolved
    - stale browser authentication no longer reconnects forever
  residual_work:
    - upload final demo video
    - deterministic host capture for every reconciled outcome
    - rotate previously process-visible local credentials before high-capital use
    - split the large frontend bundle
  live_recovery:
    source_session: 26
    recovered_session: 27
    adopted_positions: 2
    duplicate_entries: 0
    chain_reconciliation: clean
hackathon:
  competition: Agent Builders Cup 1
  team: Meteora
  repository: https://github.com/cryptoclassdev/condor/tree/meteora-cup
  workshop: https://www.youtube.com/live/UUjgZoiZ_2g
  submission_deadline: 2026-08-31
  demo_video: pending-user-recording
```

This project integrates an existing Solana liquidity protocol; it does not deploy a custom
program or take custody through a new vault. Strategy-policy changes must remain disabled
until their pure decision logic, lifecycle exits, and small-capital dry-run evidence pass.
