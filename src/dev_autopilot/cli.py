"""Command-line interface for persistent autopilot runs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from dev_autopilot import cli_storage
from dev_autopilot.adapters.fake import FakeCommandAdapter, ScriptedAgentAdapter
from dev_autopilot.adapters.subprocess import ExecutableAgentAdapter, LocalCommandAdapter
from dev_autopilot.config import load_job_configuration
from dev_autopilot.db import SCHEMA_VERSION, SQLiteStore
from dev_autopilot.engine import TransitionEngine
from dev_autopilot.errors import AutopilotError, ConfigurationError, TransitionError
from dev_autopilot.events import EventType
from dev_autopilot.legacy import migrate_legacy_checkpoint
from dev_autopilot.models import (
    AuditReport,
    ExecutionResult,
    JobSpecification,
    ResultStatus,
    ReviewDecision,
    ReviewReport,
)
from dev_autopilot.orchestrator import Orchestrator
from dev_autopilot.project import ContinuousProjectRunner, ProjectStore, load_project_charter
from dev_autopilot.states import PAUSED_STATES, TERMINAL_STATES, WorkflowState

DEFAULT_DB = Path(".dev-autopilot/autopilot.sqlite3")


def _success(summary: str, output: dict[str, object] | None = None) -> ExecutionResult:
    return ExecutionResult(status=ResultStatus.SUCCESS, summary=summary, output=output or {})


def _build_orchestrator(
    store: SQLiteStore,
    job: JobSpecification,
    *,
    fake: bool,
    progress: Callable[[str], None] | None = None,
) -> Orchestrator:
    if fake:
        commands = FakeCommandAdapter(changed=())
        codex = ScriptedAgentAdapter("codex", [_success("fake implementation")])
        agy = ScriptedAgentAdapter(
            "agy",
            [_success("fake audit", AuditReport(passed=True, summary="clean").to_dict())],
        )
        reviewer = ScriptedAgentAdapter(
            "claude-reviewer",
            [
                _success(
                    "fake review",
                    ReviewReport(decision=ReviewDecision.APPROVE, summary="approved").to_dict(),
                )
            ],
        )
        return Orchestrator(
            store,
            command_adapter=commands,
            codex=codex,
            agy=agy,
            claude_reviewer=reviewer,
            progress=progress,
        )
    if not job.gates.allow_network:
        raise ConfigurationError("real agent execution requires explicit gates.allow_network: true")
    missing = [
        name
        for name, settings in (
            ("codex", job.agents.codex),
            ("agy", job.agents.agy),
            ("claude_reviewer", job.agents.claude_reviewer),
        )
        if settings is None
    ]
    if missing:
        raise ConfigurationError("real run requires agent commands for: " + ", ".join(missing))
    return Orchestrator(
        store,
        command_adapter=LocalCommandAdapter(),
        codex=ExecutableAgentAdapter("codex", job.agents.codex),  # type: ignore[arg-type]
        agy=ExecutableAgentAdapter("agy", job.agents.agy),  # type: ignore[arg-type]
        claude_reviewer=ExecutableAgentAdapter(
            "claude-reviewer",
            job.agents.claude_reviewer,  # type: ignore[arg-type]
        ),
        claude_supervisor=(
            None
            if job.agents.claude_supervisor is None
            else ExecutableAgentAdapter(
                "claude-supervisor",
                job.agents.claude_supervisor,
            )
        ),
        progress=progress,
    )


def _print_progress(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    print(
        f"[{timestamp}] {message}",
        file=sys.stderr,
        flush=True,
    )


def _print_run(run: object, *, as_json: bool = False) -> None:
    payload = run.to_dict() if hasattr(run, "to_dict") else run
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if isinstance(payload, dict):
        for key in (
            "run_id",
            "state",
            "resume_state",
            "stop_reason",
            "failure_class",
            "correction_rounds",
            "approved_at",
            "archived_at",
        ):
            if key in payload and payload[key] is not None:
                print(f"{key}: {payload[key]}")


def _template(repository: str) -> str:
    return f"""name: example-development-job
objective: Implement the assigned issue and leave it ready for human review
repository: {repository}
allowed_paths:
  - kind: tree
    path: src
  - kind: tree
    path: tests
  - kind: file
    path: README.md
test_commands:
  baseline: pytest -q
  fast: pytest -q
  final: pytest -q
  # Strings are argv-parsed by default. Set allow_shell: true only for trusted
  # shell syntax such as pipes or &&.
  allow_shell: false
retry_policy:
  delays_seconds: [1, 2, 3, 4, 5, 6, 7, 8]
  max_attempts: 8
  jitter_fraction: 0.1
gates:
  allow_network: false
  allow_git_writes: false
  max_changed_files: 200
  max_context_bytes: 2000000
review:
  max_correction_rounds: 3
  require_agy: true
scientific_invariants: []
# Real adapters use argv arrays and communicate through
# DEV_AUTOPILOT_CONTEXT_FILE / DEV_AUTOPILOT_OUTPUT_FILE.
agents: {{}}
"""


def _project_template(repository: str) -> str:
    job_text = "\n".join(f"      {line}" for line in _template(repository).splitlines())
    return f"""project_id: example-project
title: Example continuous project
mission: Complete every milestone with verified evidence and no conversational questions
definition_of_done:
  - every milestone is accepted
  - the final review bundle verifies
non_goals:
  - automatic merge
autonomy:
  mode: continuous
  ask_questions: false
  ambiguity_policy: safest_reversible_assumption
  destructive_actions: deny
  overwrite_policy: version_outputs
  human_gate: release_only
milestones:
  - milestone_id: M05
    title: Final M05 delivery
    objective: Implement, test, review and package the requested M05 scope
    dependencies: []
    job:
{job_text}
      evidence:
        milestone_id: M05
        required_score: 90
        generate_bundle: true
        enforce_acceptance: true
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev-autopilot")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--job", type=Path)

    init = sub.add_parser("init")
    init.add_argument("path", type=Path)
    init.add_argument("--repository", default=".")
    init.add_argument("--force", action="store_true")

    start = sub.add_parser("start")
    start.add_argument("job", type=Path)
    start.add_argument("--fake", action="store_true")
    start.add_argument("--json", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("run_id", type=UUID)
    status.add_argument("--json", action="store_true")

    events = sub.add_parser("events")
    events.add_argument("run_id", type=UUID)

    watch = sub.add_parser("watch")
    watch.add_argument("run_id", type=UUID)
    watch.add_argument("job", type=Path)
    watch.add_argument("--fake", action="store_true")
    watch.add_argument("--interval", type=float, default=5.0)

    pause = sub.add_parser("pause")
    pause.add_argument("run_id", type=UUID)
    pause.add_argument("--reason", default="paused by user")

    resume = sub.add_parser("resume")
    resume.add_argument("run_id", type=UUID)
    resume.add_argument("job", type=Path)
    resume.add_argument("--fake", action="store_true")

    cancel = sub.add_parser("cancel")
    cancel.add_argument("run_id", type=UUID)

    approve = sub.add_parser("approve")
    approve.add_argument("run_id", type=UUID)

    archive = sub.add_parser("archive")
    archive.add_argument("run_id", type=UUID)

    migrate = sub.add_parser("migrate-legacy")
    migrate.add_argument("job", type=Path)
    migrate.add_argument("checkpoint", type=Path)

    project = sub.add_parser("project", help="continuous no-questions project execution")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_init = project_sub.add_parser("init")
    project_init.add_argument("path", type=Path)
    project_init.add_argument("--repository", default=".")
    project_init.add_argument("--force", action="store_true")
    project_plan = project_sub.add_parser("plan")
    project_plan.add_argument("charter", type=Path)
    project_start = project_sub.add_parser("start")
    project_start.add_argument("charter", type=Path)
    project_start.add_argument("--fake", action="store_true")
    project_start.add_argument("--json", action="store_true")
    project_start.add_argument("--verbose", action="store_true")
    project_resume = project_sub.add_parser("resume")
    project_resume.add_argument("project_run_id", type=UUID)
    project_resume.add_argument("--fake", action="store_true")
    project_resume.add_argument("--json", action="store_true")
    project_resume.add_argument("--verbose", action="store_true")
    project_status = project_sub.add_parser("status")
    project_status.add_argument("project_run_id", type=UUID)
    project_status.add_argument("--json", action="store_true")

    cli_storage.register(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            checks: list[tuple[str, bool, str]] = [
                ("python", sys.version_info >= (3, 11), sys.version.split()[0]),
                ("sqlite", sqlite3.sqlite_version_info >= (3, 35, 0), sqlite3.sqlite_version),
                ("git", shutil.which("git") is not None, shutil.which("git") or "missing"),
                (
                    "worker-platform",
                    os.name == "posix",
                    "POSIX (Linux/WSL2 supported)" if os.name == "posix" else "unsupported native Windows; use Linux or WSL2",
                ),
            ]
            if args.job:
                job = load_job_configuration(args.job)
                repository = Path(job.repository)
                checks.append(("repository", repository.is_dir(), str(repository)))
                for name, setting in (
                    ("codex", job.agents.codex),
                    ("agy", job.agents.agy),
                    ("claude-reviewer", job.agents.claude_reviewer),
                ):
                    if setting:
                        executable = setting.command[0]
                        checks.append((name, shutil.which(executable) is not None, executable))
            for name, ok, detail in checks:
                print(f"{'OK' if ok else 'FAIL'} {name}: {detail}")
            print(f"schema_version: {SCHEMA_VERSION}")
            return 0 if all(ok for _, ok, _ in checks) else 1

        if args.command == "init":
            if args.path.exists() and not args.force:
                raise ConfigurationError(f"refusing to overwrite {args.path}; use --force")
            args.path.parent.mkdir(parents=True, exist_ok=True)
            args.path.write_text(_template(args.repository), encoding="utf-8")
            print(args.path)
            return 0

        if args.command == "project" and args.project_command == "init":
            if args.path.exists() and not args.force:
                raise ConfigurationError(f"refusing to overwrite {args.path}; use --force")
            args.path.parent.mkdir(parents=True, exist_ok=True)
            args.path.write_text(_project_template(args.repository), encoding="utf-8")
            print(args.path)
            return 0
        if args.command == "project" and args.project_command == "plan":
            charter = load_project_charter(args.charter)
            print(
                json.dumps(
                    {
                        "project_id": charter.project_id,
                        "mission": charter.mission,
                        "autonomy": charter.autonomy.to_dict(),
                        "milestones": [m.to_dict() for m in charter.ordered_milestones()],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        storage_exit = cli_storage.handle(args)
        if storage_exit is not None:
            return storage_exit

        store = SQLiteStore(args.db)

        if args.command == "project":
            project_store = ProjectStore(store)
            if args.project_command == "status":
                payload = project_store.get(args.project_run_id)
                print(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload["status"])
                return 0
            runner = ContinuousProjectRunner(
                store,
                lambda job: _build_orchestrator(
                    store,
                    job,
                    fake=args.fake,
                    progress=(_print_progress if args.verbose else None),
                ),
            )
            if args.project_command == "start":
                project_run_id = runner.start(load_project_charter(args.charter))
                payload = project_store.get(project_run_id)
            elif args.project_command == "resume":
                payload = runner.run(args.project_run_id)
            else:
                raise AssertionError(f"unhandled project command {args.project_command}")
            print(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload["status"])
            return 0 if payload["status"] != "FAILED" else 1

        if args.command == "start":
            job = load_job_configuration(args.job)
            orchestrator = _build_orchestrator(store, job, fake=args.fake)
            run = orchestrator.create_run(job)
            run = orchestrator.run_until_blocked(run.run_id)
            _print_run(run, as_json=args.json)
            return 0 if run.state is not WorkflowState.FAILED else 1

        if args.command == "status":
            _print_run(store.get_run(args.run_id), as_json=args.json)
            return 0

        if args.command == "events":
            print(json.dumps(store.list_events(args.run_id), indent=2, sort_keys=True))
            return 0

        if args.command == "pause":
            engine = TransitionEngine(store)
            run = engine.transition(
                args.run_id,
                WorkflowState.PAUSED_HUMAN_DECISION,
                reason=args.reason,
                actor="human",
            )
            _print_run(run)
            return 0

        if args.command == "resume":
            job = load_job_configuration(args.job)
            orchestrator = _build_orchestrator(store, job, fake=args.fake)
            TransitionEngine(store).resume(args.run_id)
            run = orchestrator.run_until_blocked(args.run_id)
            _print_run(run)
            return 0 if run.state is not WorkflowState.FAILED else 1

        if args.command == "watch":
            job = load_job_configuration(args.job)
            orchestrator = _build_orchestrator(store, job, fake=args.fake)
            while True:
                run = orchestrator.resume_if_due(args.run_id)
                if run.state not in PAUSED_STATES:
                    run = orchestrator.run_until_blocked(args.run_id)
                _print_run(run)
                if run.state in TERMINAL_STATES or run.state is WorkflowState.PAUSED_HUMAN_DECISION:
                    return 0 if run.state is not WorkflowState.FAILED else 1
                time.sleep(max(0.1, args.interval))

        if args.command == "cancel":
            store.request_cancel(args.run_id)
            run = TransitionEngine(store).cancel(args.run_id)
            store.append_event(
                args.run_id,
                EventType.CANCEL_REQUESTED,
                actor="human",
                reason="cancel command",
            )
            _print_run(run)
            return 0

        if args.command == "approve":
            run = store.get_run(args.run_id)
            if run.state is not WorkflowState.READY_FOR_HUMAN_REVIEW:
                raise TransitionError("only READY_FOR_HUMAN_REVIEW runs can be approved")
            store.mark_approved(args.run_id)
            store.append_event(args.run_id, EventType.HUMAN_APPROVED, actor="human", reason="approved")
            _print_run(store.get_run(args.run_id))
            return 0

        if args.command == "archive":
            run = store.get_run(args.run_id)
            if run.state not in TERMINAL_STATES:
                raise TransitionError("only terminal runs can be archived")
            store.mark_archived(args.run_id)
            store.append_event(args.run_id, EventType.ARCHIVED, actor="human", reason="archived")
            _print_run(store.get_run(args.run_id))
            return 0

        if args.command == "migrate-legacy":
            run = migrate_legacy_checkpoint(store, load_job_configuration(args.job), args.checkpoint)
            _print_run(run)
            return 0

        raise AssertionError(f"unhandled command {args.command}")
    except (AutopilotError, ConfigurationError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
