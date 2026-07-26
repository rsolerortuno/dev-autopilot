from __future__ import annotations

import json

import pytest

from dev_autopilot import bridge
from dev_autopilot.bridge import _extract_json


def test_bridge_extracts_plain_and_fenced_json() -> None:
    assert _extract_json('{"passed": true, "summary": "ok", "findings": []}')["passed"] is True
    assert _extract_json('text\n```json\n{"decision":"APPROVE","summary":"ok","findings":[]}\n```')["decision"] == "APPROVE"


def test_bridge_rejects_missing_json() -> None:
    with pytest.raises(ValueError):
        _extract_json("not json")


@pytest.mark.parametrize(
    "text",
    [
        '```\n{"passed": true}\n```',
        'before {"passed": true} after',
        'first {"first": 1} then {"second": 2}',
    ],
)
def test_bridge_extracts_embedded_json_deterministically(text: str) -> None:
    assert _extract_json(text) == ({"first": 1} if "first" in text else {"passed": True})


@pytest.mark.parametrize("text", ["[]", "42", "{bad", "[1, 2]"])
def test_bridge_rejects_non_object_or_malformed_json(text: str) -> None:
    with pytest.raises(ValueError):
        _extract_json(text)


def test_bridge_main_exit_branches_and_canonical_output(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("MISSING_COMMAND", raising=False)
    assert bridge.main(["--env", "MISSING_COMMAND"]) == 2
    assert bridge.main([]) == 2

    context = tmp_path / "context.json"
    output = tmp_path / "output.json"
    context.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DEV_AUTOPILOT_CONTEXT_FILE", str(context))
    monkeypatch.setenv("DEV_AUTOPILOT_OUTPUT_FILE", str(output))
    assert bridge.main(["--", "python", "-c", "import sys; sys.exit(7)"]) == 7
    assert bridge.main(["--", "python", "-c", "print('no object')"]) == 3
    assert bridge.main(["--", "python", "-c", 'print(\'{\\"z\\": 1, \\"a\\": 2}\')']) == 0
    assert output.read_text(encoding="utf-8") == '{"a": 2, "z": 1}'
    assert "no agent command configured" in capsys.readouterr().err


def test_bridge_reads_command_from_environment(tmp_path, monkeypatch) -> None:
    context = tmp_path / "context.json"
    output = tmp_path / "output.json"
    context.write_text(json.dumps({"input": 1}), encoding="utf-8")
    monkeypatch.setenv("DEV_AUTOPILOT_CONTEXT_FILE", str(context))
    monkeypatch.setenv("DEV_AUTOPILOT_OUTPUT_FILE", str(output))
    monkeypatch.setenv("AGENT_COMMAND", 'python -c "print(\'{\\"ok\\": true}\')"')
    assert bridge.main(["--env", "AGENT_COMMAND"]) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {"ok": True}
