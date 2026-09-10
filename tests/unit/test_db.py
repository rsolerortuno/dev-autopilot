from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from dev_autopilot.db import SCHEMA_VERSION, SQLiteStore
from dev_autopilot.errors import ErrorClass, PersistenceError, RunLockError
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
    assert second.schema_version == SCHEMA_VERSION


def test_future_schema_is_rejected_without_mutation(tmp_path) -> None:
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_migrations VALUES (99, 'future')")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(PersistenceError, match="unsupported database schema version 99"):
        SQLiteStore(path)
    assert path.read_bytes() == before


def test_backup_and_restore_round_trip_atomically(tmp_path, job) -> None:
    source = SQLiteStore(tmp_path / "source.sqlite3")
    run = source.create_run(job)
    backup = source.backup(tmp_path / "backup.sqlite3")
    restored = SQLiteStore.restore(backup, tmp_path / "restored.sqlite3")
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.get_run(run.run_id) == source.get_run(run.run_id)


def test_restore_protects_existing_destination(tmp_path, job) -> None:
    source = SQLiteStore(tmp_path / "source.sqlite3")
    source.create_run(job)
    backup = source.backup(tmp_path / "backup.sqlite3")
    destination = tmp_path / "destination.sqlite3"
    destination.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        SQLiteStore.restore(backup, destination)
    assert destination.read_bytes() == b"keep"


def test_restore_rejects_unrelated_sqlite_without_creating_target(tmp_path) -> None:
    source = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    destination = tmp_path / "destination.sqlite3"
    with pytest.raises(PersistenceError, match="not a Dev Autopilot"):
        SQLiteStore.restore(source, destination)
    assert not destination.exists()


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


def test_run_lock_renewal_is_atomic_and_does_not_resurrect_lost_lease(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = store.create_run(job)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store.acquire_lock(run.run_id, "owner", ttl_seconds=10, now=now)
    store.renew_lock(run.run_id, "owner", ttl_seconds=10, now=now + timedelta(seconds=9))
    store.assert_lock_owner(run.run_id, "owner", now=now + timedelta(seconds=18))
    with pytest.raises(RunLockError):
        store.renew_lock(run.run_id, "other", ttl_seconds=10, now=now + timedelta(seconds=18))
    store.acquire_lock(run.run_id, "other", ttl_seconds=10, now=now + timedelta(seconds=20))
    with pytest.raises(RunLockError):
        store.renew_lock(run.run_id, "owner", ttl_seconds=10, now=now + timedelta(seconds=20))
