from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dev_autopilot.adapters.fake import FakeCommandAdapter, ScriptedAgentAdapter
from dev_autopilot.db import SQLiteStore
from dev_autopilot.errors import RunLockError
from dev_autopilot.models import (
    AgentCommand,
    AgentSettings,
    AuditReport,
    ExecutionResult,
    ResultStatus,
    ReviewDecision,
    ReviewReport,
)
from dev_autopilot.orchestrator import Orchestrator
from dev_autopilot.retries import FakeClock
from dev_autopilot.states import WorkflowState


def success(summary: str, output=None) -> ExecutionResult:
    return ExecutionResult(status=ResultStatus.SUCCESS, summary=summary, output=output or {})


def test_clean_run_reaches_human_review(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    commands = FakeCommandAdapter(changed=["src/a.py"], diff_content="one")
    orchestrator = Orchestrator(
        store,
        command_adapter=commands,
        codex=ScriptedAgentAdapter("codex", [success("implemented")]),
        agy=ScriptedAgentAdapter("agy", [success("audit", AuditReport(passed=True, summary="clean").to_dict())]),
        claude_reviewer=ScriptedAgentAdapter(
            "claude-reviewer",
            [
                success(
                    "review",
                    ReviewReport(decision=ReviewDecision.APPROVE, summary="approved").to_dict(),
                )
            ],
        ),
    )
    run = orchestrator.create_run(job)
    finished = orchestrator.run_until_blocked(run.run_id)
    assert finished.state is WorkflowState.READY_FOR_HUMAN_REVIEW
    assert commands.calls == [
        job.test_commands.baseline,
        job.test_commands.fast,
        job.test_commands.final,
    ]


def test_quota_pause_persists_and_resumes_after_restart(tmp_path, job) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FakeClock(now)
    path = tmp_path / "state.sqlite3"
    first_store = SQLiteStore(path)
    first = Orchestrator(
        first_store,
        command_adapter=FakeCommandAdapter(changed=["src/a.py"]),
        codex=ScriptedAgentAdapter(
            "codex",
            [ExecutionResult(status=ResultStatus.QUOTA, summary="quota exceeded")],
        ),
        agy=ScriptedAgentAdapter("agy", []),
        claude_reviewer=ScriptedAgentAdapter("claude-reviewer", []),
        clock=clock,
    )
    run = first.create_run(job)
    paused = first.run_until_blocked(run.run_id)
    assert paused.state is WorkflowState.PAUSED_QUOTA
    retry = first_store.get_retry(run.run_id, "codex")
    assert retry is not None and retry.count == 1

    clock.advance(seconds=11)
    second_store = SQLiteStore(path)
    second = Orchestrator(
        second_store,
        command_adapter=FakeCommandAdapter(changed=["src/a.py"]),
        codex=ScriptedAgentAdapter("codex", [success("implemented")]),
        agy=ScriptedAgentAdapter("agy", [success("audit", AuditReport(passed=True, summary="clean").to_dict())]),
        claude_reviewer=ScriptedAgentAdapter(
            "claude-reviewer",
            [
                success(
                    "review",
                    ReviewReport(decision=ReviewDecision.APPROVE, summary="approved").to_dict(),
                )
            ],
        ),
        clock=clock,
    )
    assert second.resume_if_due(run.run_id).state is WorkflowState.IMPLEMENTATION
    finished = second.run_until_blocked(run.run_id)
    assert finished.state is WorkflowState.READY_FOR_HUMAN_REVIEW


def test_review_correction_loop(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    codex = ScriptedAgentAdapter("codex", [success("implemented"), success("corrected")])
    reviewer = ScriptedAgentAdapter(
        "claude-reviewer",
        [
            success(
                "review",
                ReviewReport(
                    decision=ReviewDecision.REQUEST_CHANGES,
                    summary="fix issue",
                    findings=("bug",),
                ).to_dict(),
            ),
            success(
                "review",
                ReviewReport(decision=ReviewDecision.APPROVE, summary="approved").to_dict(),
            ),
        ],
    )
    orchestrator = Orchestrator(
        store,
        command_adapter=FakeCommandAdapter(changed=["src/a.py"], diff_content="same"),
        codex=codex,
        agy=ScriptedAgentAdapter("agy", [success("audit", AuditReport(passed=True, summary="clean").to_dict())]),
        claude_reviewer=reviewer,
    )
    run = orchestrator.create_run(job)
    finished = orchestrator.run_until_blocked(run.run_id)
    assert finished.state is WorkflowState.READY_FOR_HUMAN_REVIEW
    assert finished.correction_rounds == 1
    assert codex.calls == 2


def test_completed_phase_is_reused_after_restart(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    commands = FakeCommandAdapter(changed=[])
    orchestrator = Orchestrator(
        store,
        command_adapter=commands,
        codex=ScriptedAgentAdapter("codex", []),
        agy=ScriptedAgentAdapter("agy", []),
        claude_reviewer=ScriptedAgentAdapter("claude-reviewer", []),
    )
    run = orchestrator.create_run(job)
    orchestrator.step(run.run_id)  # CREATED -> PLAN
    orchestrator.step(run.run_id)  # PLAN -> BASELINE
    orchestrator.step(run.run_id)  # execute baseline
    assert commands.calls == [job.test_commands.baseline]
    # Simulate a restart positioned at the same phase with identical input.
    with store.transaction() as connection:
        connection.execute(
            "UPDATE runs SET state=? WHERE run_id=?",
            (WorkflowState.BASELINE_VALIDATION.value, str(run.run_id)),
        )
    orchestrator.step(run.run_id)
    assert commands.calls == [job.test_commands.baseline]


def test_long_fake_agent_phase_renews_lease_and_lost_lease_fails_closed(tmp_path, job) -> None:
    class AdvancingAgent(ScriptedAgentAdapter):
        def __init__(self, clock: FakeClock, seconds: int) -> None:
            super().__init__("codex", [success("implemented")])
            self.clock, self.seconds = clock, seconds

        def execute(self, **kwargs):
            self.clock.advance(seconds=self.seconds)
            return super().execute(**kwargs)

    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FakeClock(now)
    long_job = job.model_copy(update={"agents": AgentSettings(codex=AgentCommand(command=("codex",), timeout_seconds=7200))})
    assert Orchestrator._lease_ttl_seconds(long_job, None) == 7260
    store = SQLiteStore(tmp_path / "state.sqlite3")
    complete = Orchestrator(
        store,
        command_adapter=FakeCommandAdapter(changed=["src/a.py"]),
        codex=AdvancingAgent(clock, 301),
        agy=ScriptedAgentAdapter("agy", [success("audit", AuditReport(passed=True, summary="clean").to_dict())]),
        claude_reviewer=ScriptedAgentAdapter(
            "claude-reviewer", [success("review", ReviewReport(decision=ReviewDecision.APPROVE, summary="ok").to_dict())]
        ),
        clock=clock,
    )
    run = complete.create_run(long_job)
    # No explicit TTL: 7,200 seconds plus the cleanup margin covers the
    # 301-second blocking phase.
    assert complete.run_until_blocked(run.run_id).state is WorkflowState.READY_FOR_HUMAN_REVIEW

    expired_clock = FakeClock(now)
    lost = Orchestrator(
        SQLiteStore(tmp_path / "lost.sqlite3"),
        command_adapter=FakeCommandAdapter(changed=["src/a.py"]),
        codex=AdvancingAgent(expired_clock, 301),
        agy=ScriptedAgentAdapter("agy", []),
        claude_reviewer=ScriptedAgentAdapter("claude-reviewer", []),
        clock=expired_clock,
    )
    run = lost.create_run(long_job)
    with pytest.raises(RunLockError, match="live lock"):
        lost.run_until_blocked(run.run_id, lock_ttl_seconds=300)
    assert lost.store.get_run(run.run_id).state is WorkflowState.IMPLEMENTATION
