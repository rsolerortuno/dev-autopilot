from __future__ import annotations

import sys

import pytest

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
        "auto",
    ]


def test_claude_supervisor_has_builtin_default() -> None:
    assert _default_agent_command("DEV_AUTOPILOT_CLAUDE_SUPERVISOR_COMMAND") == [
        "claude",
        "-p",
        "--permission-mode",
        "auto",
    ]


def test_claude_defaults_never_use_read_only_plan_mode() -> None:
    for env_name in (
        "DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND",
        "DEV_AUTOPILOT_CLAUDE_SUPERVISOR_COMMAND",
    ):
        assert "plan" not in (_default_agent_command(env_name) or [])


def test_claude_permission_mode_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_AUTOPILOT_CLAUDE_PERMISSION_MODE", "acceptEdits")
    assert _default_agent_command("DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND") == [
        "claude",
        "-p",
        "--permission-mode",
        "acceptEdits",
    ]


def test_blank_permission_mode_override_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEV_AUTOPILOT_CLAUDE_PERMISSION_MODE", "   ")
    assert _default_agent_command("DEV_AUTOPILOT_CLAUDE_SUPERVISOR_COMMAND") == [
        "claude",
        "-p",
        "--permission-mode",
        "auto",
    ]


def test_unknown_environment_has_no_default() -> None:
    assert _default_agent_command("DEV_AUTOPILOT_UNKNOWN_COMMAND") is None
