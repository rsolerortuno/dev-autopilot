from __future__ import annotations

import sys

from dev_autopilot.bridge import _default_agent_command


def test_codex_has_builtin_default() -> None:
    assert _default_agent_command("DEV_AUTOPILOT_CODEX_COMMAND") == [
        "codex",
        "exec",
        "--full-auto",
        "-",
    ]


def test_agy_has_builtin_structured_default() -> None:
    assert _default_agent_command("DEV_AUTOPILOT_AGY_COMMAND") == [
        sys.executable,
        "-m",
        "dev_autopilot.agy_structured",
    ]


def test_claude_reviewer_has_builtin_default() -> None:
    assert _default_agent_command("DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND") == [
        "claude",
        "-p",
        "--permission-mode",
        "plan",
    ]


def test_claude_supervisor_has_builtin_default() -> None:
    assert _default_agent_command("DEV_AUTOPILOT_CLAUDE_SUPERVISOR_COMMAND") == [
        "claude",
        "-p",
        "--permission-mode",
        "plan",
    ]


def test_unknown_environment_has_no_default() -> None:
    assert _default_agent_command("DEV_AUTOPILOT_UNKNOWN_COMMAND") is None
