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
from dev_autopilot.states import WorkflowState


def _success(summary: str, output=None) -> ExecutionResult:
    return ExecutionResult(
        status=ResultStatus.SUCCESS,
        summary=summary,
        output=output or {},
    )


def test_fast_gate_failure_is_corrected_automatically(tmp_path, job):
    store = SQLiteStore(tmp_path / "state.sqlite3")

    commands = FakeCommandAdapter(
        results=[
            _success("baseline passed"),
            ExecutionResult(
                status=ResultStatus.ERROR,
                summary="command failed with exit code 1",
                stdout=("M00 GATE FAIL: missing required artifact: docs/autopilot/v0.7.0/m00_preflight.json"),
            ),
            _success("fast gate passed after correction"),
            _success("final gate passed"),
        ],
        changed=(),
    )

    codex = ScriptedAgentAdapter(
        "codex",
        [
            _success("initial implementation"),
            _success("corrected implementation"),
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

    orchestrator = Orchestrator(
        store,
        command_adapter=commands,
        codex=codex,
        agy=agy,
        claude_reviewer=reviewer,
    )

    run = orchestrator.create_run(job)
    result = orchestrator.run_until_blocked(run.run_id)

    assert result.state is WorkflowState.READY_FOR_HUMAN_REVIEW
    assert result.correction_rounds == 1
    assert codex.calls == 2

    events = store.list_events(run.run_id)

    correction_events = [event for event in events if event["to_state"] == "CORRECTION"]

    assert len(correction_events) == 1
    assert "missing required artifact" in correction_events[0]["reason"]
