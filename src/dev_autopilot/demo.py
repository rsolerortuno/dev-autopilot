"""Run a deterministic, offline Dev Autopilot contract demonstration."""

from __future__ import annotations

import argparse
import html
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dev_autopilot.bundle import BundleInputs, verify_bundle, write_bundle
from dev_autopilot.db import SQLiteStore
from dev_autopilot.engine import TransitionEngine
from dev_autopilot.models import FilePathRule, JobSpecification, TestCommands
from dev_autopilot.states import WorkflowState

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def run(output: Path) -> dict[str, object]:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    repository = output / "fixture-repository"
    repository.mkdir()
    (repository / "README.md").write_text("offline demo fixture\n", encoding="utf-8")
    job = JobSpecification(
        name="offline-demo",
        objective="exercise persistent contracts",
        repository=str(repository),
        allowed_paths=(FilePathRule(kind="file", path="README.md"),),
        test_commands=TestCommands(
            baseline="python -m compileall .", fast="python -m compileall .", final="python -m compileall ."
        ),
    )
    store = SQLiteStore(output / "demo.sqlite3")
    engine = TransitionEngine(store)
    run_record = store.create_run(job, run_id=None, now=_T0)
    states = (WorkflowState.PLAN_VALIDATION, WorkflowState.BASELINE_VALIDATION, WorkflowState.IMPLEMENTATION)
    for index, state in enumerate(states):
        run_record = engine.transition(
            run_record.run_id,
            state,
            reason="offline demo",
            actor="deterministic-fake",
            now=_T0 + timedelta(seconds=index + 1),
        )
    run_record = engine.transition(
        run_record.run_id,
        WorkflowState.PAUSED_QUOTA,
        reason="synthetic quota pause",
        actor="deterministic-fake",
        now=_T0 + timedelta(seconds=4),
    )
    run_record = engine.resume(run_record.run_id, reason="synthetic restart")
    bundle_dir = output / "bundle"
    write_bundle(
        BundleInputs(
            project_yaml="project_id: offline-demo\n",
            baseline={"mode": "offline-demo", "provider_evaluation": False},
            final_patch="",
            milestones=[{"milestone_id": "M06", "accepted": True, "total_score": 100, "required_score": 90}],
            findings=[],
            tests={"deterministic_fake_contract_tests": {"passed": True}},
            scientific_gates=[
                {"type": "offline-fixture", "passed": True, "summary": "synthetic fixture only; no provider evaluation"}
            ],
            artifacts=[],
            title="Dev Autopilot offline demonstration",
        ),
        bundle_dir,
        now=_T0,
    )
    verification = verify_bundle(bundle_dir)
    report = {
        "mode": "offline deterministic demonstration",
        "fake_agents": True,
        "provider_evaluation": False,
        "successful_bundle": not verification,
        "bundle_directory": str(bundle_dir),
        "bundle_verification_errors": verification,
        "sqlite_database": str(output / "demo.sqlite3"),
        "quota_pause_restart": {
            "paused": WorkflowState.PAUSED_QUOTA.value,
            "resumed": run_record.state.value,
        },
        "denied_path_example": {"candidate": "secrets.txt", "allowed": job.allows_path("secrets.txt")},
    }
    (output / "demo-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checks = "PASS" if report["successful_bundle"] and not report["denied_path_example"]["allowed"] else "FAIL"  # type: ignore[index]
    (output / "demo-report.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Dev Autopilot offline demo</title>"
        f"<h1>Dev Autopilot offline deterministic demo: {html.escape(str(checks))}</h1>"
        "<p>Fake agents and gates are explicitly synthetic; this is not a quality benchmark.</p>"
        f"<pre>{html.escape(json.dumps(report, indent=2, sort_keys=True))}</pre>",
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
