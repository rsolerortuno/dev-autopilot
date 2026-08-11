"""CLI for paper-to-eval authoring built on the existing Dev Autopilot engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dev_autopilot.cli import _build_orchestrator, _print_progress
from dev_autopilot.db import SQLiteStore
from dev_autopilot.eval_authoring import (
    DEFAULT_EVAL_COUNT,
    EvalAuthoringError,
    prepare_eval_workspace,
    run_gate,
    validate_eval_pack,
)
from dev_autopilot.project import ContinuousProjectRunner, ProjectStore, load_project_charter


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", help="local paper file or http(s) URL")
    parser.add_argument("--data", action="append", default=[], type=Path, help="supporting local file/directory; repeatable")
    parser.add_argument("--data-url", action="append", default=[], help="supporting http(s) file URL; repeatable")
    parser.add_argument("--out", type=Path, default=Path("scientific-eval-workspace"))
    parser.add_argument("--count", type=int, default=DEFAULT_EVAL_COUNT, help="number of evals to author")
    parser.add_argument(
        "--focus",
        default="Function / experimental biology",
        help="scientific role emphasis supplied to the eval author",
    )
    parser.add_argument("--force", action="store_true", help="replace a prior marked eval workspace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev-autopilot eval",
        description="Turn a paper plus optional data into a validated Latch-style scientific eval pack.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="ingest evidence and create the eval-authoring workspace only")
    _add_source_arguments(prepare)
    prepare.add_argument("--json", action="store_true")

    build = sub.add_parser("build", help="prepare, run the multi-agent authoring loop, and validate the eval pack")
    _add_source_arguments(build)
    build.add_argument("--json", action="store_true")
    build.add_argument("--verbose", action="store_true")

    validate = sub.add_parser("validate", help="validate a previously generated eval pack")
    validate.add_argument("root", type=Path)
    validate.add_argument("--expected-count", type=int)
    validate.add_argument("--json", action="store_true")

    gate = sub.add_parser("gate", help="validate the pack and run its deterministic tests")
    gate.add_argument("root", type=Path)
    gate.add_argument("--expected-count", type=int)
    gate.add_argument("--json", action="store_true")
    return parser


def _workspace_payload(workspace: Any) -> dict[str, object]:
    return {
        "workspace": workspace.root.as_posix(),
        "project_charter": workspace.charter.as_posix(),
        "source_manifest": workspace.manifest.as_posix(),
        "expected_eval_count": workspace.expected_count,
    }


def _print_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _print_validation(root: Path, issues: list[str], *, as_json: bool) -> int:
    payload: dict[str, object] = {
        "workspace": root.expanduser().resolve().as_posix(),
        "passed": not issues,
        "issues": issues,
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif issues:
        for issue in issues:
            print(f"FAIL {issue}")
    else:
        print("OK")
    return 0 if not issues else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"prepare", "build"}:
            workspace = prepare_eval_workspace(
                args.source,
                output=args.out,
                data=args.data,
                data_urls=args.data_url,
                expected_count=args.count,
                focus=args.focus,
                force=args.force,
            )
            if args.command == "prepare":
                _print_payload(_workspace_payload(workspace), as_json=args.json)
                return 0

            db_path = workspace.root / ".dev-autopilot" / "autopilot.sqlite3"
            store = SQLiteStore(db_path)
            runner = ContinuousProjectRunner(
                store,
                lambda job: _build_orchestrator(
                    store,
                    job,
                    fake=False,
                    progress=(_print_progress if args.verbose else None),
                ),
            )
            project_run_id = runner.start(load_project_charter(workspace.charter))
            project_payload = ProjectStore(store).get(project_run_id)
            validation_issues = validate_eval_pack(workspace.root, expected_count=workspace.expected_count)
            payload = {
                **_workspace_payload(workspace),
                "project_run_id": str(project_run_id),
                "project_status": project_payload["status"],
                "validation_passed": not validation_issues,
                "validation_issues": validation_issues,
            }
            _print_payload(payload, as_json=args.json)
            return 0 if project_payload["status"] != "FAILED" and not validation_issues else 1

        if args.command == "validate":
            return _print_validation(
                args.root,
                validate_eval_pack(args.root, expected_count=args.expected_count),
                as_json=args.json,
            )
        if args.command == "gate":
            return _print_validation(
                args.root,
                run_gate(args.root, expected_count=args.expected_count),
                as_json=args.json,
            )
        raise AssertionError(f"unhandled eval command: {args.command}")
    except (EvalAuthoringError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
