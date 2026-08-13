"""Drive the unmodified ``dev-autopilot`` CLI as an external program.

The companion package deliberately refuses to import Dev Autopilot internals
such as ``dev_autopilot.cli._build_orchestrator``. Everything it needs is
already exposed by the public command line::

    dev-autopilot --db DB project start CHARTER --json [--verbose]

Talking to that boundary keeps the core free to refactor its own internals
without breaking eval authoring, and keeps eval authoring installable next to
any compatible Dev Autopilot release.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class ProjectRunError(RuntimeError):
    """Raised when the Dev Autopilot CLI could not be located or executed."""


@dataclass(frozen=True)
class ProjectRunResult:
    """Outcome of one ``dev-autopilot project start`` invocation."""

    returncode: int
    payload: dict[str, object]
    stdout: str
    stderr: str

    @property
    def status(self) -> str:
        status = self.payload.get("status")
        return status if isinstance(status, str) else "UNKNOWN"

    @property
    def project_run_id(self) -> str | None:
        for key in ("project_run_id", "id"):
            value = self.payload.get(key)
            if isinstance(value, str):
                return value
        return None

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.status not in {"FAILED", "UNKNOWN"}


def resolve_autopilot_command() -> list[str]:
    """Return the argv prefix that runs the Dev Autopilot CLI.

    ``DEV_AUTOPILOT_BIN`` wins when set, so a caller can point at a specific
    checkout or virtualenv. Otherwise the installed console script is used, and
    finally ``python -m dev_autopilot``, which works even when the script
    directory is not on ``PATH``.
    """

    override = os.environ.get("DEV_AUTOPILOT_BIN", "").strip()
    if override:
        return [override]
    executable = shutil.which("dev-autopilot")
    if executable:
        return [executable]
    return [sys.executable, "-m", "dev_autopilot"]


def _parse_payload(stdout: str) -> dict[str, object]:
    """Read the last JSON object printed on stdout.

    ``--verbose`` progress goes to stderr, so stdout normally holds exactly the
    JSON document. Scanning from the end keeps this robust if a future release
    prints anything ahead of it.
    """

    stripped = stdout.strip()
    if not stripped:
        return {}
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        pass
    else:
        return value if isinstance(value, dict) else {}
    decoder = json.JSONDecoder()
    for index in range(len(stripped) - 1, -1, -1):
        if stripped[index] != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def run_project_start(
    charter: Path,
    *,
    db: Path,
    verbose: bool = False,
    fake: bool = False,
    extra_args: Sequence[str] = (),
    stream_output: bool = True,
) -> ProjectRunResult:
    """Run ``dev-autopilot project start`` on ``charter`` and parse its JSON."""

    command = [
        *resolve_autopilot_command(),
        "--db",
        str(db),
        "project",
        "start",
        str(charter),
        "--json",
    ]
    if verbose:
        command.append("--verbose")
    if fake:
        command.append("--fake")
    command.extend(extra_args)

    db.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on host PATH
        raise ProjectRunError(
            f"could not execute the Dev Autopilot CLI ({command[0]}); install dev-autopilot or set DEV_AUTOPILOT_BIN"
        ) from exc

    if stream_output and completed.stderr:
        sys.stderr.write(completed.stderr)
        sys.stderr.flush()

    return ProjectRunResult(
        returncode=completed.returncode,
        payload=_parse_payload(completed.stdout),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
