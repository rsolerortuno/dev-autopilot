"""Application-facing checkpoint helper tests."""

from __future__ import annotations

import json

from dev_autopilot.worker.checkpoint import load_resume_checkpoint, resume_sequence, save_progress


def test_checkpoint_helpers_round_trip(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint.json"
    progress = tmp_path / "progress.json"
    checkpoint.write_text(json.dumps({"sequence": 4, "progress": {"shard": 12}}), encoding="utf-8")
    monkeypatch.setenv("DEV_AUTOPILOT_CHECKPOINT_FILE", str(checkpoint))
    monkeypatch.setenv("DEV_AUTOPILOT_PROGRESS_FILE", str(progress))
    monkeypatch.setenv("DEV_AUTOPILOT_RESUME_SEQUENCE", "4")

    assert resume_sequence() == 4
    assert load_resume_checkpoint() == {"shard": 12}
    assert save_progress({"shard": 13}) == progress
    assert json.loads(progress.read_text(encoding="utf-8")) == {"shard": 13}


def test_checkpoint_helpers_fail_closed_without_environment(monkeypatch):
    for name in (
        "DEV_AUTOPILOT_CHECKPOINT_FILE",
        "DEV_AUTOPILOT_PROGRESS_FILE",
        "DEV_AUTOPILOT_RESUME_SEQUENCE",
    ):
        monkeypatch.delenv(name, raising=False)
    assert resume_sequence() == -1
    assert load_resume_checkpoint() == {}
