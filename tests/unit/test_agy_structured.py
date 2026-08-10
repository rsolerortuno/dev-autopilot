from __future__ import annotations

import json

from dev_autopilot.agy_structured import AUDIT_SCHEMA, _agy_command


def test_agy_command_creates_isolated_project() -> None:
    command = _agy_command("audit this repository")

    assert command[:4] == [
        "agy",
        "--new-project",
        "--print",
        "audit this repository",
    ]


def test_agy_command_is_structured_plan_mode() -> None:
    command = _agy_command("audit")

    assert command[command.index("--output-format") + 1] == "stream-json"
    assert command[command.index("--mode") + 1] == "plan"

    schema = json.loads(command[command.index("--json-schema") + 1])
    assert schema == AUDIT_SCHEMA


def test_agy_command_does_not_bypass_permissions() -> None:
    command = _agy_command("audit")

    assert "--dangerously-skip-permissions" not in command
