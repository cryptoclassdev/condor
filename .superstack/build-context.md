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
  security_score: B+
  quality_score: A-
  ready_for_mainnet: false
  dimensions:
    security: B+
    correctness: A-
    error_handling: A-
    testing: A-
    code_organization: B+
    documentation: A-
  critical_findings_open: 0
  findings:
    - severity: P1
      category: credentials
      description: Telegram reports a competing remote poller, so the old bot token cannot be trusted as single-owner.
      fix: Revoke/reissue it in BotFather, install through scripts/install_telegram_token.py, restart once, and prove exactly one poll owner.
    - severity: P1
      category: recovery
      description: Recreating Hummingbot API marks RUNNING executors SYSTEM_CLEANUP while their LPs may remain open on-chain.
      fix: Enforce a zero-RUNNING-executor preflight before API restart and retain exact chain close/verification as the recovery path.
    - severity: P2
      category: market-data
      description: Public GeckoTerminal runner discovery remains rate-limit-prone despite bounded retry and circuit-breaker behavior.
      fix: Add a Meteora-native equivalent source and retain fail-closed partial-coverage labeling until parity is proven.
  high_findings_fixed:
    - ACP bridge secrets removed from process arguments
    - frontend high-severity dependency advisories resolved
    - stale browser authentication no longer reconnects forever
    - Hummingbot API credential rotated with owner-only local files
    - deterministic executor outcome capture proven on a live sparse ACP event
    - frontend entry bundle split into stable dependency groups
  residual_work:
    - upload final demo video
    - revoke and reissue the Telegram bot token in BotFather, then install it with the hidden-input helper
    - complete the 24-48 hour fault-free soak
  live_recovery:
    credential_cutover_source_session: 27
    safely_closed_unmanaged_positions: 2
    restart_source_session: 29
    recovered_session: 30
    adopted_positions: 2
    current_managed_positions: 2
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
