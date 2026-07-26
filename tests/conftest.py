from __future__ import annotations

from pathlib import Path

import pytest

from dev_autopilot.models import JobSpecification


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    return repo


@pytest.fixture
def job(repository: Path) -> JobSpecification:
    return JobSpecification.model_validate(
        {
            "name": "test-job",
            "objective": "Implement the requested change",
            "repository": str(repository),
            "allowed_paths": [
                {"kind": "tree", "path": "src"},
                {"kind": "tree", "path": "tests"},
                {"kind": "file", "path": "README.md"},
            ],
            "test_commands": {
                "baseline": "pytest baseline",
                "fast": "pytest fast",
                "final": "pytest final",
            },
            "retry_policy": {
                "delays_seconds": [10, 20],
                "max_attempts": 3,
                "jitter_fraction": 0.0,
            },
        }
    )
