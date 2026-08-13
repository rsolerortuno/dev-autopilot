from __future__ import annotations

import json
from pathlib import Path

import pytest

from dev_autopilot_evals import cli
from dev_autopilot_evals.runner import ProjectRunResult


def _paper(tmp_path: Path) -> Path:
    paper = tmp_path / "paper.txt"
    paper.write_text("a small paper", encoding="utf-8")
    return paper


def test_prepare_emits_workspace_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "work"
    exit_code = cli.main(["prepare", _paper(tmp_path).as_posix(), "--out", out.as_posix(), "--count", "2", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["expected_eval_count"] == 2
    assert Path(payload["project_charter"]).is_file()
    assert (out / "SCHEMA_CONTRACT.json").is_file()


def test_build_reports_orchestrator_failure_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "run_project_start",
        lambda *_a, **_k: ProjectRunResult(
            returncode=1,
            payload={"status": "FAILED"},
            stdout="",
            stderr="",
        ),
    )
    exit_code = cli.main(
        ["build", _paper(tmp_path).as_posix(), "--out", (tmp_path / "work").as_posix(), "--count", "1", "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["project_status"] == "FAILED"
    # No evals were authored, so validation must also flag the empty pack.
    assert payload["validation_passed"] is False


def test_rerun_reuses_the_recorded_expected_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "work"
    assert cli.main(["prepare", _paper(tmp_path).as_posix(), "--out", out.as_posix(), "--count", "3"]) == 0

    seen: dict[str, object] = {}

    def fake_validate(root: Path, *, expected_count: int | None = None) -> list[str]:
        seen["expected_count"] = expected_count
        return ["stub issue"]

    monkeypatch.setattr(
        cli,
        "run_project_start",
        lambda *_a, **_k: ProjectRunResult(returncode=0, payload={"status": "READY_FOR_HUMAN_REVIEW"}, stdout="", stderr=""),
    )
    monkeypatch.setattr(cli, "validate_eval_pack", fake_validate)

    exit_code = cli.main(["rerun", out.as_posix(), "--json"])

    capsys.readouterr()
    assert exit_code == 1
    assert seen["expected_count"] == 3


def test_rerun_rejects_a_directory_that_is_not_a_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["rerun", tmp_path.as_posix()]) == 2
    assert "no project.yaml" in capsys.readouterr().err


def test_gate_reports_issues_for_an_empty_workspace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "work"
    assert cli.main(["prepare", _paper(tmp_path).as_posix(), "--out", out.as_posix(), "--count", "1"]) == 0
    capsys.readouterr()

    exit_code = cli.main(["gate", out.as_posix(), "--expected-count", "1", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["issues"]
