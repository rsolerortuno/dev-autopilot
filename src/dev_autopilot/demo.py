"""Run a deterministic, offline end-to-end Dev Autopilot demonstration."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from dev_autopilot.adapters.fake import FakeCommandAdapter, ScriptedAgentAdapter
from dev_autopilot.bundle import verify_bundle
from dev_autopilot.db import SQLiteStore
from dev_autopilot.gates import GateEvaluator
from dev_autopilot.models import (
    AuditReport,
    EvidencePolicy,
    ExecutionResult,
    FilePathRule,
    JobSpecification,
    ResultStatus,
    ReviewDecision,
    ReviewReport,
    TestCommands,
    TreePathRule,
)
from dev_autopilot.orchestrator import Orchestrator
from dev_autopilot.retries import FakeClock
from dev_autopilot.states import WorkflowState

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _success(summary: str, output: dict[str, object] | None = None) -> ExecutionResult:
    return ExecutionResult(status=ResultStatus.SUCCESS, summary=summary, output=output or {})


def _job(repository: Path, output: Path) -> JobSpecification:
    return JobSpecification(
        name="offline-demo",
        objective="exercise persistent contracts",
        repository=str(repository),
        allowed_paths=(FilePathRule(kind="file", path="README.md"), TreePathRule(kind="tree", path="src")),
        test_commands=TestCommands(
            baseline="python -m compileall .", fast="python -m compileall .", final="python -m compileall ."
        ),
        evidence=EvidencePolicy(milestone_id="M06", bundle_directory=str(output / "bundles")),
    )


def _agents(*, quota: bool = False) -> tuple[ScriptedAgentAdapter, ScriptedAgentAdapter, ScriptedAgentAdapter]:
    codex_results = (
        [ExecutionResult(status=ResultStatus.QUOTA, summary="synthetic quota pause")]
        if quota
        else [_success("synthetic implementation")]
    )
    return (
        ScriptedAgentAdapter("codex", codex_results),
        ScriptedAgentAdapter("agy", [_success("synthetic audit", AuditReport(passed=True, summary="clean").to_dict())]),
        ScriptedAgentAdapter(
            "claude-reviewer",
            [_success("synthetic review", ReviewReport(decision=ReviewDecision.APPROVE, summary="approved").to_dict())],
        ),
    )


def run(output: Path) -> dict[str, object]:
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    repository = output / "fixture-repository"
    (repository / "src").mkdir(parents=True)
    (repository / "README.md").write_text("offline demo fixture\n", encoding="utf-8")
    (repository / "src" / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "-c", "user.name=offline-demo", "-c", "user.email=demo@example.invalid", "commit", "-qm", "fixture"],
        cwd=repository,
        check=True,
    )

    job = _job(repository, output)
    clock = FakeClock(_T0)
    database = output / "demo.sqlite3"
    first_store = SQLiteStore(database)
    codex, agy, reviewer = _agents(quota=True)
    first = Orchestrator(
        first_store,
        command_adapter=FakeCommandAdapter(changed=["src/fixture.py"], diff_content="fixture"),
        codex=codex,
        agy=agy,
        claude_reviewer=reviewer,
        clock=clock,
    )
    run_record = first.create_run(job)
    paused = first.run_until_blocked(run_record.run_id)
    clock.advance(seconds=11)

    second_store = SQLiteStore(database)
    codex, agy, reviewer = _agents()
    second = Orchestrator(
        second_store,
        command_adapter=FakeCommandAdapter(changed=["src/fixture.py"], diff_content="fixture"),
        codex=codex,
        agy=agy,
        claude_reviewer=reviewer,
        clock=clock,
    )
    resumed = second.resume_if_due(run_record.run_id)
    finished = second.run_until_blocked(run_record.run_id)
    bundle_dir = output / "bundles" / str(run_record.run_id)
    bundle_errors = verify_bundle(bundle_dir)
    denied = GateEvaluator(FakeCommandAdapter(changed=["secrets.txt"])).validate_scope(job)
    report = {
        "mode": "offline deterministic demonstration",
        "fake_agents": True,
        "provider_evaluation": False,
        "quality_benchmark": False,
        "orchestrator_end_to_end": finished.state is WorkflowState.READY_FOR_HUMAN_REVIEW,
        "successful_bundle": not bundle_errors,
        "bundle_directory": str(bundle_dir),
        "bundle_verification_errors": bundle_errors,
        "sqlite_database": str(database),
        "quota_pause_restart": {"paused": paused.state.value, "resumed": resumed.state.value, "finished": finished.state.value},
        "denied_path_example": {
            "candidate": "secrets.txt",
            "allowed": denied is None,
            "gate": None if denied is None else denied.error_class.value,
        },
    }
    (output / "demo-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checks = "PASS" if report["successful_bundle"] and not report["denied_path_example"]["allowed"] else "FAIL"  # type: ignore[index]
    report_html = (
        f"<h1>Dev Autopilot offline deterministic demo: {html.escape(str(checks))}</h1>"
        "<p>Fake agents and gates are explicitly synthetic; this is not a quality benchmark.</p>"
        f"<pre>{html.escape(json.dumps(report, indent=2, sort_keys=True))}</pre>"
    )
    (output / "demo-report.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Dev Autopilot offline demo</title>" + report_html,
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["successful_bundle"] and not report["denied_path_example"]["allowed"] else 1  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())
