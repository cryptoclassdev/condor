---
on_kill_switch: flatten_all     # close ALL LP positions on shutdown (not keep_spot)
cancel_open_orders: true
---
# Emergency shutdown — Meteora Regime LP

This same cleanup applies when `competition_guard` emits `EXIT_ALL_NOW` during the final
15-minute race buffer. Organizer-forced closure is a backstop, not the preferred execution
path: closing early enough to verify chain removal and residual-token conversion gives the
agent control over the P&L that will be scored.

The deterministic winddown has already stopped this session's `lp_executor`s with
`keep_position=false` (removes on-chain liquidity, refunds position rent). You are the
best-effort cleanup pass on top of that floor.

> **⚠ THE SWAP-BACK LEG IS NOT GUARANTEED — VERIFY IT YOURSELF.** Liquidity removal and
> conversion to USDC are two separate legs; only the first is reliable. A clean executor
> status is NOT evidence the tokens were converted.

Now:
- Verify no LP position is still open: `get_portfolio_overview(include_lp_positions=True)`.
  Close any stragglers (stop the executor with `keep_position=false`; if it has no executor,
  close via the Gateway `/clmm/close` path).
- Read the **actual on-chain wallet balance** of every base mint this session touched and
  sell each whole balance back to USDC with an order_executor MARKET sell (mint in the
  trading pair). Skip only leftovers worth under ~$5. Re-read balances and retry any
  non-zero.
- **Pace the sells** — Jupiter throttling surfaces as `NO_ROUTE_FOUND` with "Rate limit"
  text: it is a rate limit, not a missing route. ~2s spacing with an API key, 15–20s without.
- Confirm each position's rent (~0.057 SOL) was refunded.
- `send_notification` with final realized PnL in USDC and a one-line summary per slot wound
  down.

Be decisive — the safety-critical closes are already done.
