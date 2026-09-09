"""Versioned SQLite persistence with append-only events and run locks."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from uuid import UUID, uuid4

from dev_autopilot.errors import ErrorClass, PersistenceError, RunLockError
from dev_autopilot.events import EventType
from dev_autopilot.models import JobSpecification, RetryState, RunRecord
from dev_autopilot.states import WorkflowState

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    job_json TEXT NOT NULL,
    configuration_id TEXT NOT NULL,
    state TEXT NOT NULL,
    resume_state TEXT,
    stop_reason TEXT,
    failure_class TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    approved_at TEXT,
    archived_at TEXT,
    correction_rounds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    actor TEXT NOT NULL,
    reason TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TABLE IF NOT EXISTS phase_results (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    phase TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY(run_id, phase, input_hash)
);
CREATE TABLE IF NOT EXISTS retries (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    owner TEXT NOT NULL,
    count INTEGER NOT NULL,
    error_class TEXT NOT NULL,
    next_attempt_at TEXT,
    last_reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, owner)
);
CREATE TABLE IF NOT EXISTS run_locks (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    owner_token TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    name TEXT NOT NULL,
    digest TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, name, digest)
);
CREATE TABLE IF NOT EXISTS audit_cache (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    diff_sha256 TEXT NOT NULL,
    adapter TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, diff_sha256, adapter)
);
"""


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


def _required_dt(value: str | None, field: str) -> datetime:
    parsed = _dt(value)
    if parsed is None:
        raise PersistenceError(f"run has NULL {field}")
    return parsed


class _ClosingConnection(sqlite3.Connection):
    """Commit/rollback and close when used as a context manager."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
        return False


class SQLiteStore:
    """The single source of truth for orchestration state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._check_schema_compatibility(self.path)
        self._initialize()

    @staticmethod
    def _check_schema_compatibility(path: Path) -> None:
        """Reject databases written by a newer release before any mutation."""
        if not path.exists() or path.stat().st_size == 0:
            return
        try:
            connection = sqlite3.connect(path, uri=False)
            try:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
                if table is None:
                    return
                row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise PersistenceError(f"cannot inspect database schema: {path}") from exc
        version = None if row is None else row[0]
        if version is not None and (not isinstance(version, int) or version > SCHEMA_VERSION):
            raise PersistenceError(
                f"unsupported database schema version {version}; this release supports up to {SCHEMA_VERSION}"
            )

    @property
    def schema_version(self) -> int:
        """Return the highest schema migration applied to this database."""
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        if row is None or row[0] is None:
            raise PersistenceError("database has no schema version")
        return int(row[0])

    def backup(self, destination: str | Path) -> Path:
        """Create a consistent SQLite backup, atomically replacing destination."""
        target = Path(destination)
        if target.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the database path")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False) as handle:
                temporary_name = handle.name
            with self.connect() as source:
                destination_connection = sqlite3.connect(temporary_name)
                try:
                    source.backup(destination_connection)
                    check = destination_connection.execute("PRAGMA integrity_check").fetchone()
                    if check != ("ok",):
                        raise PersistenceError(f"backup integrity check failed for {target}")
                finally:
                    destination_connection.close()
            os.replace(temporary_name, target)
            temporary_name = None
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name)
        return target

    @classmethod
    def restore(cls, source: str | Path, destination: str | Path, *, overwrite: bool = False) -> SQLiteStore:
        """Restore a consistent backup into a new database path.

        Existing destinations are protected unless ``overwrite`` is explicit;
        replacement itself is atomic so a failed restore leaves the destination intact.
        """
        source_path = Path(source)
        target = Path(destination)
        if source_path.resolve() == target.resolve():
            raise ValueError("restore source and destination must differ")
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if target.exists() and not overwrite:
            raise FileExistsError(target)
        cls._check_schema_compatibility(source_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False) as handle:
                temporary_name = handle.name
            source_connection = sqlite3.connect(source_path)
            target_connection = sqlite3.connect(temporary_name)
            try:
                source_connection.backup(target_connection)
                check = target_connection.execute("PRAGMA integrity_check").fetchone()
                if check != ("ok",):
                    raise PersistenceError(f"restore integrity check failed for {target}")
            finally:
                target_connection.close()
                source_connection.close()
            os.replace(temporary_name, target)
            temporary_name = None
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name)
        return cls(target)

    backup_to = backup
    restore_from = restore

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _iso(datetime.now(UTC))),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_run(self, job: JobSpecification, *, run_id: UUID | None = None, now: datetime | None = None) -> RunRecord:
        run_uuid = run_id or uuid4()
        timestamp = now or datetime.now(UTC)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO runs(
                    run_id, job_json, configuration_id, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(run_uuid),
                    job.to_json(),
                    job.configuration_id,
                    WorkflowState.CREATED.value,
                    _iso(timestamp),
                    _iso(timestamp),
                ),
            )
            self._append_event_tx(
                connection,
                run_uuid,
                EventType.RUN_CREATED,
                actor="system",
                to_state=WorkflowState.CREATED,
                reason="run created",
                payload={"configuration_id": job.configuration_id},
                now=timestamp,
            )
        return self.get_run(run_uuid)

    def get_run(self, run_id: UUID | str) -> RunRecord:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise PersistenceError(f"run not found: {run_id}")
        return RunRecord(
            run_id=UUID(row["run_id"]),
            job=JobSpecification.from_json(row["job_json"]),
            state=WorkflowState(row["state"]),
            resume_state=(None if row["resume_state"] is None else WorkflowState(row["resume_state"])),
            stop_reason=row["stop_reason"],
            failure_class=(None if row["failure_class"] is None else ErrorClass(row["failure_class"])),
            created_at=_required_dt(row["created_at"], "created_at"),
            updated_at=_required_dt(row["updated_at"], "updated_at"),
            cancel_requested=bool(row["cancel_requested"]),
            approved_at=_dt(row["approved_at"]),
            archived_at=_dt(row["archived_at"]),
            correction_rounds=row["correction_rounds"],
        )

    def update_state(
        self,
        run_id: UUID | str,
        *,
        from_state: WorkflowState,
        to_state: WorkflowState,
        actor: str,
        reason: str,
        resume_state: WorkflowState | None = None,
        stop_reason: str | None = None,
        failure_class: ErrorClass | None = None,
        now: datetime | None = None,
    ) -> RunRecord:
        if not reason.strip():
            raise ValueError("transition reason must not be empty")
        timestamp = now or datetime.now(UTC)
        with self.transaction() as connection:
            row = connection.execute("SELECT state FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
            if row is None:
                raise PersistenceError(f"run not found: {run_id}")
            if row["state"] != from_state.value:
                raise PersistenceError(f"stale transition for {run_id}: expected {from_state}, found {row['state']}")
            connection.execute(
                """UPDATE runs SET state=?, resume_state=?, stop_reason=?,
                   failure_class=?, updated_at=? WHERE run_id=?""",
                (
                    to_state.value,
                    None if resume_state is None else resume_state.value,
                    stop_reason,
                    None if failure_class is None else failure_class.value,
                    _iso(timestamp),
                    str(run_id),
                ),
            )
            self._append_event_tx(
                connection,
                UUID(str(run_id)),
                EventType.TRANSITION,
                actor=actor,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                payload={},
                now=timestamp,
            )
        return self.get_run(run_id)

    def _append_event_tx(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        event_type: EventType,
        *,
        actor: str,
        reason: str | None,
        payload: dict[str, Any],
        now: datetime,
        from_state: WorkflowState | None = None,
        to_state: WorkflowState | None = None,
    ) -> None:
        next_sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), -1) + 1 FROM events WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO events(
                run_id, sequence, event_type, from_state, to_state, actor,
                reason, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(run_id),
                next_sequence,
                event_type.value,
                None if from_state is None else from_state.value,
                None if to_state is None else to_state.value,
                actor,
                reason,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                _iso(now),
            ),
        )

    def append_event(
        self,
        run_id: UUID | str,
        event_type: EventType,
        *,
        actor: str,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or datetime.now(UTC)
        with self.transaction() as connection:
            self._append_event_tx(
                connection,
                UUID(str(run_id)),
                event_type,
                actor=actor,
                reason=reason,
                payload=payload or {},
                now=timestamp,
            )

    def list_events(self, run_id: UUID | str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM events WHERE run_id=? ORDER BY sequence", (str(run_id),)).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_phase_result(
        self,
        run_id: UUID | str,
        phase: WorkflowState,
        input_hash: str,
        result: dict[str, Any],
        *,
        status: str = "SUCCESS",
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        started = started_at or datetime.now(UTC)
        completed = completed_at or datetime.now(UTC)
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO phase_results(
                    run_id, phase, input_hash, status, result_json, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(run_id),
                    phase.value,
                    input_hash,
                    status,
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    _iso(started),
                    _iso(completed),
                ),
            )

    def get_phase_result(self, run_id: UUID | str, phase: WorkflowState, input_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT result_json FROM phase_results
                   WHERE run_id=? AND phase=? AND input_hash=? AND status='SUCCESS'""",
                (str(run_id), phase.value, input_hash),
            ).fetchone()
        return None if row is None else json.loads(row["result_json"])

    def list_phase_results(self, run_id: UUID | str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT phase,input_hash,status,result_json,started_at,completed_at
                   FROM phase_results WHERE run_id=? ORDER BY completed_at,phase""",
                (str(run_id),),
            ).fetchall()
        return [
            {
                "phase": row["phase"],
                "input_hash": row["input_hash"],
                "status": row["status"],
                "result": json.loads(row["result_json"]),
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
            }
            for row in rows
        ]

    def set_retry(self, run_id: UUID | str, retry: RetryState, *, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO retries(
                    run_id, owner, count, error_class, next_attempt_at, last_reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, owner) DO UPDATE SET
                    count=excluded.count,
                    error_class=excluded.error_class,
                    next_attempt_at=excluded.next_attempt_at,
                    last_reason=excluded.last_reason,
                    updated_at=excluded.updated_at""",
                (
                    str(run_id),
                    retry.owner,
                    retry.count,
                    retry.error_class.value,
                    None if retry.next_attempt_at is None else _iso(retry.next_attempt_at),
                    retry.last_reason,
                    _iso(timestamp),
                ),
            )

    def get_retry(self, run_id: UUID | str, owner: str) -> RetryState | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM retries WHERE run_id=? AND owner=?",
                (str(run_id), owner),
            ).fetchone()
        if row is None:
            return None
        return RetryState(
            owner=row["owner"],
            count=row["count"],
            error_class=ErrorClass(row["error_class"]),
            next_attempt_at=_dt(row["next_attempt_at"]),
            last_reason=row["last_reason"],
        )

    def clear_retry(self, run_id: UUID | str, owner: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM retries WHERE run_id=? AND owner=?", (str(run_id), owner))

    def acquire_lock(
        self,
        run_id: UUID | str,
        owner_token: str,
        *,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or datetime.now(UTC)
        expires = timestamp + timedelta(seconds=ttl_seconds)
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM run_locks WHERE run_id=? AND expires_at <= ?",
                (str(run_id), _iso(timestamp)),
            )
            try:
                connection.execute(
                    "INSERT INTO run_locks(run_id, owner_token, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                    (str(run_id), owner_token, _iso(timestamp), _iso(expires)),
                )
            except sqlite3.IntegrityError as exc:
                row = connection.execute("SELECT owner_token FROM run_locks WHERE run_id=?", (str(run_id),)).fetchone()
                owner = "unknown" if row is None else row["owner_token"]
                raise RunLockError(f"run {run_id} is locked by {owner}") from exc

    def release_lock(self, run_id: UUID | str, owner_token: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM run_locks WHERE run_id=? AND owner_token=?",
                (str(run_id), owner_token),
            )
            if cursor.rowcount == 0:
                raise RunLockError(f"lock for run {run_id} is not owned by {owner_token}")

    def renew_lock(
        self,
        run_id: UUID | str,
        owner_token: str,
        *,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> None:
        """Atomically extend a live lease owned by ``owner_token``.

        Expired leases are never resurrected: after expiry another supervisor may
        acquire the run, and this owner must stop rather than persist work.
        """
        timestamp = now or datetime.now(UTC)
        expires = timestamp + timedelta(seconds=ttl_seconds)
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE run_locks SET expires_at=?
                   WHERE run_id=? AND owner_token=? AND expires_at > ?""",
                (_iso(expires), str(run_id), owner_token, _iso(timestamp)),
            )
            if cursor.rowcount != 1:
                raise RunLockError(f"live lock for run {run_id} is not owned by {owner_token}")

    def assert_lock_owner(self, run_id: UUID | str, owner_token: str, *, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM run_locks WHERE run_id=? AND owner_token=? AND expires_at > ?",
                (str(run_id), owner_token, _iso(timestamp)),
            ).fetchone()
        if row is None:
            raise RunLockError(f"live lock for run {run_id} is not owned by {owner_token}")

    def request_cancel(self, run_id: UUID | str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runs SET cancel_requested=1, updated_at=? WHERE run_id=?",
                (_iso(datetime.now(UTC)), str(run_id)),
            )
            if cursor.rowcount == 0:
                raise PersistenceError(f"run not found: {run_id}")

    def increment_correction_rounds(self, run_id: UUID | str) -> int:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE runs SET correction_rounds=correction_rounds+1 WHERE run_id=?",
                (str(run_id),),
            )
            value = connection.execute("SELECT correction_rounds FROM runs WHERE run_id=?", (str(run_id),)).fetchone()[0]
        return int(value)

    def mark_approved(self, run_id: UUID | str, *, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE runs SET approved_at=?, updated_at=? WHERE run_id=?",
                (_iso(timestamp), _iso(timestamp), str(run_id)),
            )

    def mark_archived(self, run_id: UUID | str, *, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE runs SET archived_at=?, updated_at=? WHERE run_id=?",
                (_iso(timestamp), _iso(timestamp), str(run_id)),
            )

    def put_audit_cache(self, run_id: UUID | str, diff_sha256: str, adapter: str, report: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO audit_cache(
                    run_id, diff_sha256, adapter, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    str(run_id),
                    diff_sha256,
                    adapter,
                    json.dumps(report, sort_keys=True, separators=(",", ":")),
                    _iso(datetime.now(UTC)),
                ),
            )

    def get_audit_cache(self, run_id: UUID | str, diff_sha256: str, adapter: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM audit_cache WHERE run_id=? AND diff_sha256=? AND adapter=?",
                (str(run_id), diff_sha256, adapter),
            ).fetchone()
        return None if row is None else json.loads(row["report_json"])
