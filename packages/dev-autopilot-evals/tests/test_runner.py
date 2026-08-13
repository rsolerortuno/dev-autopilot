from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dev_autopilot_evals import runner


def test_resolve_prefers_explicit_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_AUTOPILOT_BIN", "/opt/bin/dev-autopilot")
    assert runner.resolve_autopilot_command() == ["/opt/bin/dev-autopilot"]


def test_resolve_falls_back_to_module_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_AUTOPILOT_BIN", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    assert runner.resolve_autopilot_command() == [sys.executable, "-m", "dev_autopilot"]


def test_resolve_uses_console_script_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_AUTOPILOT_BIN", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "/usr/bin/dev-autopilot")
    assert runner.resolve_autopilot_command() == ["/usr/bin/dev-autopilot"]


def test_parse_payload_reads_trailing_json_object() -> None:
    stdout = 'noise line\n{"status": "READY_FOR_HUMAN_REVIEW", "project_run_id": "abc"}\n'
    assert runner._parse_payload(stdout) == {
        "status": "READY_FOR_HUMAN_REVIEW",
        "project_run_id": "abc",
    }


def test_parse_payload_tolerates_missing_json() -> None:
    assert runner._parse_payload("") == {}
    assert runner._parse_payload("no json here") == {}


def _fake_completed(returncode: int, stdout: str, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_project_start_builds_public_cli_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return _fake_completed(0, json.dumps({"status": "READY_FOR_HUMAN_REVIEW", "project_run_id": "run-1"}))

    monkeypatch.setenv("DEV_AUTOPILOT_BIN", "dev-autopilot")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    charter = tmp_path / "project.yaml"
    charter.write_text("project_id: x\n", encoding="utf-8")
    db = tmp_path / ".dev-autopilot" / "autopilot.sqlite3"

    result = runner.run_project_start(charter, db=db, verbose=True)

    assert captured["command"] == [
        "dev-autopilot",
        "--db",
        str(db),
        "project",
        "start",
        str(charter),
        "--json",
        "--verbose",
    ]
    assert db.parent.is_dir()
    assert result.succeeded
    assert result.status == "READY_FOR_HUMAN_REVIEW"
    assert result.project_run_id == "run-1"


def test_run_project_start_reports_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEV_AUTOPILOT_BIN", "dev-autopilot")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_a, **_k: _fake_completed(1, json.dumps({"status": "FAILED"})),
    )
    charter = tmp_path / "project.yaml"
    charter.write_text("project_id: x\n", encoding="utf-8")

    result = runner.run_project_start(charter, db=tmp_path / "db.sqlite3", stream_output=False)

    assert not result.succeeded
    assert result.status == "FAILED"
    assert result.project_run_id is None


def test_run_project_start_raises_when_cli_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEV_AUTOPILOT_BIN", "definitely-not-installed")

    def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("definitely-not-installed")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    charter = tmp_path / "project.yaml"
    charter.write_text("project_id: x\n", encoding="utf-8")

    with pytest.raises(runner.ProjectRunError, match="DEV_AUTOPILOT_BIN"):
        runner.run_project_start(charter, db=tmp_path / "db.sqlite3")
