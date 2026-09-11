from __future__ import annotations

import sqlite3
from pathlib import Path

from dev_autopilot.cli import main

DIFF = "a" * 64


def _run_id(db: Path) -> str:
    with sqlite3.connect(db) as connection:
        return connection.execute("SELECT run_id FROM runs").fetchone()[0]


def test_cli_approval_grant_binds_current_diff_and_is_single_use(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "job.yaml"
    db = tmp_path / "state.sqlite3"
    assert main(["init", str(config), "--repository", str(repo)]) == 0
    assert main(["--db", str(db), "start", str(config), "--fake"]) == 0
    run_id = _run_id(db)
    monkeypatch.setattr("dev_autopilot.cli.LocalCommandAdapter.diff_sha256", lambda self, repository: DIFF)
    assert main(["--db", str(db), "approval", "issue", run_id, "--actor", "alice", "--ttl-seconds", "60"]) == 0
    grant = capsys.readouterr().out.strip().splitlines()[-1]
    assert main(["--db", str(db), "approve", run_id, "--grant-id", grant, "--actor", "alice"]) == 0
    assert main(["--db", str(db), "approve", run_id, "--grant-id", grant, "--actor", "alice"]) == 2


def test_cli_approval_grant_rejects_changed_diff(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "job.yaml"
    db = tmp_path / "state.sqlite3"
    assert main(["init", str(config), "--repository", str(repo)]) == 0
    assert main(["--db", str(db), "start", str(config), "--fake"]) == 0
    run_id = _run_id(db)
    monkeypatch.setattr("dev_autopilot.cli.LocalCommandAdapter.diff_sha256", lambda self, repository: DIFF)
    assert main(["--db", str(db), "approval", "issue", run_id, "--actor", "alice"]) == 0
    grant = capsys.readouterr().out.strip().splitlines()[-1]
    monkeypatch.setattr("dev_autopilot.cli.LocalCommandAdapter.diff_sha256", lambda self, repository: "b" * 64)
    assert main(["--db", str(db), "approve", run_id, "--grant-id", grant, "--actor", "alice"]) == 2


def test_cli_approval_issue_prefixes_dash_starting_random_id(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "job.yaml"
    db = tmp_path / "state.sqlite3"
    assert main(["init", str(config), "--repository", str(repo)]) == 0
    assert main(["--db", str(db), "start", str(config), "--fake"]) == 0
    run_id = _run_id(db)
    monkeypatch.setattr("dev_autopilot.cli.LocalCommandAdapter.diff_sha256", lambda self, repository: DIFF)
    monkeypatch.setattr("dev_autopilot.authorization.secrets.token_urlsafe", lambda length: "-I8-regression")
    assert main(["--db", str(db), "approval", "issue", run_id, "--actor", "alice"]) == 0
    grant = capsys.readouterr().out.strip().splitlines()[-1]
    assert grant == "grant_-I8-regression"
    assert main(["--db", str(db), "approve", run_id, "--grant-id", grant, "--actor", "alice"]) == 0


def test_cli_approval_consumes_legacy_dash_grant_with_equals_syntax(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "job.yaml"
    db = tmp_path / "state.sqlite3"
    assert main(["init", str(config), "--repository", str(repo)]) == 0
    assert main(["--db", str(db), "start", str(config), "--fake"]) == 0
    run_id = _run_id(db)
    monkeypatch.setattr("dev_autopilot.cli.LocalCommandAdapter.diff_sha256", lambda self, repository: DIFF)
    assert main(["--db", str(db), "approval", "issue", run_id, "--actor", "alice"]) == 0
    issued = capsys.readouterr().out.strip().splitlines()[-1]
    legacy = "-legacy-grant"
    with sqlite3.connect(db.with_name("authorization.sqlite3")) as connection:
        connection.execute("UPDATE approval_grants SET grant_id = ? WHERE grant_id = ?", (legacy, issued))
        connection.commit()
    assert main(["--db", str(db), "approve", run_id, f"--grant-id={legacy}", "--actor", "alice"]) == 0
