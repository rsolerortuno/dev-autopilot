from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dev_autopilot.errors import ErrorClass
from dev_autopilot.models import RetryPolicySpec
from dev_autopilot.retries import FakeClock, RetryScheduler


def test_retry_schedule_is_deterministic_and_honors_reset() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    policy = RetryPolicySpec(delays_seconds=(60, 300), max_attempts=2, jitter_fraction=0.0)
    scheduler = RetryScheduler(policy, FakeClock(now))
    reset = now + timedelta(seconds=120)
    state = scheduler.schedule(
        owner="agy",
        previous_count=0,
        error_class=ErrorClass.RETRYABLE_QUOTA,
        reason="quota",
        run_id="run",
        explicit_reset_at=reset,
    )
    assert state.next_attempt_at == reset
    assert not scheduler.due(state)


def test_retry_budget_exhaustion() -> None:
    policy = RetryPolicySpec(delays_seconds=(0,), max_attempts=1, jitter_fraction=0.0)
    scheduler = RetryScheduler(policy, FakeClock(datetime.now(UTC)))
    with pytest.raises(RuntimeError, match="exhausted"):
        scheduler.schedule(
            owner="codex",
            previous_count=1,
            error_class=ErrorClass.RETRYABLE_AGENT_ERROR,
            reason="again",
            run_id="run",
        )
