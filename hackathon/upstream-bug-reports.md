# Upstream issue drafts — verify versions before filing

These two high-severity defects were isolated during live Solana/Meteora testing. They are
submission evidence and filing drafts, not claims that upstream has accepted the diagnosis.
The analysis targeted `hummingbot/gateway` v2.16.0 at `f090f4a` and Hummingbot at `2bfaccc`.
Confirm the Botcamp Docker versions and current source before filing.

Local environment check on 21 August 2026: the running Gateway reports version **2.16.0**,
image digest `sha256:34d59dedff78b2d31d74b4afb4a8f1b85ffdfdbd7e83751449612162de099578`.
Its image labels do not expose a source commit and it is configured as mutable `latest`, so
the source revision still must be confirmed before line-specific reports are filed.

## 1. LP executor can complete without sending a close

**Component:** Hummingbot `LPExecutor._close_position` and Gateway position reads
**Observed impact:** a funded position remains on-chain without a supervising executor.

Gateway's `get_position_info` catches broad read/transport/parse failures and returns `None`.
The LP executor interprets that same `None` as authoritative proof that the position is gone,
emits an already-closed event, transitions to `COMPLETE`, and never sends or retries a close.
A transient RPC failure can therefore become a false-successful close.

Suggested fix:

- reserve `None` for authoritative not-found and raise or return an explicit unknown state for
  transport and parse failures;
- never transition to `COMPLETE` on an indeterminate read;
- keep the executor in `CLOSING` for retry, or attempt the idempotent close and handle the
  authoritative not-found response.

Regime LP Operator mitigates this externally by reconciling executor claims with chain-owned
positions. Unknown reads remain unclassified and cannot authorize ghost cleanup.

## 2. Confirmed Meteora close can be booked with zero proceeds

**Components:** Gateway Meteora CLMM close route and Hummingbot Gateway connector
**Observed impact:** a real close is recorded with zero withdrawn tokens, fees, rent and
transaction fee, corrupting downstream P&L.

The close route performs a single `getTransaction` immediately after confirmation. RPC lag can
return `null`, causing a `status: 0` response with a valid signature but no `data`. The connector
checks only for a signature, ignores `status`, and defaults every missing accounting field to
zero. The close is then treated as settled.

The same route also keeps only the final signature from a multi-transaction close, so earlier
balance changes can be omitted; a partial multi-transaction failure does not preserve which
signatures landed.

Suggested fix:

- reuse confirmation transaction data or fetch it with the existing bounded retry helper;
- require a settled status before booking amounts;
- aggregate balance changes across every transaction signature;
- report landed signatures when a later transaction fails.

Regime LP Operator treats zero-proceeds close records as suspicious and uses wallet and chain
reconciliation instead of accepting them as authoritative P&L.
