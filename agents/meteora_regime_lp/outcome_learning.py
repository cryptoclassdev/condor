"""Bounded learning from LP execution outcomes.

The public interface is intentionally small: callers submit one authoritative
``PositionAttempt`` plus the prior ``LearningState`` and receive a
``LearningResult``.  Classification, retry safety, cooldowns, and later policy
adaptation stay local to this module.  Hard portfolio risk limits are not part
of the adaptive state and therefore cannot be changed here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


Action = Literal["create", "close", "monitor"]
BASELINE_POLICY_PATH = Path(__file__).with_name("baseline_policy.json")


@dataclass(frozen=True)
class BaselinePolicy:
    version: int
    inventory_heavy_ratio: float
    minimum_verified_side_samples: int
    minimum_outperformance_margin_pct: float
    maximum_learned_bias: float


def load_baseline_policy(path: Path = BASELINE_POLICY_PATH) -> BaselinePolicy:
    """Load the tracked, wallet-agnostic policy shipped to every installation."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    policy = BaselinePolicy(**payload)
    if policy.version != 1:
        raise ValueError("unsupported baseline policy version")
    if not 0.5 <= policy.inventory_heavy_ratio <= 0.9:
        raise ValueError("unsafe inventory_heavy_ratio in baseline policy")
    if policy.minimum_verified_side_samples < 3:
        raise ValueError("side learning requires at least three verified samples")
    if not 0 <= policy.maximum_learned_bias <= 0.20:
        raise ValueError("maximum_learned_bias exceeds the bounded learning cap")
    return policy


@dataclass(frozen=True)
class SideContext:
    """Observable context used to choose an LP inventory direction."""

    pool_address: str
    sleeve: str
    regime: str
    base_asset: str
    quote_asset: str
    wallet_base_usd: float
    wallet_quote_usd: float
    trend_direction: str = ""


@dataclass(frozen=True)
class SideRecommendation:
    side: int
    placement: Literal["ABOVE", "BELOW", "CENTERED", "ABSTAIN"]
    intent: str
    reason: str


@dataclass(frozen=True)
class PositionAttempt:
    attempt_id: str
    action: Action
    pool_address: str
    sleeve: str
    executor_status: str = "UNKNOWN"
    chain_position_found: bool | None = None
    wallet_delta_usd: float | None = None
    error_message: str = ""
    confirmation_age_ticks: int = 0
    exit_reason: str = ""
    pnl_pct: float | None = None
    vs_hodl_pct: float | None = None
    observed_tick: int = 0
    entry_side: int | None = None
    entry_regime: str = ""


@dataclass(frozen=True)
class PoolGate:
    reason: str
    requires_reconciliation: bool
    release_after_tick: int | None = None


@dataclass(frozen=True)
class AdaptivePolicy:
    """Only parameters that the learner is authorized to tune."""

    entry_haircut_bps: int = 50
    rpc_backoff_scale: float = 1.0
    range_width_scale: float = 1.0


@dataclass(frozen=True)
class SidePerformance:
    verified_samples: int = 0
    cumulative_vs_hodl_pct: float = 0.0
    beat_hodl_count: int = 0


@dataclass(frozen=True)
class ActiveSideContext:
    entry_side: int
    entry_regime: str


@dataclass(frozen=True)
class LearningState:
    outcome_counts: dict[str, int] = field(default_factory=dict)
    consecutive_outcome: str = ""
    consecutive_count: int = 0
    pool_gates: dict[str, PoolGate] = field(default_factory=dict)
    observation_versions: dict[str, int] = field(default_factory=dict)
    policy: AdaptivePolicy = field(default_factory=AdaptivePolicy)
    side_performance: dict[str, SidePerformance] = field(default_factory=dict)
    active_side_contexts: dict[str, ActiveSideContext] = field(
        default_factory=dict
    )
    last_adjustment: str = ""


@dataclass(frozen=True)
class LearningResult:
    outcome: str
    retry_allowed: bool
    next_action: str
    explanation: str
    state: LearningState


@dataclass(frozen=True)
class EntryPermission:
    allowed: bool
    reason: str
    state: LearningState


class FileLearningStore:
    """Append evidence and atomically persist bounded cross-session state."""

    STATE_SCHEMA_VERSION = 1

    def __init__(self, root: Path):
        self.root = Path(root)
        self.state_path = self.root / "state.json"
        self.ledger_path = self.root / "outcomes.jsonl"

    def load_state(self) -> LearningState:
        if not self.state_path.exists():
            return LearningState()
        try:
            payload = json.loads(self.state_path.read_text())
            if payload.get("schema_version") != self.STATE_SCHEMA_VERSION:
                raise ValueError("unsupported outcome-learning state schema")
            state = payload["state"]
            return LearningState(
                outcome_counts={
                    str(key): int(value)
                    for key, value in state.get("outcome_counts", {}).items()
                },
                consecutive_outcome=str(state.get("consecutive_outcome", "")),
                consecutive_count=int(state.get("consecutive_count", 0)),
                pool_gates={
                    str(pool): PoolGate(**gate)
                    for pool, gate in state.get("pool_gates", {}).items()
                },
                observation_versions={
                    str(attempt_id): int(version)
                    for attempt_id, version in state.get(
                        "observation_versions", {}
                    ).items()
                },
                policy=AdaptivePolicy(**state.get("policy", {})),
                side_performance={
                    str(key): SidePerformance(**performance)
                    for key, performance in state.get(
                        "side_performance", {}
                    ).items()
                },
                active_side_contexts={
                    str(pool): ActiveSideContext(**context)
                    for pool, context in state.get(
                        "active_side_contexts", {}
                    ).items()
                },
                last_adjustment=str(state.get("last_adjustment", "")),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"unreadable outcome-learning state: {exc}") from exc

    def record(self, attempt: PositionAttempt, result: LearningResult) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        event = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "attempt": asdict(attempt),
            "outcome": result.outcome,
            "retry_allowed": result.retry_allowed,
            "next_action": result.next_action,
            "explanation": result.explanation,
            "effective_policy": asdict(result.state.policy),
            "adjustment": result.state.last_adjustment,
        }
        with self.ledger_path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(event, sort_keys=True) + "\n")

        self.save_state(result.state)

    def save_state(self, state: LearningState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.STATE_SCHEMA_VERSION,
            "state": asdict(state),
        }
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temporary.replace(self.state_path)


class OutcomeLearner:
    """Classify an observed attempt and return the safe next action."""

    def __init__(self, baseline: BaselinePolicy | None = None):
        self.baseline = baseline or load_baseline_policy()

    def recommend_side(
        self, context: SideContext, state: LearningState
    ) -> SideRecommendation:
        """Choose inventory direction without changing any hard risk limit."""
        regime = self._normalized_regime(context)
        total = max(0.0, context.wallet_base_usd) + max(
            0.0, context.wallet_quote_usd
        )
        base_share = context.wallet_base_usd / total if total else 0.0
        quote_share = context.wallet_quote_usd / total if total else 0.0

        if context.sleeve == "core":
            if regime in {"CHAOTIC", "HOT"}:
                return SideRecommendation(
                    side=0,
                    placement="ABSTAIN",
                    intent="DO_NOT_OPEN",
                    reason="The core does not open in a chaotic or HOT regime.",
                )
            heavy = self.baseline.inventory_heavy_ratio
            if regime in {"TRENDING_DOWN", "DOWN"} and quote_share < heavy:
                return SideRecommendation(
                    side=0,
                    placement="ABSTAIN",
                    intent="DO_NOT_OPEN",
                    reason=(
                        "Do not manufacture quote inventory by selling base into a "
                        "downtrend; wait for a safer regime."
                    ),
                )
            if base_share >= heavy and regime in {
                "CALM",
                "RANGING",
                "TRENDING_UP",
                "TRENDING",
            }:
                return self._apply_side_evidence(
                    context,
                    state,
                    SideRecommendation(
                        side=2,
                        placement="ABOVE",
                        intent="SELL_BASE_INTO_STRENGTH",
                        reason=(
                            "The core wallet is base-heavy in an uptrend; use existing "
                            "base inventory above spot instead of swapping it to quote."
                        ),
                    ),
                )
            if quote_share >= heavy:
                return self._apply_side_evidence(
                    context,
                    state,
                    SideRecommendation(
                        side=1,
                        placement="BELOW",
                        intent="BUY_BASE_ON_PULLBACK",
                        reason=(
                            "The core wallet is quote-heavy; use existing quote inventory "
                            "below spot instead of swapping into base."
                        ),
                    ),
                )
            if regime in {"CALM", "RANGING"}:
                return self._apply_side_evidence(
                    context,
                    state,
                    SideRecommendation(
                        side=3,
                        placement="CENTERED",
                        intent="MARKET_MAKE_BOTH_SIDES",
                        reason=(
                            "The core wallet is balanced and the regime is stable enough "
                            "for centered two-sided liquidity."
                        ),
                    ),
                )

        return self._apply_side_evidence(
            context,
            state,
            SideRecommendation(
                side=1,
                placement="BELOW",
                intent="BUY_BASE_ON_PULLBACK",
                reason="Use quote inventory below spot for a defensive pullback entry.",
            ),
        )

    def _apply_side_evidence(
        self,
        context: SideContext,
        state: LearningState,
        recommendation: SideRecommendation,
    ) -> SideRecommendation:
        """Suspend a context only after repeated, verified HODL underperformance."""
        regime = self._normalized_regime(context)
        key = f"{context.sleeve}|{regime}|{recommendation.side}"
        performance = state.side_performance.get(key)
        if (
            performance is None
            or performance.verified_samples
            < self.baseline.minimum_verified_side_samples
        ):
            return recommendation
        average = (
            performance.cumulative_vs_hodl_pct / performance.verified_samples
        )
        majority_beat_hodl = (
            performance.beat_hodl_count * 2 >= performance.verified_samples
        )
        if (
            average <= -self.baseline.minimum_outperformance_margin_pct
            and not majority_beat_hodl
        ):
            return SideRecommendation(
                side=0,
                placement="ABSTAIN",
                intent="DO_NOT_OPEN",
                reason=(
                    f"Side {recommendation.side} in {regime} underperformed HODL "
                    f"by {average:.2f}% on average across "
                    f"{performance.verified_samples} verified closes; suspend this "
                    "context until reviewed or stronger evidence is recorded."
                ),
            )
        return recommendation

    @staticmethod
    def _normalized_regime(context: SideContext) -> str:
        regime = context.regime.strip().upper().replace(" ", "_")
        direction = context.trend_direction.strip().upper()
        if regime == "TRENDING" and direction in {"UP", "DOWN"}:
            return f"TRENDING_{direction}"
        return regime

    def entry_permission(
        self, pool_address: str, observed_tick: int, state: LearningState
    ) -> EntryPermission:
        gate = state.pool_gates.get(pool_address)
        if gate is None:
            return EntryPermission(True, "no-active-pool-gate", state)
        if gate.requires_reconciliation:
            return EntryPermission(False, gate.reason, state)
        if gate.release_after_tick is None or observed_tick < gate.release_after_tick:
            return EntryPermission(False, gate.reason, state)

        gates = dict(state.pool_gates)
        gates.pop(pool_address, None)
        return EntryPermission(
            True,
            "expired-pool-gate-released",
            replace(state, pool_gates=gates),
        )

    def observe(
        self, attempt: PositionAttempt, state: LearningState
    ) -> LearningResult:
        version = max(
            attempt.confirmation_age_ticks,
            attempt.observed_tick if attempt.action in {"close", "monitor"} else 0,
        )
        prior_version = state.observation_versions.get(attempt.attempt_id)
        if prior_version is not None and version <= prior_version:
            return LearningResult(
                outcome="DUPLICATE_OBSERVATION",
                retry_allowed=False,
                next_action="NOOP",
                explanation=(
                    "This attempt observation version was already recorded; it cannot "
                    "count twice or trigger another policy adjustment."
                ),
                state=state,
            )

        result = self._observe_new(attempt, state)
        versions = dict(result.state.observation_versions)
        versions[attempt.attempt_id] = version
        return replace(
            result,
            state=replace(result.state, observation_versions=versions),
        )

    def _observe_new(
        self, attempt: PositionAttempt, state: LearningState
    ) -> LearningResult:
        status = attempt.executor_status.strip().upper()
        if (
            attempt.action == "close"
            and status in {"COMPLETED", "CLOSED", "STOPPED", "SUCCESS"}
            and attempt.chain_position_found is False
        ):
            active = state.active_side_contexts.get(attempt.pool_address)
            if active is not None and (
                attempt.entry_side is None or not attempt.entry_regime.strip()
            ):
                attempt = replace(
                    attempt,
                    entry_side=attempt.entry_side or active.entry_side,
                    entry_regime=attempt.entry_regime or active.entry_regime,
                )
            state = self._record_side_performance(attempt, state)
            if (
                attempt.entry_side in {1, 2, 3}
                and attempt.entry_regime.strip()
                and attempt.vs_hodl_pct is not None
            ):
                active_contexts = dict(state.active_side_contexts)
                active_contexts.pop(attempt.pool_address, None)
                state = replace(state, active_side_contexts=active_contexts)
        wallet_moved = (
            attempt.wallet_delta_usd is not None
            and abs(attempt.wallet_delta_usd) >= 0.01
        )

        exit_reason = attempt.exit_reason.strip().lower()
        if (
            attempt.action == "close"
            and status in {"COMPLETED", "CLOSED", "STOPPED", "SUCCESS"}
            and attempt.chain_position_found is True
        ):
            outcome = "FALSE_SUCCESS_ORPHAN"
            gates = dict(state.pool_gates)
            gates[attempt.pool_address] = PoolGate(
                reason="false-success-close-orphan",
                requires_reconciliation=True,
            )
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="RECOVER_CLOSE_ORPHAN",
                explanation=(
                    "The executor reported a successful close but the next authority "
                    "read still owns the position; quarantine the pool and recover "
                    "that address before any re-entry."
                ),
                state=self._count(state, outcome, pool_gates=gates),
            )

        if (
            attempt.action == "close"
            and status in {"COMPLETED", "CLOSED", "STOPPED", "SUCCESS"}
            and attempt.chain_position_found is False
            and ("stop" in exit_reason or "loss" in exit_reason)
        ):
            outcome = "STRATEGY_STOP_LOSS"
            gates = dict(state.pool_gates)
            gates[attempt.pool_address] = PoolGate(
                reason="strategy-stop-loss",
                requires_reconciliation=False,
                release_after_tick=attempt.observed_tick + 30,
            )
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="COOLDOWN_AND_RESCAN",
                explanation=(
                    "The position was confirmed closed by its hard stop; cool this "
                    "pool for 30 supervision ticks and require a fresh "
                    "candidate/regime read."
                ),
                state=self._count(state, outcome, pool_gates=gates),
            )

        close_causes = (
            ("volume", "STRATEGY_VOLUME_DECAY_EXIT", "volume-decay", 30),
            ("max-hold", "STRATEGY_MAX_HOLD_EXIT", "max-hold", 10),
            ("max hold", "STRATEGY_MAX_HOLD_EXIT", "max-hold", 10),
            ("out-of-range", "STRATEGY_OUT_OF_RANGE_EXIT", "out-of-range", 10),
            ("out of range", "STRATEGY_OUT_OF_RANGE_EXIT", "out-of-range", 10),
        )
        if (
            attempt.action == "close"
            and status in {"COMPLETED", "CLOSED", "STOPPED", "SUCCESS"}
            and attempt.chain_position_found is False
        ):
            for marker, outcome, gate_reason, cooldown_ticks in close_causes:
                if marker not in exit_reason:
                    continue
                gates = dict(state.pool_gates)
                gates[attempt.pool_address] = PoolGate(
                    reason=gate_reason,
                    requires_reconciliation=False,
                    release_after_tick=attempt.observed_tick + cooldown_ticks,
                )
                return LearningResult(
                    outcome=outcome,
                    retry_allowed=False,
                    next_action="COOLDOWN_AND_RESCAN",
                    explanation=(
                        f"The position was confirmed closed for {gate_reason}; cool "
                        "the pool, then require fresh discovery and regime evidence."
                    ),
                    state=self._count(state, outcome, pool_gates=gates),
                )

            underperformed = (
                attempt.pnl_pct is not None
                and attempt.pnl_pct < 0
            ) or (
                attempt.vs_hodl_pct is not None
                and attempt.vs_hodl_pct < 0
            )
            if underperformed:
                outcome = "STRATEGY_UNDERPERFORMANCE"
                gates = dict(state.pool_gates)
                gates[attempt.pool_address] = PoolGate(
                    reason="strategy-underperformance",
                    requires_reconciliation=False,
                    release_after_tick=attempt.observed_tick + 15,
                )
                return LearningResult(
                    outcome=outcome,
                    retry_allowed=False,
                    next_action="COOLDOWN_AND_RESCAN",
                    explanation=(
                        "The confirmed close lost value or underperformed HODL without "
                        "a more specific lifecycle cause; cool the pool for diagnostic "
                        "review and require fresh evidence."
                    ),
                    state=self._count(state, outcome, pool_gates=gates),
                )

            profitable = (
                attempt.pnl_pct is not None
                and attempt.pnl_pct >= 0
                and (
                    attempt.vs_hodl_pct is None
                    or attempt.vs_hodl_pct >= 0
                )
            )
            outcome = (
                "CONFIRMED_PROFIT_EXIT" if profitable else "CONFIRMED_CLOSE"
            )
            gates = dict(state.pool_gates)
            gates.pop(attempt.pool_address, None)
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="RESCAN_NEXT_DEEP_TICK",
                explanation=(
                    "The position is confirmed closed; no failure cooldown is needed, "
                    "but the next entry still requires fresh discovery and regime data."
                ),
                state=self._count(state, outcome, pool_gates=gates),
            )

        if (
            attempt.action == "create"
            and status in {"RUNNING", "SUCCESS", "COMPLETED"}
            and attempt.chain_position_found is True
            and wallet_moved
        ):
            outcome = "CONFIRMED_SUCCESS"
            gates = dict(state.pool_gates)
            gates.pop(attempt.pool_address, None)
            state = self._remember_active_side(attempt, state)
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="MONITOR",
                explanation=(
                    "Executor, chain ownership, and wallet movement agree that the "
                    "position was created."
                ),
                state=self._count(state, outcome, pool_gates=gates),
            )

        if attempt.action == "create" and status in {"FAILED", "ERROR"} and (
            attempt.chain_position_found is True or wallet_moved
        ):
            outcome = "FALSE_FAILURE_ORPHAN"
            gates = dict(state.pool_gates)
            gates[attempt.pool_address] = PoolGate(
                reason="false-failure-orphan",
                requires_reconciliation=True,
            )
            next_state = self._count(state, outcome, pool_gates=gates)
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="ADOPT_OR_RECOVER",
                explanation=(
                    "The executor reported failure but authoritative chain or wallet "
                    "state moved; reconcile and adopt/recover before any new create."
                ),
                state=next_state,
            )

        error = attempt.error_message.lower()
        if any(
            marker in error
            for marker in (
                "429",
                "too many requests",
                "server disconnected",
                "fetch failed",
                "timed out",
                "timeout",
                "transient error",
            )
        ):
            outcome = "RPC_TRANSIENT"
            next_state = self._count(state, outcome)
            if next_state.outcome_counts[outcome] % 2 == 0:
                old = next_state.policy.rpc_backoff_scale
                new = min(4.0, round(old * 1.5, 2))
                if new != old:
                    next_state = replace(
                        next_state,
                        policy=replace(next_state.policy, rpc_backoff_scale=new),
                        last_adjustment=f"rpc_backoff_scale:{old}->{new}",
                    )
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="RETRY_RECONCILIATION",
                explanation=(
                    "Upstream authority data is transiently unavailable; hold all "
                    "transactional decisions and retry reconciliation with backoff."
                ),
                state=next_state,
            )

        if (
            attempt.action == "create"
            and status in {"FAILED", "ERROR"}
            and attempt.confirmation_age_ticks >= 1
            and attempt.chain_position_found is False
            and not wallet_moved
            and any(
                marker in error
                for marker in (
                    "maximum bin count",
                    "max bin count",
                    "invalid range",
                    "range must bracket",
                    "lower price",
                    "upper price",
                )
            )
        ):
            outcome = "INVALID_RANGE"
            next_state = self._count(state, outcome)
            if next_state.outcome_counts[outcome] % 2 == 0:
                old = next_state.policy.range_width_scale
                new = max(0.6, round(old - 0.1, 2))
                if new != old:
                    next_state = replace(
                        next_state,
                        policy=replace(next_state.policy, range_width_scale=new),
                        last_adjustment=f"range_width_scale:{old}->{new}",
                    )
            return LearningResult(
                outcome=outcome,
                retry_allowed=True,
                next_action="REBUILD_RANGE_NEXT_DEEP_TICK",
                explanation=(
                    "The failed create left no chain or wallet footprint and the "
                    "simulation rejected its range; rebuild it from a fresh price "
                    "using the bounded width scale."
                ),
                state=next_state,
            )

        if (
            attempt.action == "create"
            and status in {"FAILED", "ERROR"}
            and attempt.confirmation_age_ticks >= 1
            and attempt.chain_position_found is False
            and not wallet_moved
            and (
                "insufficient funds" in error
                or "insufficient_balance" in error
                or "custom program error: 0x1" in error
            )
        ):
            outcome = "INSUFFICIENT_BALANCE"
            next_state = self._count(state, outcome)
            if next_state.outcome_counts[outcome] % 2 == 0:
                old = next_state.policy.entry_haircut_bps
                new = min(200, old + 25)
                if new != old:
                    next_state = replace(
                        next_state,
                        policy=replace(next_state.policy, entry_haircut_bps=new),
                        last_adjustment=f"entry_haircut_bps:{old}->{new}",
                    )
            return LearningResult(
                outcome=outcome,
                retry_allowed=True,
                next_action="RETRY_NEXT_DEEP_TICK",
                explanation=(
                    "The failed create left no chain or wallet footprint and reported "
                    "insufficient balance; retry once later using the exact-token cap "
                    "and the bounded funding haircut."
                ),
                state=next_state,
            )

        if (
            attempt.action == "create"
            and status in {"RUNNING", "SUCCESS", "COMPLETED"}
            and attempt.confirmation_age_ticks >= 1
            and attempt.chain_position_found is False
            and not wallet_moved
        ):
            outcome = "FALSE_SUCCESS_GHOST"
            gates = dict(state.pool_gates)
            gates[attempt.pool_address] = PoolGate(
                reason="false-success-ghost",
                requires_reconciliation=True,
            )
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="CLEAN_GHOST_THEN_RETRY",
                explanation=(
                    "The executor reported success but the next authority read and "
                    "wallet delta show no position; clean the ghost before retrying."
                ),
                state=self._count(state, outcome, pool_gates=gates),
            )

        if (
            attempt.action == "create"
            and status in {"TERMINATED", "CLOSED", "STOPPED"}
            and attempt.confirmation_age_ticks >= 2
            and attempt.chain_position_found is False
            and attempt.wallet_delta_usd is None
        ):
            outcome = "HISTORICAL_CREATE_TERMINATED"
            gates = dict(state.pool_gates)
            gates.pop(attempt.pool_address, None)
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="NOOP",
                explanation=(
                    "This pre-ledger create now has a terminal executor and no "
                    "on-chain position. Retire the stale reconciliation record "
                    "without treating missing historical wallet data as a failure."
                ),
                state=self._count(state, outcome, pool_gates=gates),
            )

        if (
            attempt.action == "create"
            and status in {"", "UNKNOWN"}
            and attempt.confirmation_age_ticks >= 10
            and attempt.chain_position_found is False
        ):
            outcome = "STALE_UNIDENTIFIED_CREATE"
            gates = dict(state.pool_gates)
            gates.pop(attempt.pool_address, None)
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="NOOP",
                explanation=(
                    "This pre-ledger create has no executor or position identity, "
                    "and a much later authority read owns nothing in the pool. "
                    "Retire the stale gate without guessing whether it once filled."
                ),
                state=self._count(state, outcome, pool_gates=gates),
            )

        if (
            attempt.action == "create"
            and status in {"RUNNING", "SUCCESS", "COMPLETED"}
            and attempt.confirmation_age_ticks == 0
            and attempt.chain_position_found is False
            and not wallet_moved
        ):
            outcome = "PENDING_CONFIRMATION"
            return LearningResult(
                outcome=outcome,
                retry_allowed=False,
                next_action="VERIFY_NEXT_TICK",
                explanation=(
                    "Same-tick executor state is not authoritative; wait for the next "
                    "chain and wallet reconciliation before classifying the create."
                ),
                state=self._count(state, outcome),
            )

        outcome = "UNCLASSIFIED"
        return LearningResult(
            outcome=outcome,
            retry_allowed=False,
            next_action="HOLD_AND_RECONCILE",
            explanation="Outcome lacks enough authoritative evidence to retry safely.",
            state=self._count(state, outcome),
        )

    @staticmethod
    def _remember_active_side(
        attempt: PositionAttempt, state: LearningState
    ) -> LearningState:
        if attempt.entry_side not in {1, 2, 3} or not attempt.entry_regime.strip():
            return state
        contexts = dict(state.active_side_contexts)
        contexts[attempt.pool_address] = ActiveSideContext(
            entry_side=attempt.entry_side,
            entry_regime=attempt.entry_regime.strip().upper().replace(" ", "_"),
        )
        return replace(state, active_side_contexts=contexts)

    @staticmethod
    def _record_side_performance(
        attempt: PositionAttempt, state: LearningState
    ) -> LearningState:
        """Learn only from confirmed closes with a HODL-relative result."""
        if (
            attempt.entry_side not in {1, 2, 3}
            or not attempt.entry_regime.strip()
            or attempt.vs_hodl_pct is None
        ):
            return state
        regime = attempt.entry_regime.strip().upper().replace(" ", "_")
        key = f"{attempt.sleeve}|{regime}|{attempt.entry_side}"
        performance = state.side_performance.get(key, SidePerformance())
        updated = SidePerformance(
            verified_samples=performance.verified_samples + 1,
            cumulative_vs_hodl_pct=round(
                performance.cumulative_vs_hodl_pct + attempt.vs_hodl_pct, 6
            ),
            beat_hodl_count=(
                performance.beat_hodl_count
                + (1 if attempt.vs_hodl_pct > 0 else 0)
            ),
        )
        side_performance = dict(state.side_performance)
        side_performance[key] = updated
        return replace(state, side_performance=side_performance)

    @staticmethod
    def _count(
        state: LearningState,
        outcome: str,
        *,
        pool_gates: dict[str, PoolGate] | None = None,
    ) -> LearningState:
        counts = dict(state.outcome_counts)
        counts[outcome] = counts.get(outcome, 0) + 1
        consecutive = (
            state.consecutive_count + 1
            if state.consecutive_outcome == outcome
            else 1
        )
        return replace(
            state,
            outcome_counts=counts,
            consecutive_outcome=outcome,
            consecutive_count=consecutive,
            pool_gates=(
                pool_gates
                if pool_gates is not None
                else dict(state.pool_gates)
            ),
            last_adjustment="",
        )
