"""Persistent retry scheduling with injectable clocks and deterministic jitter."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Protocol

from dev_autopilot.errors import ErrorClass
from dev_autopilot.models import RetryPolicySpec, RetryState


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    def __init__(self, current: datetime) -> None:
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("fake clock requires a timezone-aware datetime")
        self.current = current.astimezone(UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class RetryScheduler:
    def __init__(self, policy: RetryPolicySpec, clock: Clock | None = None) -> None:
        self.policy = policy
        self.clock = clock or SystemClock()

    def schedule(
        self,
        *,
        owner: str,
        previous_count: int,
        error_class: ErrorClass,
        reason: str,
        run_id: str,
        explicit_reset_at: datetime | None = None,
    ) -> RetryState:
        count = previous_count + 1
        if count > self.policy.max_attempts:
            raise RuntimeError(f"retry budget exhausted for {owner}: {count - 1} attempts")
        index = min(count - 1, len(self.policy.delays_seconds) - 1)
        delay = float(self.policy.delays_seconds[index])
        if self.policy.jitter_fraction:
            digest = hashlib.sha256(f"{run_id}:{owner}:{count}".encode()).digest()
            unit = int.from_bytes(digest[:8], "big") / (2**64 - 1)
            signed = (unit * 2.0) - 1.0
            delay *= 1.0 + signed * self.policy.jitter_fraction
        proposed = self.clock.now() + timedelta(seconds=max(0.0, delay))
        if explicit_reset_at is not None:
            if explicit_reset_at.tzinfo is None or explicit_reset_at.utcoffset() is None:
                raise ValueError("quota reset time must be timezone-aware")
            proposed = max(proposed, explicit_reset_at.astimezone(UTC))
        return RetryState(
            owner=owner,
            count=count,
            error_class=error_class,
            next_attempt_at=proposed,
            last_reason=reason,
        )

    def due(self, state: RetryState) -> bool:
        return state.next_attempt_at is None or self.clock.now() >= state.next_attempt_at
