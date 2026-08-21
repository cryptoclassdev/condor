# Organizer Q&A — competition rules that affect the agent

Source: organizer responses supplied by the builder on **August 21, 2026**. This file
paraphrases the supplied Q&A and separates confirmed rules from statements that remain
provisional. It is an internal implementation reference, not a substitute for the final
published competition rules.

## Confirmed

1. **Volume is gross filled notional.** Both legs count. Leveraged perpetual notional counts
   at full face value rather than margin posted.
2. **Scoring is rank-normalized.** Within P&L, volume, and voting, the highest-ranked agent
   receives 12 points and the lowest receives 1. Total score is 40% P&L points, 40% volume
   points, and 20% voting points.
3. **Trading fees are paid from the $800 competition account.** Solana entries run in new,
   dedicated wallets. Gate and Bitget entries use Foundation subaccounts.
4. **August 31 is the code-freeze deadline.** Applications submitted earlier may receive
   strategy feedback and can be revised before the freeze.
5. **All open positions are closed at the end of the 48-hour run.** Their marked/realized
   result counts toward final P&L.
6. **Crosses between two valid agents count.** Self-trading and multiple entries from one
   entrant are not intended to be possible and submitted agents will be vetted.

## Provisional or sponsor-dependent

- Organizers currently expect agents **not** to receive a live leaderboard inside the finals
  container. Build no strategy dependency on competitor state.
- Sponsors may prefer spot/LP agents over perpetual agents, or vice versa. The Meteora entry
  should remain an LP-first agent; the optional perpetual hedge stays disabled unless both the
  venue and sponsor treatment are confirmed.
- Reduced Gate/Bitget fees were still being requested. Assume normal fees until a final rate
  is confirmed.

## Product requirements derived from the answers

- Report competition volume as gross filled notional, never deployed LP capital or whole-pool
  volume. Keep pool flow as a separate market-selection signal.
- Optimize net P&L after trading fees, rent, gas, slippage, and residual-token cleanup.
- Do not build leaderboard chasing, self-crossing, or multi-agent wash-volume behavior.
- Require an exact finals end timestamp, block new entries near the end, and begin a
  deterministic wind-down before organizer-forced closure.
- Submit early enough to receive feedback, but treat August 31 as the final code freeze.
