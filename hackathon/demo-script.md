# Demo script — 2:55 target

## Before recording

- Refresh immediately before recording. Save the latest Orphan Guard, Wallet Audit, Capital
  Guard, Lifecycle Guard, Competition Guard, Runner Scanner and Meteora portfolio views.
- Record at 1080p; browser zoom 110–125%; hide bookmarks, wallet address, Telegram identifiers,
  RPC URLs, API keys and local filesystem paths.
- Pre-open Condor, the profile selector, the live session, the latest reports, Meteora
  Portfolio, the architecture section in `hackathon/submission.html`, and one Solana/Meteora
  position link if a position is live.
- Do not manufacture a trade for the video. If there is no live position, show the latest
  chain-verified completed/recovered lifecycle and say that the agent is currently holding.
- Keep terminal/code footage below 15 seconds. Cut all loading time.
- Replace bracketed evidence values below from the frozen recording run.

## Shot list and narration

### 0:00–0:18 — Hook

**Visual:** Condor and Meteora Portfolio side by side.

**Narration:** “Opening a liquidity position is easy. Staying in control when the RPC,
executor and position cache disagree is the hard part. Regime LP Operator is a Meteora agent
that trusts Solana before it trusts itself.”

### 0:18–0:38 — Three operating profiles

**Visual:** Start New Session dialog; move across the three profile cards without starting a
new session.

**Narration:** “A user chooses Guardian, Balanced or Hunter: safest, slightly risky or more
risky. Each divides measured capital across core, satellite and runner sleeves. The profile
changes opportunity, but never removes reserves, loss limits or recovery rules.”

### 0:38–1:06 — Chain truth

**Visual:** Latest Orphan Guard report. Highlight on-chain positions, executors, orphans,
ghosts, unclassified reads and phantom-cache rows.

**Narration:** “Every minute, this guard compares running executors with positions the wallet
actually owns. A stale cache row is not a position. An unreadable pool is not empty. The agent
acts only when the authoritative read is complete, so missing data can never authorize a close.”

### 1:06–1:31 — Capital truth

**Visual:** Wallet Audit followed by Capital Guard.

**Narration:** “Sizing starts from wallet plus chain-confirmed LP equity—not the configured
eight-hundred-dollar finals ceiling. Gas, refundable position rent, sleeve ceilings and the
daily-loss budget are recalculated from the real book before another dollar is deployed.”

### 1:31–1:58 — Regime-aware liquidity

**Visual:** Regime Engine and Strategy canvas; briefly show a range recommendation.

**Narration:** “Calm pools receive concentrated Curve liquidity. Ranging or rising markets use
quote-side Bid-Ask liquidity below price, earning while a pullback fills. Chaotic markets pause.
Runner discovery uses five-minute volume acceleration and reports degraded source coverage
instead of loosening gates to find action.”

### 1:58–2:28 — Recovery proof

**Visual:** Saved report/journal sequence for the false-success core stop: first mismatch,
second authoritative sighting, then confirmed removal and wallet return.

**Narration:** “Here a stop reported success, but the position remained on-chain—a real
false-success. The agent withheld the capital, confirmed the mismatch on a second tick, closed
the orphan, and verified the wallet return. The same doctrine also catches failed creates that
actually landed, preventing duplicate positions. Verified outcomes persist across restarts and
can adjust only bounded funding, range and backoff mechanics—never the hard risk controls.”

### 2:28–2:45 — Honest performance

**Visual:** Refreshed Meteora Portfolio metrics. Keep the values visible.

**Narration:** “Meteora currently reports [TOTAL PNL] total P-and-L, [FEES CLAIMED] fees
claimed and [WIN RATE] win rate. We show the loss if it is negative, because fees and deposit
notional are not profit.”

### 2:45–2:55 — Close

**Visual:** Condor live loop with the architecture card in the submission preview.

**Narration:** “Regime LP Operator is a chain-verified operating system for autonomous Meteora
liquidity. Its finals clock blocks late entries and verifies every close before forced
settlement—built to survive the full forty-eight-hour race.”

## Recording acceptance check

- Runtime is 2:55 or less and narration is natural, not read rapidly.
- The three profiles, a real agent decision, chain reconciliation, measured capital, recovery
  evidence and honest Meteora P&L are all visible.
- No secrets or local-only paths are visible.
- Upload as an accessible public or unlisted video and replace `<DEMO_VIDEO_URL>` in the
  submission package.
