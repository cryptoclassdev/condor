# Demo script — 2:50 target

## Recording checklist

- Record at 1080p; browser zoom at 110–125%.
- Pre-open Condor, the Meteora portfolio, the latest orphan report, and the strategy canvas.
- Hide wallet secrets, RPC URLs, Telegram identifiers, local filesystem paths, and API keys.
- Do not open a position for the recording. The two existing LPs are enough to prove the loop.
- Keep terminal/code footage under 15 seconds total.

## Shot list and narration

### 0:00–0:18 — Hook: the real failure mode

**Visual:** Meteora portfolio with the two live positions.

**Narration:** “Automating an LP entry is easy. Keeping control when your RPC, executor, and
cache disagree is the hard part. This is Regime LP Operator, a live Meteora agent that trusts
Solana before it trusts itself.”

### 0:18–0:42 — Product overview

**Visual:** Condor strategy canvas and sleeve summary.

**Narration:** “The book has three independent sleeves: a SOL/USDC core, gated SOL-quoted
satellites, and short-lived runner probes driven by five-minute volume. Volatility decides
the range shape; sleeve budgets prevent one idea from borrowing another idea's risk.”

### 0:42–1:10 — Capital truth

**Visual:** `capital_guard` report with effective equity, daily loss budget, and reserves.

**Narration:** “The config ceiling is eight hundred dollars, but the chain and wallet show a
seventy-seven-dollar book. The guard therefore makes the six-percent loss budget about four
dollars and sixty-four cents—not forty-eight—and deducts gas and position rent before any
new entry can be sized.”

### 1:10–1:37 — Chain reconciliation

**Visual:** Orphan Guard report: two on-chain, two executors, zero orphan, zero ghost, thirty
phantoms with only five rows rendered.

**Narration:** “The execution cache says thirty old positions are still open. Solana says
they are not. We label them phantom cache records and never close them. The two real
positions match two running executors, so the live book is consistent.”

### 1:37–2:02 — Lifecycle intelligence

**Visual:** Lifecycle Guard row for XST/SOL showing fill percentage and exact UTC deadline.

**Narration:** “Inventory conversion is the risk state. This position moved from mostly quote
into token inventory, so a twenty-point fill change forces a fresh regime check. Its
satellite deadline is explicit—17:47:22 UTC—not a guessed runner deadline.”

### 2:02–2:23 — Runner discovery

**Visual:** Runner Scanner REACH/GATES output, then a passing-candidate table if one exists.

**Narration:** “Runner discovery runs every five minutes. The report separates network-wide
feed reach from actual Meteora gates, so ‘other venue’ records can never be mislabeled as a
broken market filter. No candidate means pause, never force a trade.”

### 2:23–2:42 — Honest performance

**Visual:** Meteora Truth report and the live portfolio PnL.

**Narration:** “Performance comes directly from Meteora. Lifetime PnL is currently negative
two dollars and eighty-two cents. We show it because fees are not the same as profit, and a
credible agent must learn from losses instead of hiding them.”

### 2:42–2:50 — Close

**Visual:** Condor + Meteora split view.

**Narration:** “Regime LP Operator is not a trade screenshot. It is a chain-verified operating
system for autonomous liquidity.”

