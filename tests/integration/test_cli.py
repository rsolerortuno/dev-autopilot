from __future__ import annotations

import json
from pathlib import Path

from dev_autopilot.cli import main


def test_cli_init_start_status_and_events(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "job.yaml"
    db = tmp_path / "state.sqlite3"
    assert main(["init", str(config), "--repository", str(repo)]) == 0
    assert main(["--db", str(db), "start", str(config), "--fake", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[-1] if False else "{}")
    del payload
    # Read the only run id directly from SQLite through the public status path.
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db)) as connection:
        run_id = connection.execute("SELECT run_id FROM runs").fetchone()[0]
    assert main(["--db", str(db), "status", run_id, "--json"]) == 0
    status_output = capsys.readouterr().out
    assert "READY_FOR_HUMAN_REVIEW" in status_output
    assert main(["--db", str(db), "events", run_id]) == 0
    assert "RUN_CREATED" in capsys.readouterr().out
