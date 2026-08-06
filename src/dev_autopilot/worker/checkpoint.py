"""Small entrypoint-side helpers for cooperative M05 checkpoint recovery."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def resume_sequence() -> int:
    """Return the last durable runner checkpoint sequence, or ``-1`` for a new job."""
    raw = os.environ.get("DEV_AUTOPILOT_RESUME_SEQUENCE", "-1")
    try:
        return int(raw)
    except ValueError:
        return -1


def load_resume_checkpoint() -> dict[str, Any]:
    """Read the checkpoint supplied by the worker runner.

    Arbitrary programs cannot be resumed transparently. Entry points use this
    payload to restore their own cursor, epoch, shard, or completed-unit state.
    """
    path = _environment_path("DEV_AUTOPILOT_CHECKPOINT_FILE")
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    progress = value.get("progress")
    return dict(progress) if isinstance(progress, dict) else value


def save_progress(progress: dict[str, Any]) -> Path:
    """Atomically expose application progress for the runner's periodic checkpoint."""
    path = _environment_path("DEV_AUTOPILOT_PROGRESS_FILE")
    if path is None:
        raise RuntimeError("DEV_AUTOPILOT_PROGRESS_FILE is not configured")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(progress, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return path
