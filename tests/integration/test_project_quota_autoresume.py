from datetime import UTC, datetime

import dev_autopilot.project as project_module
from dev_autopilot.adapters.fake import (
    FakeCommandAdapter,
    ScriptedAgentAdapter,
)
from dev_autopilot.db import SQLiteStore
from dev_autopilot.models import (
    AuditReport,
    ExecutionResult,
    ResultStatus,
    ReviewDecision,
    ReviewReport,
)
from dev_autopilot.orchestrator import Orchestrator
from dev_autopilot.project import (
    ContinuousProjectRunner,
    ProjectCharter,
    ProjectStatus,
)
from dev_autopilot.retries import FakeClock


def _success(summary: str, output=None) -> ExecutionResult:
    return ExecutionResult(
        status=ResultStatus.SUCCESS,
        summary=summary,
        output=output or {},
    )


def test_codex_false_quota_auto_resumes_without_human_action(
    tmp_path,
    job,
    monkeypatch,
):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    clock = FakeClock(datetime(2026, 8, 10, tzinfo=UTC))

    configured_job = job.model_copy(update={"evidence": job.evidence.model_copy(update={"milestone_id": "M00"})})

    codex = ScriptedAgentAdapter(
        "codex",
        [
            ExecutionResult(
                status=ResultStatus.QUOTA,
                summary="codex exited with 3",
            ),
            _success("implemented after transient quota"),
        ],
    )

    agy = ScriptedAgentAdapter(
        "agy",
        [
            _success(
                "audit passed",
                AuditReport(
                    passed=True,
                    summary="clean",
                ).to_dict(),
            )
        ],
    )

    reviewer = ScriptedAgentAdapter(
        "claude-reviewer",
        [
            _success(
                "review passed",
                ReviewReport(
                    decision=ReviewDecision.APPROVE,
                    summary="approved",
                ).to_dict(),
            )
        ],
    )

    def factory(current_job):
        return Orchestrator(
            store,
            command_adapter=FakeCommandAdapter(changed=()),
            codex=codex,
            agy=agy,
            claude_reviewer=reviewer,
            clock=clock,
        )

    # Advance deterministic time instead of actually sleeping.
    monkeypatch.setattr(
        project_module.time,
        "sleep",
        lambda seconds: clock.advance(seconds=seconds),
    )

    charter = ProjectCharter.model_validate(
        {
            "project_id": "quota-autoresume-test",
            "title": "Quota autoresume",
            "mission": "Retry transient Codex quota automatically",
            "definition_of_done": ["M00 accepted without human resume"],
            "milestones": [
                {
                    "milestone_id": "M00",
                    "title": "Quota retry",
                    "objective": "Complete after transient Codex quota",
                    "dependencies": [],
                    "job": configured_job.to_dict(),
                }
            ],
        }
    )

    project_run_id = ContinuousProjectRunner(
        store,
        factory,
    ).start(charter)

    record = ContinuousProjectRunner(
        store,
        factory,
    ).projects.get(project_run_id)

    assert record["status"] == ProjectStatus.READY_FOR_HUMAN_RELEASE.value
    assert record["milestones"][0]["status"] == "ACCEPTED"

    run_id = record["milestones"][0]["autopilot_run_id"]
    events = store.list_events(run_id)

    retries = [event for event in events if event["event_type"] == "RETRY_SCHEDULED"]

    assert len(retries) == 1
    assert retries[0]["payload"]["owner"] == "codex"
    assert retries[0]["payload"]["count"] == 1
