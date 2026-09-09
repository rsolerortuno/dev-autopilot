"""One-use, expiring approval grants bound to an action and exact diff."""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from math import isfinite
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class ApprovalError(ValueError):
    """Raised when an approval grant is missing, stale, or mismatched."""


class ApprovalGrantStore:
    def __init__(self, database: Path | str) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS approval_grants ("
                "grant_id TEXT PRIMARY KEY, action TEXT NOT NULL, diff_sha256 TEXT NOT NULL, "
                "actor TEXT NOT NULL, expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0)"
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def issue(self, *, action: str, diff_sha256: str, actor: str, ttl_seconds: int, now: float | None = None) -> str:
        if (
            not action
            or not actor
            or not _SHA256.fullmatch(diff_sha256)
            or isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not isfinite(ttl_seconds)
            or ttl_seconds <= 0
            or (now is not None and not isfinite(now))
        ):
            raise ValueError("action, diff_sha256, actor and positive ttl_seconds are required")
        grant_id = secrets.token_urlsafe(24)
        expires_at = (time.time() if now is None else now) + ttl_seconds
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO approval_grants(grant_id,action,diff_sha256,actor,expires_at) VALUES(?,?,?,?,?)",
                (grant_id, action, diff_sha256, actor, expires_at),
            )
        return grant_id

    def consume(self, grant_id: str, *, action: str, diff_sha256: str, actor: str, now: float | None = None) -> None:
        current = time.time() if now is None else now
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM approval_grants WHERE grant_id = ?", (grant_id,)).fetchone()
            if row is None or row["used"] or row["expires_at"] <= current:
                connection.rollback()
                raise ApprovalError("approval grant is missing, expired, or already used")
            if row["action"] != action or row["diff_sha256"] != diff_sha256 or row["actor"] != actor:
                connection.rollback()
                raise ApprovalError("approval grant does not match action, diff, or actor")
            updated = connection.execute(
                "UPDATE approval_grants SET used = 1 WHERE grant_id = ? AND used = 0",
                (grant_id,),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise ApprovalError("approval grant is missing, expired, or already used")
            connection.commit()
