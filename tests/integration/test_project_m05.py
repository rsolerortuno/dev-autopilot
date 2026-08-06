"""M00-M05 continuous project acceptance and blocker integration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dev_autopilot.adapters.fake import FakeCommandAdapter, ScriptedAgentAdapter
from dev_autopilot.bundle import verify_bundle
from dev_autopilot.db import SQLiteStore
from dev_autopilot.models import AuditReport, ExecutionResult, ResultStatus, ReviewDecision, ReviewReport
from dev_autopilot.orchestrator import Orchestrator
from dev_autopilot.project import ContinuousProjectRunner, ProjectCharter, ProjectStatus


def _success(summary: str, output=None) -> ExecutionResult:
    return ExecutionResult(status=ResultStatus.SUCCESS, summary=summary, output=output or {})


def _charter(job, *, two: bool = True) -> ProjectCharter:
    first = job.model_copy(update={"evidence": job.evidence.model_copy(update={"milestone_id": "M04"})})
    milestones = [
        {
            "milestone_id": "M04",
            "title": "Drive storage",
            "objective": "Validate bounded storage primitives",
            "dependencies": [],
            "job": first.to_dict(),
        }
    ]
    if two:
        second = job.model_copy(update={"evidence": job.evidence.model_copy(update={"milestone_id": "M05"})})
        milestones.append(
            {
                "milestone_id": "M05",
                "title": "Colab worker",
                "objective": "Validate fenced resumable worker execution",
                "dependencies": ["M04"],
                "job": second.to_dict(),
            }
        )
    return ProjectCharter.model_validate(
        {
            "project_id": "m05-release",
            "title": "Final M05",
            "mission": "Complete every milestone without conversational questions",
            "definition_of_done": ["all milestones accepted", "all bundles verify"],
            "milestones": milestones,
        }
    )


def _factory(store: SQLiteStore, decision: ReviewDecision = ReviewDecision.APPROVE):
    def create(job):
        return Orchestrator(
            store,
            command_adapter=FakeCommandAdapter(changed=()),
            codex=ScriptedAgentAdapter("codex", [_success("implemented")]),
            agy=ScriptedAgentAdapter("agy", [_success("audited", AuditReport(passed=True, summary="clean").to_dict())]),
            claude_reviewer=ScriptedAgentAdapter(
                "claude-reviewer",
                [
                    _success(
                        "reviewed",
                        ReviewReport(decision=decision, summary="review complete").to_dict(),
                    )
                ],
            ),
        )

    return create


def test_project_charter_rejects_questions_and_cycles(job):
    raw = _charter(job).to_dict()
    raw["autonomy"]["ask_questions"] = True
    with pytest.raises(ValidationError):
        ProjectCharter.model_validate(raw)

    raw = _charter(job).to_dict()
    raw["milestones"][0]["dependencies"] = ["M05"]
    with pytest.raises(ValidationError, match="cycle"):
        ProjectCharter.model_validate(raw)


def test_continuous_project_runs_dependency_order_and_verifies_bundles(tmp_path, job):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    runner = ContinuousProjectRunner(store, _factory(store))
    project_run_id = runner.start(_charter(job))
    record = runner.projects.get(project_run_id)
    assert record["status"] == ProjectStatus.READY_FOR_HUMAN_RELEASE.value
    assert [item["status"] for item in record["milestones"]] == ["ACCEPTED", "ACCEPTED"]
    for item in record["milestones"]:
        bundle = tmp_path / "bundles" / item["autopilot_run_id"]
        assert bundle.is_dir()
        assert verify_bundle(bundle) == []


def test_human_decision_creates_blocker_instead_of_asking(tmp_path, job, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = SQLiteStore(tmp_path / "state.sqlite3")
    runner = ContinuousProjectRunner(store, _factory(store, ReviewDecision.HUMAN_DECISION))
    project_run_id = runner.start(_charter(job, two=False))
    record = runner.projects.get(project_run_id)
    assert record["status"] == ProjectStatus.BLOCKED.value
    assert record["blocker"]["state"] == "PAUSED_HUMAN_DECISION"
    blocked = tmp_path / ".dev-autopilot" / "projects" / "m05-release" / str(project_run_id) / "BLOCKED"
    assert (blocked / "ACTION_REQUIRED.md").is_file()
    assert (blocked / "blocker.json").is_file()
