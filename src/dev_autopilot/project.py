"""M00/M01 immutable project charters and continuous no-questions execution."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

import yaml
from pydantic import Field, StringConstraints, field_validator, model_validator

from dev_autopilot.db import SQLiteStore
from dev_autopilot.models import ContractModel, JobSpecification, NonEmptyString
from dev_autopilot.orchestrator import Orchestrator
from dev_autopilot.states import WorkflowState

ProjectId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
MilestoneId = Annotated[str, StringConstraints(pattern=r"^M\d{2,}$")]


class ProjectStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    READY_FOR_HUMAN_RELEASE = "READY_FOR_HUMAN_RELEASE"
    FAILED = "FAILED"


class MilestoneStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class AutonomyPolicy(ContractModel):
    mode: Literal["continuous"] = "continuous"
    ask_questions: Literal[False] = False
    ambiguity_policy: Literal["safest_reversible_assumption"] = "safest_reversible_assumption"
    destructive_actions: Literal["deny"] = "deny"
    overwrite_policy: Literal["version_outputs"] = "version_outputs"
    human_gate: Literal["release_only"] = "release_only"


class ProjectMilestone(ContractModel):
    milestone_id: MilestoneId
    title: NonEmptyString
    objective: NonEmptyString
    dependencies: tuple[MilestoneId, ...] = ()
    job: JobSpecification

    @field_validator("dependencies", mode="before")
    @classmethod
    def freeze_dependencies(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def align_job_evidence(self) -> ProjectMilestone:
        if self.job.evidence.milestone_id != self.milestone_id:
            raise ValueError(f"job.evidence.milestone_id {self.job.evidence.milestone_id} must equal {self.milestone_id}")
        return self


class ProjectCharter(ContractModel):
    project_id: ProjectId
    title: NonEmptyString
    mission: NonEmptyString
    definition_of_done: tuple[NonEmptyString, ...] = Field(min_length=1)
    non_goals: tuple[str, ...] = ()
    autonomy: AutonomyPolicy = AutonomyPolicy()
    milestones: tuple[ProjectMilestone, ...] = Field(min_length=1)

    @field_validator("definition_of_done", "non_goals", "milestones", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_graph(self) -> ProjectCharter:
        ids = [milestone.milestone_id for milestone in self.milestones]
        if len(ids) != len(set(ids)):
            raise ValueError("milestone ids must be unique")
        known = set(ids)
        for milestone in self.milestones:
            missing = set(milestone.dependencies) - known
            if missing:
                raise ValueError(f"{milestone.milestone_id} has unknown dependencies: {sorted(missing)}")
            if milestone.milestone_id in milestone.dependencies:
                raise ValueError(f"{milestone.milestone_id} cannot depend on itself")
        visiting: set[str] = set()
        visited: set[str] = set()
        graph = {m.milestone_id: m.dependencies for m in self.milestones}

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError(f"milestone dependency cycle includes {node}")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in ids:
            visit(node)
        return self

    def ordered_milestones(self) -> tuple[ProjectMilestone, ...]:
        result: list[ProjectMilestone] = []
        remaining = {m.milestone_id: m for m in self.milestones}
        accepted: set[str] = set()
        while remaining:
            ready = sorted(
                (m for m in remaining.values() if set(m.dependencies) <= accepted),
                key=lambda m: m.milestone_id,
            )
            if not ready:
                raise RuntimeError("milestone graph has no executable node")
            for milestone in ready:
                result.append(milestone)
                accepted.add(milestone.milestone_id)
                remaining.pop(milestone.milestone_id)
        return tuple(result)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_runs (
    project_run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    charter_json TEXT NOT NULL,
    status TEXT NOT NULL,
    blocker_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_milestones (
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id),
    milestone_id TEXT NOT NULL,
    status TEXT NOT NULL,
    autopilot_run_id TEXT,
    reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_run_id, milestone_id)
);
CREATE TABLE IF NOT EXISTS project_assumptions (
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id),
    sequence INTEGER NOT NULL,
    milestone_id TEXT,
    assumption TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_run_id, sequence)
);
"""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class ProjectStore:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        with store.connect() as connection:
            connection.executescript(_SCHEMA)

    def create(self, charter: ProjectCharter, *, now: datetime | None = None) -> UUID:
        project_run_id = uuid4()
        timestamp = now or datetime.now(UTC)
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO project_runs VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (
                    str(project_run_id),
                    charter.project_id,
                    charter.to_json(),
                    ProjectStatus.CREATED.value,
                    _iso(timestamp),
                    _iso(timestamp),
                ),
            )
            for milestone in charter.milestones:
                connection.execute(
                    "INSERT INTO project_milestones VALUES (?, ?, ?, NULL, NULL, ?)",
                    (
                        str(project_run_id),
                        milestone.milestone_id,
                        MilestoneStatus.PENDING.value,
                        _iso(timestamp),
                    ),
                )
        return project_run_id

    def get(self, project_run_id: UUID | str) -> dict[str, object]:
        with self.store.connect() as connection:
            project = connection.execute("SELECT * FROM project_runs WHERE project_run_id=?", (str(project_run_id),)).fetchone()
            milestones = connection.execute(
                "SELECT * FROM project_milestones WHERE project_run_id=? ORDER BY milestone_id",
                (str(project_run_id),),
            ).fetchall()
            assumptions = connection.execute(
                "SELECT * FROM project_assumptions WHERE project_run_id=? ORDER BY sequence",
                (str(project_run_id),),
            ).fetchall()
        if project is None:
            raise KeyError(f"project run not found: {project_run_id}")
        return {
            **dict(project),
            "charter": json.loads(project["charter_json"]),
            "blocker": None if project["blocker_json"] is None else json.loads(project["blocker_json"]),
            "milestones": [dict(row) for row in milestones],
            "assumptions": [dict(row) for row in assumptions],
        }

    def set_project_status(
        self,
        project_run_id: UUID | str,
        status: ProjectStatus,
        *,
        blocker: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or datetime.now(UTC)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE project_runs SET status=?, blocker_json=?, updated_at=? WHERE project_run_id=?",
                (
                    status.value,
                    None if blocker is None else json.dumps(blocker, sort_keys=True),
                    _iso(timestamp),
                    str(project_run_id),
                ),
            )

    def set_milestone(
        self,
        project_run_id: UUID | str,
        milestone_id: str,
        status: MilestoneStatus,
        *,
        autopilot_run_id: UUID | str | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                """UPDATE project_milestones SET status=?,autopilot_run_id=COALESCE(?,autopilot_run_id),
                   reason=?,updated_at=? WHERE project_run_id=? AND milestone_id=?""",
                (
                    status.value,
                    None if autopilot_run_id is None else str(autopilot_run_id),
                    reason,
                    _iso(now or datetime.now(UTC)),
                    str(project_run_id),
                    milestone_id,
                ),
            )

    def add_assumption(
        self,
        project_run_id: UUID | str,
        *,
        milestone_id: str | None,
        assumption: str,
        rationale: str,
        now: datetime | None = None,
    ) -> None:
        with self.store.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),-1)+1 FROM project_assumptions WHERE project_run_id=?",
                (str(project_run_id),),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO project_assumptions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(project_run_id),
                    sequence,
                    milestone_id,
                    assumption,
                    rationale,
                    _iso(now or datetime.now(UTC)),
                ),
            )


OrchestratorFactory = Callable[[JobSpecification], Orchestrator]


class ContinuousProjectRunner:
    """Execute all milestones without conversational questions."""

    def __init__(self, store: SQLiteStore, orchestrator_factory: OrchestratorFactory) -> None:
        self.store = store
        self.projects = ProjectStore(store)
        self.orchestrator_factory = orchestrator_factory

    def start(self, charter: ProjectCharter) -> UUID:
        project_run_id = self.projects.create(charter)
        self.run(project_run_id)
        return project_run_id

    def run(self, project_run_id: UUID | str) -> dict[str, object]:
        record = self.projects.get(project_run_id)
        charter = ProjectCharter.from_dict(cast(dict[str, Any], record["charter"]))
        self.projects.set_project_status(project_run_id, ProjectStatus.RUNNING)
        milestone_rows = cast(list[dict[str, Any]], record["milestones"])
        status_by_id: dict[str, dict[str, Any]] = {str(row["milestone_id"]): row for row in milestone_rows}
        for milestone in charter.ordered_milestones():
            row = status_by_id[milestone.milestone_id]
            if row["status"] == MilestoneStatus.ACCEPTED.value:
                continue
            dependencies = [status_by_id[dependency]["status"] for dependency in milestone.dependencies]
            if any(value != MilestoneStatus.ACCEPTED.value for value in dependencies):
                blocker: dict[str, object] = {
                    "milestone_id": milestone.milestone_id,
                    "reason": "one or more dependencies are not accepted",
                    "single_required_action": "Resolve the failed dependency milestone",
                }
                self.projects.set_milestone(
                    project_run_id, milestone.milestone_id, MilestoneStatus.BLOCKED, reason=str(blocker["reason"])
                )
                self.projects.set_project_status(project_run_id, ProjectStatus.BLOCKED, blocker=blocker)
                return self.projects.get(project_run_id)
            run_id = row["autopilot_run_id"]
            runtime_job = milestone.job
            if runtime_job.budget.project_id is None:
                runtime_job = runtime_job.model_copy(
                    update={
                        "budget": runtime_job.budget.model_copy(update={"project_id": f"{charter.project_id}:{project_run_id}"})
                    }
                )
            orchestrator = self.orchestrator_factory(runtime_job)
            if run_id is None:
                run_id = orchestrator.create_run(runtime_job).run_id
                self.projects.set_milestone(
                    project_run_id, milestone.milestone_id, MilestoneStatus.RUNNING, autopilot_run_id=run_id
                )
            # Retryable quota pauses are operational states, not human blockers.
            # Keep the continuous project alive and resume the same persisted run
            # automatically when its retry becomes due.
            quota_wait_announced = False

            while True:
                run = self.store.get_run(run_id)

                if run.state is WorkflowState.PAUSED_QUOTA:
                    if not quota_wait_announced:
                        orchestrator.progress(
                            f"{milestone.milestone_id} [{str(run_id)[:8]}] | PAUSED_QUOTA | waiting for scheduled retry..."
                        )
                        quota_wait_announced = True

                    resumed = orchestrator.resume_if_due(run_id)

                    if resumed.state is WorkflowState.PAUSED_QUOTA:
                        time.sleep(1.0)
                        continue

                    orchestrator.progress(
                        f"{milestone.milestone_id} [{str(run_id)[:8]}] | retry due | resuming {resumed.state.value}"
                    )
                    run = resumed
                    quota_wait_announced = False

                run = orchestrator.run_until_blocked(run_id)

                if run.state is WorkflowState.PAUSED_QUOTA:
                    continue

                break

            if run.state is WorkflowState.READY_FOR_HUMAN_REVIEW:
                self.projects.set_milestone(
                    project_run_id,
                    milestone.milestone_id,
                    MilestoneStatus.ACCEPTED,
                    autopilot_run_id=run_id,
                    reason="verified milestone bundle ready",
                )
                status_by_id[milestone.milestone_id]["status"] = MilestoneStatus.ACCEPTED.value
                continue
            blocker = {
                "milestone_id": milestone.milestone_id,
                "autopilot_run_id": str(run_id),
                "state": run.state.value,
                "reason": run.stop_reason or f"milestone stopped in {run.state.value}",
                "single_required_action": "Inspect the recorded evidence and satisfy the persisted resume condition",
            }
            milestone_status = MilestoneStatus.FAILED if run.state is WorkflowState.FAILED else MilestoneStatus.BLOCKED
            self.projects.set_milestone(
                project_run_id,
                milestone.milestone_id,
                milestone_status,
                autopilot_run_id=run_id,
                reason=str(blocker["reason"]),
            )
            self.projects.set_project_status(project_run_id, ProjectStatus.BLOCKED, blocker=blocker)
            self._write_action_required(charter, project_run_id, blocker)
            return self.projects.get(project_run_id)
        self.projects.set_project_status(project_run_id, ProjectStatus.READY_FOR_HUMAN_RELEASE)
        return self.projects.get(project_run_id)

    @staticmethod
    def _write_action_required(charter: ProjectCharter, project_run_id: UUID | str, blocker: dict[str, object]) -> None:
        root = Path(".dev-autopilot") / "projects" / charter.project_id / str(project_run_id) / "BLOCKED"
        root.mkdir(parents=True, exist_ok=True)
        (root / "blocker.json").write_text(json.dumps(blocker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (root / "ACTION_REQUIRED.md").write_text(
            f"# Action required\n\n{blocker['single_required_action']}\n\nReason: {blocker['reason']}\n",
            encoding="utf-8",
        )


def load_project_charter(path: Path | str) -> ProjectCharter:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load project charter {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("project charter must be a mapping")
    # Resolve relative repositories against the charter directory.
    for milestone in raw.get("milestones", []):
        job = milestone.get("job", {})
        repository = job.get("repository")
        if isinstance(repository, str) and not Path(repository).is_absolute():
            job["repository"] = str((source.parent / repository).resolve())
    return ProjectCharter.from_dict(raw)
