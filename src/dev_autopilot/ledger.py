"""SQLite-backed persistence for the M02 findings ledger.

This reuses the existing :class:`~dev_autopilot.db.SQLiteStore` connection and
transaction semantics, adding two tables (``findings`` and
``milestone_scores``) plus a diff-invalidation operation.  The tables are
created idempotently, so an existing autopilot database gains the ledger on
first use without a destructive migration.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dev_autopilot.db import SQLiteStore
from dev_autopilot.errors import PersistenceError
from dev_autopilot.findings import (
    Finding,
    FindingStatus,
    MilestoneScore,
    ReviewerRole,
    next_finding_id,
)

_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    finding_id TEXT NOT NULL,
    milestone_id TEXT NOT NULL,
    role TEXT NOT NULL,
    severity TEXT NOT NULL,
    finding_json TEXT NOT NULL,
    status TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, finding_id)
);
CREATE INDEX IF NOT EXISTS findings_by_milestone
    ON findings(run_id, milestone_id, status);
CREATE TABLE IF NOT EXISTS milestone_scores (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    milestone_id TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL,
    score_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, milestone_id, diff_sha256)
);
"""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class FindingLedger:
    """Durable, diff-aware ledger operations layered on the shared store."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        with self.store.connect() as connection:
            connection.executescript(_LEDGER_SCHEMA)

    def _points_for(self, connection: Any, run_id: str, milestone_id: str, role: ReviewerRole) -> int:
        row = connection.execute(
            "SELECT COUNT(*) FROM findings WHERE run_id=? AND milestone_id=? AND role=?",
            (run_id, milestone_id, role.value),
        ).fetchone()
        return int(row[0])

    def raise_finding(
        self,
        run_id: UUID | str,
        *,
        milestone_id: str,
        role: ReviewerRole,
        severity: Any,
        category: Any,
        path: str,
        problem: str,
        required_resolution: str,
        diff_sha256: str,
        line: int | None = None,
        now: datetime | None = None,
    ) -> Finding:
        """Record a new OPEN finding with a deterministic id and diff binding."""
        timestamp = now or datetime.now(UTC)
        run = str(run_id)
        with self.store.transaction() as connection:
            existing = self._points_for(connection, run, milestone_id, role)
            finding = Finding(
                finding_id=next_finding_id(milestone_id, role, existing),
                milestone_id=milestone_id,
                role=role,
                severity=severity,
                category=category,
                blocking=bool(getattr(severity, "blocks_acceptance", False))
                if hasattr(severity, "blocks_acceptance")
                else severity in ("P0", "P1"),
                path=path,
                line=line,
                problem=problem,
                required_resolution=required_resolution,
                diff_sha256=diff_sha256,
            )
            connection.execute(
                """INSERT INTO findings(
                    run_id, finding_id, milestone_id, role, severity,
                    finding_json, status, diff_sha256, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run,
                    finding.finding_id,
                    finding.milestone_id,
                    finding.role.value,
                    finding.severity.value,
                    finding.to_json(),
                    finding.status.value,
                    finding.diff_sha256,
                    _iso(timestamp),
                    _iso(timestamp),
                ),
            )
        return finding

    def get_finding(self, run_id: UUID | str, finding_id: str) -> Finding:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT finding_json FROM findings WHERE run_id=? AND finding_id=?",
                (str(run_id), finding_id),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"finding not found: {finding_id}")
        return Finding.from_json(row["finding_json"])

    def resolve_finding(
        self,
        run_id: UUID | str,
        finding_id: str,
        *,
        resolution: str,
        code_evidence: tuple[str, ...] = (),
        test_evidence: tuple[str, ...] = (),
        verified_by: tuple[ReviewerRole, ...],
        diff_sha256: str,
        now: datetime | None = None,
    ) -> Finding:
        """Resolve a finding against a specific diff.

        The caller must pass the diff the resolution was verified against.  If
        the finding was raised against a different diff, resolving re-binds it to
        the new diff, so the ledger always reflects the code that the
        verification actually saw.
        """
        timestamp = now or datetime.now(UTC)
        current = self.get_finding(run_id, finding_id)
        # ``model_copy(update=...)`` does not validate updates in Pydantic. Build
        # a fresh model so an empty resolution, empty verifier list, malformed
        # digest, or any other invalid state can never be persisted.
        payload = current.to_dict()
        payload.update(
            {
                "status": FindingStatus.RESOLVED.value,
                "resolution": resolution,
                "code_evidence": list(code_evidence),
                "test_evidence": list(test_evidence),
                "verified_by": [role.value for role in verified_by],
                "diff_sha256": diff_sha256,
            }
        )
        resolved = Finding.from_dict(payload)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE findings SET finding_json=?, status=?, diff_sha256=?, updated_at=? WHERE run_id=? AND finding_id=?",
                (
                    resolved.to_json(),
                    resolved.status.value,
                    resolved.diff_sha256,
                    _iso(timestamp),
                    str(run_id),
                    finding_id,
                ),
            )
        return resolved

    def invalidate_stale(
        self,
        run_id: UUID | str,
        *,
        milestone_id: str,
        current_diff_sha256: str,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Invalidate every finding bound to a diff other than the current one.

        Returns the ids that were invalidated.  This is the automatic
        consequence of the code changing: a later diff cannot inherit the
        verification of an earlier one.  OPEN findings on a stale diff are also
        invalidated -- their line references may no longer point at anything --
        and must be re-raised against the new diff if still relevant.
        """
        timestamp = now or datetime.now(UTC)
        invalidated: list[str] = []
        with self.store.transaction() as connection:
            rows = connection.execute(
                """SELECT finding_id, finding_json FROM findings
                   WHERE run_id=? AND milestone_id=? AND status!=? AND diff_sha256!=?""",
                (str(run_id), milestone_id, FindingStatus.INVALIDATED.value, current_diff_sha256),
            ).fetchall()
            for row in rows:
                finding = Finding.from_json(row["finding_json"])
                payload = finding.to_dict()
                payload["status"] = FindingStatus.INVALIDATED.value
                # Resolution evidence remains in the immutable history, but the
                # status makes it unusable for acceptance until a final-diff
                # review explicitly resolves or supersedes it.
                updated = Finding.from_dict(payload)
                connection.execute(
                    "UPDATE findings SET finding_json=?, status=?, updated_at=? WHERE run_id=? AND finding_id=?",
                    (
                        updated.to_json(),
                        updated.status.value,
                        _iso(timestamp),
                        str(run_id),
                        finding.finding_id,
                    ),
                )
                invalidated.append(finding.finding_id)
        return tuple(invalidated)

    def list_findings(
        self,
        run_id: UUID | str,
        *,
        milestone_id: str | None = None,
        status: FindingStatus | None = None,
    ) -> tuple[Finding, ...]:
        clauses = ["run_id=?"]
        params: list[Any] = [str(run_id)]
        if milestone_id is not None:
            clauses.append("milestone_id=?")
            params.append(milestone_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        query = "SELECT finding_json FROM findings WHERE " + " AND ".join(clauses) + " ORDER BY finding_id"
        with self.store.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(Finding.from_json(row["finding_json"]) for row in rows)

    def open_blockers(self, run_id: UUID | str, milestone_id: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.list_findings(run_id, milestone_id=milestone_id) if f.is_open_blocker)

    def record_score(self, run_id: UUID | str, score: MilestoneScore, *, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO milestone_scores(
                    run_id, milestone_id, diff_sha256, score_json, created_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (str(run_id), score.milestone_id, score.diff_sha256, score.to_json(), _iso(timestamp)),
            )

    def get_score(self, run_id: UUID | str, milestone_id: str, diff_sha256: str) -> MilestoneScore | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT score_json FROM milestone_scores WHERE run_id=? AND milestone_id=? AND diff_sha256=?",
                (str(run_id), milestone_id, diff_sha256),
            ).fetchone()
        return None if row is None else MilestoneScore.from_json(row["score_json"])

    def export_findings(self, run_id: UUID | str) -> list[dict[str, Any]]:
        return [json.loads(f.to_json()) for f in self.list_findings(run_id)]
