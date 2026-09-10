"""Durable, fail-closed project and milestone call budgets."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BudgetConfig:
    project_id: str
    milestone_id: str
    max_calls: int
    max_micro_usd: int | None = None
    project_max_calls: int | None = None
    project_max_micro_usd: int | None = None


class BudgetExceeded(RuntimeError):
    pass


class ReservationAlreadyClaimed(RuntimeError):
    """The invocation has already started or completed and must not be replayed."""


class BudgetStore:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS budget_config (
              project_id TEXT NOT NULL, milestone_id TEXT NOT NULL,
              max_calls INTEGER NOT NULL, max_micro_usd INTEGER,
              project_max_calls INTEGER, project_max_micro_usd INTEGER,
              PRIMARY KEY(project_id, milestone_id));
            CREATE TABLE IF NOT EXISTS budget_reservation (
              call_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, milestone_id TEXT NOT NULL,
              estimated_micro_usd INTEGER NOT NULL, actual_micro_usd INTEGER,
              status TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch()));
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _validate(config: BudgetConfig) -> None:
        if not config.project_id or not config.milestone_id or config.max_calls < 1:
            raise ValueError("budget identifiers and max_calls are required")
        if any(v is not None and v < 0 for v in (config.max_micro_usd, config.project_max_calls, config.project_max_micro_usd)):
            raise ValueError("budget limits must be non-negative")

    def configure(self, config: BudgetConfig) -> None:
        self._validate(config)
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing_project = db.execute(
                "SELECT project_max_calls, project_max_micro_usd FROM budget_config WHERE project_id=? LIMIT 1",
                (config.project_id,),
            ).fetchone()
            if existing_project and tuple(existing_project) != (config.project_max_calls, config.project_max_micro_usd):
                db.rollback()
                raise ValueError("project budget configuration is immutable")
            existing = db.execute(
                "SELECT * FROM budget_config WHERE project_id=? AND milestone_id=?", (config.project_id, config.milestone_id)
            ).fetchone()
            expected = (config.max_calls, config.max_micro_usd, config.project_max_calls, config.project_max_micro_usd)
            actual = (
                tuple(existing[k] for k in ("max_calls", "max_micro_usd", "project_max_calls", "project_max_micro_usd"))
                if existing
                else None
            )
            if existing and actual != expected:
                db.rollback()
                raise ValueError("budget configuration is immutable")
            db.execute(
                "INSERT OR IGNORE INTO budget_config VALUES (?,?,?,?,?,?)",
                (
                    config.project_id,
                    config.milestone_id,
                    config.max_calls,
                    config.max_micro_usd,
                    config.project_max_calls,
                    config.project_max_micro_usd,
                ),
            )
            db.commit()

    def reserve(self, config: BudgetConfig, *, call_id: str | None = None, estimated_micro_usd: int = 0) -> str:
        self._validate(config)
        if not isinstance(estimated_micro_usd, int) or estimated_micro_usd < 0:
            raise ValueError("estimated cost must be a non-negative integer")
        if (config.max_micro_usd is not None or config.project_max_micro_usd is not None) and estimated_micro_usd == 0:
            raise BudgetExceeded("known positive estimate required when a monetary cap is configured")
        call_id = call_id or str(uuid.uuid4())
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._configure_tx(db, config)
            prior = db.execute("SELECT * FROM budget_reservation WHERE call_id=?", (call_id,)).fetchone()
            if prior:
                if (prior["project_id"], prior["milestone_id"], prior["estimated_micro_usd"]) != (
                    config.project_id,
                    config.milestone_id,
                    estimated_micro_usd,
                ):
                    db.rollback()
                    raise ValueError("reservation ID already belongs to a different request")
                db.commit()
                return call_id
            row = db.execute(
                "SELECT * FROM budget_config WHERE project_id=? AND milestone_id=?", (config.project_id, config.milestone_id)
            ).fetchone()
            used_calls = db.execute(
                "SELECT count(*) FROM budget_reservation WHERE project_id=? AND milestone_id=?",
                (config.project_id, config.milestone_id),
            ).fetchone()[0]
            project_calls = db.execute(
                "SELECT count(*) FROM budget_reservation WHERE project_id=?", (config.project_id,)
            ).fetchone()[0]
            used_cost = db.execute(
                "SELECT coalesce(sum(coalesce(actual_micro_usd, estimated_micro_usd)),0) "
                "FROM budget_reservation WHERE project_id=? AND milestone_id=?",
                (config.project_id, config.milestone_id),
            ).fetchone()[0]
            project_cost = db.execute(
                "SELECT coalesce(sum(coalesce(actual_micro_usd, estimated_micro_usd)),0) FROM budget_reservation WHERE project_id=?",
                (config.project_id,),
            ).fetchone()[0]
            if (
                used_calls >= row["max_calls"]
                or (row["project_max_calls"] is not None and project_calls >= row["project_max_calls"])
                or (row["max_micro_usd"] is not None and used_cost + estimated_micro_usd > row["max_micro_usd"])
                or (
                    row["project_max_micro_usd"] is not None and project_cost + estimated_micro_usd > row["project_max_micro_usd"]
                )
            ):
                db.rollback()
                raise BudgetExceeded("budget exhausted")
            db.execute(
                "INSERT INTO budget_reservation(call_id,project_id,milestone_id,estimated_micro_usd,status) VALUES(?,?,?,?,?)",
                (call_id, config.project_id, config.milestone_id, estimated_micro_usd, "pending"),
            )
            db.commit()
        return call_id

    def claim_execution(self, call_id: str) -> None:
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM budget_reservation WHERE call_id=?", (call_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(call_id)
            if row["status"] != "pending":
                db.rollback()
                raise ReservationAlreadyClaimed(call_id)
            db.execute("UPDATE budget_reservation SET status='executing' WHERE call_id=?", (call_id,))
            db.commit()

    @staticmethod
    def _configure_tx(db: sqlite3.Connection, config: BudgetConfig) -> None:
        existing_project = db.execute(
            "SELECT project_max_calls, project_max_micro_usd FROM budget_config WHERE project_id=? LIMIT 1",
            (config.project_id,),
        ).fetchone()
        if existing_project and tuple(existing_project) != (config.project_max_calls, config.project_max_micro_usd):
            raise ValueError("project budget configuration is immutable")
        row = db.execute(
            "SELECT * FROM budget_config WHERE project_id=? AND milestone_id=?", (config.project_id, config.milestone_id)
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO budget_config VALUES (?,?,?,?,?,?)",
                (
                    config.project_id,
                    config.milestone_id,
                    config.max_calls,
                    config.max_micro_usd,
                    config.project_max_calls,
                    config.project_max_micro_usd,
                ),
            )
        elif tuple(row[k] for k in ("max_calls", "max_micro_usd", "project_max_calls", "project_max_micro_usd")) != (
            config.max_calls,
            config.max_micro_usd,
            config.project_max_calls,
            config.project_max_micro_usd,
        ):
            raise ValueError("budget configuration is immutable")

    def settle(self, call_id: str, actual_micro_usd: int | None = None) -> None:
        if actual_micro_usd is not None and actual_micro_usd < 0:
            raise ValueError("actual cost cannot be negative")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM budget_reservation WHERE call_id=?", (call_id,)).fetchone()
            if row is None:
                raise KeyError(call_id)
            if row["status"] == "settled":
                if row["actual_micro_usd"] != actual_micro_usd:
                    raise ValueError("settled reservation cannot be rewritten")
                return
            config = db.execute(
                "SELECT * FROM budget_config WHERE project_id=? AND milestone_id=?", (row["project_id"], row["milestone_id"])
            ).fetchone()
            cost = actual_micro_usd if actual_micro_usd is not None else row["estimated_micro_usd"]
            milestone_total = (
                db.execute(
                    "SELECT coalesce(sum(coalesce(actual_micro_usd, estimated_micro_usd)),0) FROM budget_reservation WHERE project_id=? AND milestone_id=?",
                    (row["project_id"], row["milestone_id"]),
                ).fetchone()[0]
                - row["estimated_micro_usd"]
                + cost
            )
            project_total = (
                db.execute(
                    "SELECT coalesce(sum(coalesce(actual_micro_usd, estimated_micro_usd)),0) FROM budget_reservation WHERE project_id=?",
                    (row["project_id"],),
                ).fetchone()[0]
                - row["estimated_micro_usd"]
                + cost
            )
            if (config["max_micro_usd"] is not None and milestone_total > config["max_micro_usd"]) or (
                config["project_max_micro_usd"] is not None and project_total > config["project_max_micro_usd"]
            ):
                db.execute(
                    "UPDATE budget_reservation SET actual_micro_usd=?,status='settled' WHERE call_id=?",
                    (actual_micro_usd, call_id),
                )
                db.commit()
                raise BudgetExceeded("budget exhausted; measured usage persisted")
            db.execute(
                "UPDATE budget_reservation SET actual_micro_usd=?,status='settled' WHERE call_id=?", (actual_micro_usd, call_id)
            )
            db.commit()

    def reservations(self, project_id: str, milestone_id: str) -> list[dict[str, object]]:
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM budget_reservation WHERE project_id=? AND milestone_id=? ORDER BY created_at,call_id",
                    (project_id, milestone_id),
                )
            ]
