"""The companion package must never reach into Dev Autopilot internals.

The whole point of shipping eval authoring as a separate distribution is that
``dev-autopilot`` stays a general orchestrator that knows nothing about papers,
Latch or evals. That contract only holds if this package talks to the public
command line and nothing else, so it is asserted mechanically rather than left
to review discipline.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "dev_autopilot_evals"
SOURCE_FILES = sorted(SOURCE_ROOT.glob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_source_files_are_discovered() -> None:
    assert {path.name for path in SOURCE_FILES} >= {"authoring.py", "cli.py", "runner.py"}


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda path: path.name)
def test_no_module_imports_dev_autopilot(path: Path) -> None:
    offenders = {name for name in _imported_modules(path) if name == "dev_autopilot" or name.startswith("dev_autopilot.")}
    assert not offenders, f"{path.name} imports Dev Autopilot internals: {sorted(offenders)}"


def test_charter_uses_the_public_bridge_entry_point() -> None:
    """Agent commands may name ``dev_autopilot.bridge``: that is a public CLI."""

    from dev_autopilot_evals.authoring import _agent_bridge

    command = _agent_bridge("DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND")["command"]
    assert command[1:] == ["-m", "dev_autopilot.bridge", "--env", "DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND"]


def test_generated_test_commands_call_this_package(tmp_path: Path) -> None:
    import yaml

    from dev_autopilot_evals.authoring import prepare_eval_workspace

    paper = tmp_path / "paper.txt"
    paper.write_text("paper", encoding="utf-8")
    workspace = prepare_eval_workspace(paper.as_posix(), output=tmp_path / "work", expected_count=2)
    job = yaml.safe_load(workspace.charter.read_text(encoding="utf-8"))["milestones"][0]["job"]

    for phase in ("baseline", "fast", "final"):
        assert "dev_autopilot_evals.authoring" in job["test_commands"][phase]
        assert "dev_autopilot.eval_authoring" not in job["test_commands"][phase]
