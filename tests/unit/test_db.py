from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from dev_autopilot.db import SCHEMA_VERSION, SQLiteStore
from dev_autopilot.errors import ErrorClass, RunLockError
from dev_autopilot.models import RetryState
from dev_autopilot.states import WorkflowState


def test_run_and_events_survive_restart(tmp_path, job) -> None:
    path = tmp_path / "state.sqlite3"
    first = SQLiteStore(path)
    run = first.create_run(job)
    first.update_state(
        run.run_id,
        from_state=WorkflowState.CREATED,
        to_state=WorkflowState.PLAN_VALIDATION,
        actor="test",
        reason="advance",
    )
    second = SQLiteStore(path)
    assert second.get_run(run.run_id).state is WorkflowState.PLAN_VALIDATION
    events = second.list_events(run.run_id)
    assert [event["sequence"] for event in events] == [0, 1]
    assert SCHEMA_VERSION == 1


def test_events_are_append_only(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = store.create_run(job)
    with store.connect() as connection, pytest.raises(sqlite3.DatabaseError):
        connection.execute("UPDATE events SET reason='tampered' WHERE run_id=?", (str(run.run_id),))


def test_retry_state_persists(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = store.create_run(job)
    reset = datetime.now(UTC) + timedelta(minutes=5)
    store.set_retry(
        run.run_id,
        RetryState(
            owner="codex",
            count=2,
            error_class=ErrorClass.RETRYABLE_QUOTA,
            next_attempt_at=reset,
            last_reason="quota",
        ),
    )
    loaded = SQLiteStore(store.path).get_retry(run.run_id, "codex")
    assert loaded is not None
    assert loaded.count == 2
    assert loaded.next_attempt_at == reset


def test_run_lock_is_exclusive_and_expirable(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = store.create_run(job)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store.acquire_lock(run.run_id, "a", ttl_seconds=10, now=now)
    with pytest.raises(RunLockError):
        store.acquire_lock(run.run_id, "b", ttl_seconds=10, now=now)
    store.acquire_lock(run.run_id, "b", ttl_seconds=10, now=now + timedelta(seconds=11))
    store.release_lock(run.run_id, "b")
