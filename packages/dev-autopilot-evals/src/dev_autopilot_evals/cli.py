"""CLI for paper-to-eval authoring on top of the public Dev Autopilot CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dev_autopilot_evals.authoring import (
    DEFAULT_EVAL_COUNT,
    EvalAuthoringError,
    PreparedEvalWorkspace,
    prepare_eval_workspace,
    run_gate,
    validate_eval_pack,
)
from dev_autopilot_evals.runner import ProjectRunError, run_project_start


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", help="local paper file or http(s) URL")
    parser.add_argument(
        "--data",
        action="append",
        default=[],
        type=Path,
        help="supporting local file/directory; repeatable",
    )
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
        prog="dev-autopilot-eval",
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
    build.add_argument(
        "--fake",
        action="store_true",
        help="run Dev Autopilot with its scripted offline adapters (wiring smoke test only)",
    )

    validate = sub.add_parser("validate", help="validate a previously generated eval pack")
    validate.add_argument("root", type=Path)
    validate.add_argument("--expected-count", type=int)
    validate.add_argument("--json", action="store_true")

    gate = sub.add_parser("gate", help="validate the pack and run its deterministic tests")
    gate.add_argument("root", type=Path)
    gate.add_argument("--expected-count", type=int)
    gate.add_argument("--json", action="store_true")

    rerun = sub.add_parser(
        "rerun",
        help=(
            "start a fresh Dev Autopilot run over an already-prepared workspace, "
            "keeping the previous attempt in the same recovery database"
        ),
    )
    rerun.add_argument("root", type=Path)
    rerun.add_argument("--expected-count", type=int, help="defaults to the count recorded at prepare time")
    rerun.add_argument("--json", action="store_true")
    rerun.add_argument("--verbose", action="store_true")
    rerun.add_argument("--fake", action="store_true")
    return parser


def _workspace_payload(workspace: PreparedEvalWorkspace) -> dict[str, Any]:
    return {
        "workspace": workspace.root.as_posix(),
        "project_charter": workspace.charter.as_posix(),
        "source_manifest": workspace.manifest.as_posix(),
        "expected_eval_count": workspace.expected_count,
    }


def _print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _print_validation(root: Path, issues: list[str], *, as_json: bool) -> int:
    payload: dict[str, Any] = {
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


def _recovery_db(root: Path) -> Path:
    return root / ".dev-autopilot" / "autopilot.sqlite3"


def _recorded_expected_count(root: Path) -> int | None:
    """Read the eval count captured when the workspace was prepared."""

    manifest = root / "source" / "source_manifest.json"
    if not manifest.is_file():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    authoring = payload.get("authoring") if isinstance(payload, dict) else None
    count = authoring.get("expected_eval_count") if isinstance(authoring, dict) else None
    return count if isinstance(count, int) else None


def _run_authoring(
    root: Path,
    charter: Path,
    expected_count: int | None,
    *,
    verbose: bool,
    fake: bool,
) -> tuple[dict[str, Any], int]:
    """Execute the orchestrator over ``charter`` and validate the result."""

    result = run_project_start(
        charter,
        db=_recovery_db(root),
        verbose=verbose,
        fake=fake,
    )
    validation_issues = validate_eval_pack(root, expected_count=expected_count)
    payload: dict[str, Any] = {
        "project_run_id": result.project_run_id,
        "project_status": result.status,
        "autopilot_exit_code": result.returncode,
        "validation_passed": not validation_issues,
        "validation_issues": validation_issues,
    }
    exit_code = 0 if result.succeeded and not validation_issues else 1
    return payload, exit_code


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
            run_payload, exit_code = _run_authoring(
                workspace.root,
                workspace.charter,
                workspace.expected_count,
                verbose=args.verbose,
                fake=args.fake,
            )
            _print_payload({**_workspace_payload(workspace), **run_payload}, as_json=args.json)
            return exit_code

        if args.command == "rerun":
            root = args.root.expanduser().resolve()
            charter = root / "project.yaml"
            if not charter.is_file():
                raise EvalAuthoringError(f"not a prepared eval workspace (no project.yaml): {root}")
            expected_count = args.expected_count
            if expected_count is None:
                expected_count = _recorded_expected_count(root)
            run_payload, exit_code = _run_authoring(
                root,
                charter,
                expected_count,
                verbose=args.verbose,
                fake=args.fake,
            )
            _print_payload({"workspace": root.as_posix(), **run_payload}, as_json=args.json)
            return exit_code

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
    except (EvalAuthoringError, ProjectRunError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
