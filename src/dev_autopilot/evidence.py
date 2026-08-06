"""Durable M03 evidence records layered on the shared SQLite database."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from dev_autopilot.baseline import BaselineCapture
from dev_autopilot.db import SQLiteStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_evidence (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    kind TEXT NOT NULL,
    evidence_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL,
    path TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, kind, evidence_key)
);
"""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class RunEvidenceStore:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        with store.connect() as connection:
            connection.executescript(_SCHEMA)

    def put(
        self,
        run_id: UUID | str,
        *,
        kind: str,
        key: str,
        payload: dict[str, Any],
        path: Path | str | None = None,
        now: datetime | None = None,
    ) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO run_evidence(
                    run_id, kind, evidence_key, payload_json, digest, path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(run_id),
                    kind,
                    key,
                    canonical,
                    digest,
                    None if path is None else str(path),
                    _iso(now or datetime.now(UTC)),
                ),
            )
        return digest

    def get(self, run_id: UUID | str, *, kind: str, key: str) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM run_evidence WHERE run_id=? AND kind=? AND evidence_key=?",
                (str(run_id), kind, key),
            ).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def list(self, run_id: UUID | str, *, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT kind,evidence_key,payload_json,digest,path,created_at FROM run_evidence WHERE run_id=?"
        params: list[Any] = [str(run_id)]
        if kind is not None:
            query += " AND kind=?"
            params.append(kind)
        query += " ORDER BY kind,evidence_key"
        with self.store.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "kind": row["kind"],
                "key": row["evidence_key"],
                "payload": json.loads(row["payload_json"]),
                "digest": row["digest"],
                "path": row["path"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def put_baseline(self, run_id: UUID | str, baseline: BaselineCapture) -> str:
        return self.put(run_id, kind="baseline", key="initial", payload=baseline.to_dict())

    def get_baseline(self, run_id: UUID | str) -> BaselineCapture | None:
        payload = self.get(run_id, kind="baseline", key="initial")
        return None if payload is None else BaselineCapture.from_dict(payload)
