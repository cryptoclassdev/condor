"""Small state machine for bounded degraded operation after model outages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimeoutCircuit:
    threshold: int = 2
    degraded_ticks: int = 2
    consecutive_timeouts: int = 0
    remaining_degraded_ticks: int = 0

    def should_attempt_model(self) -> bool:
        if self.remaining_degraded_ticks <= 0:
            return True
        self.remaining_degraded_ticks -= 1
        return False

    def record_timeout(self) -> None:
        self.consecutive_timeouts += 1
        if self.consecutive_timeouts >= max(1, self.threshold):
            self.remaining_degraded_ticks = max(1, self.degraded_ticks)
            self.consecutive_timeouts = 0

    def record_success(self) -> None:
        self.consecutive_timeouts = 0
        self.remaining_degraded_ticks = 0
