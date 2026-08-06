"""Reproducible repository baseline capture (M03).

The baseline records tracked and untracked content, Git identity, dependency
metadata and the execution environment.  It is captured before implementation
and is safe to compare across restarts and machines.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import StringConstraints, field_validator

from dev_autopilot.models import ContractModel, NonEmptyString

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class EnvironmentFingerprint(ContractModel):
    python_version: NonEmptyString
    platform: NonEmptyString
    machine: NonEmptyString
    installed_packages_sha256: Sha256

    @classmethod
    def capture(cls) -> EnvironmentFingerprint:
        packages = sorted(f"{dist.name}=={dist.version}" for dist in importlib.metadata.distributions())
        packages_sha = hashlib.sha256(("\n".join(packages) + "\n").encode("utf-8")).hexdigest()
        return cls(
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            machine=platform.machine() or "unknown",
            installed_packages_sha256=packages_sha,
        )


class BaselineCapture(ContractModel):
    repository: NonEmptyString
    git_commit: str | None = None
    git_branch: str | None = None
    git_dirty: bool = False
    git_status_sha256: Sha256
    tracked_tree_sha256: Sha256
    untracked_tree_sha256: Sha256
    tree_sha256: Sha256
    dependency_files_sha256: Sha256
    baseline_command: NonEmptyString
    baseline_passed: bool
    environment: EnvironmentFingerprint
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value.astimezone(UTC)

    @property
    def baseline_id(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()


def _run_git(repository: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _git_paths(repository: Path, *args: str) -> tuple[Path, ...] | None:
    listing = _run_git(repository, *args)
    if listing is None:
        return None
    return tuple(repository / name for name in listing.split("\0") if name)


def _all_files(repository: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(repository.rglob("*"))
        if path.is_file() and ".git" not in path.parts and ".dev-autopilot" not in path.parts
    )


def _digest_files(repository: Path, paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(repository).as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(repository).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        file_hash = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                file_hash.update(block)
        digest.update(file_hash.hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def compute_tree_sha256(repository: Path) -> str:
    """Digest every relevant repository file, including untracked inputs."""
    tracked = _git_paths(repository, "ls-files", "-z")
    untracked = _git_paths(repository, "ls-files", "--others", "--exclude-standard", "-z")
    paths = _all_files(repository) if tracked is None else tuple({*(tracked or ()), *(untracked or ())})
    return _digest_files(repository, paths)


def _dependency_digest(repository: Path) -> str:
    names = (
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "environment.yml",
        "environment.yaml",
        "Dockerfile",
    )
    paths = tuple(repository / name for name in names if (repository / name).is_file())
    return _digest_files(repository, paths)


def capture_baseline(
    repository: Path | str,
    *,
    baseline_command: str,
    baseline_passed: bool,
    now: datetime | None = None,
) -> BaselineCapture:
    repo = Path(repository).resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"repository directory not found: {repo}")
    tracked = _git_paths(repo, "ls-files", "-z")
    untracked = _git_paths(repo, "ls-files", "--others", "--exclude-standard", "-z")
    if tracked is None:
        tracked = _all_files(repo)
        untracked = ()
    status = (_run_git(repo, "status", "--porcelain=v2", "-z") or "").encode("utf-8")
    commit = (_run_git(repo, "rev-parse", "HEAD") or "").strip() or None
    branch = (_run_git(repo, "branch", "--show-current") or "").strip() or None
    tracked_sha = _digest_files(repo, tracked)
    untracked_sha = _digest_files(repo, untracked or ())
    combined = hashlib.sha256(f"{tracked_sha}\0{untracked_sha}".encode("ascii")).hexdigest()
    return BaselineCapture(
        repository=repo.as_posix(),
        git_commit=commit,
        git_branch=branch,
        git_dirty=bool(status),
        git_status_sha256=hashlib.sha256(status).hexdigest(),
        tracked_tree_sha256=tracked_sha,
        untracked_tree_sha256=untracked_sha,
        tree_sha256=combined,
        dependency_files_sha256=_dependency_digest(repo),
        baseline_command=baseline_command,
        baseline_passed=baseline_passed,
        environment=EnvironmentFingerprint.capture(),
        captured_at=now or datetime.now(UTC),
    )
