"""Opt-in Docker isolation for agent execution.

The local subprocess adapter remains intentionally unsandboxed.  This adapter
requires an explicitly pinned image and constructs a restrictive Docker
invocation; live enforcement still needs a Linux host with Docker installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from dev_autopilot.models import AgentCommand, ExecutionResult, ResultStatus
from dev_autopilot.security import redact, sensitive_values

_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_UNSAFE_NETWORK = re.compile(r"^(?:host|container(?::.*)?)$")
PopenFactory = Callable[..., subprocess.Popen[str]]


def _runtime_identity() -> tuple[int, int]:
    if os.name != "posix" or not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        raise RuntimeError("sandbox execution requires POSIX uid/gid support")
    uid, gid = os.getuid(), os.getgid()
    if uid == 0 or gid == 0:
        raise RuntimeError("sandbox refuses to run as root")
    return uid, gid


class DockerAgentAdapter:
    """Run an agent in a pinned, non-root, network-disabled container."""

    name: str

    def __init__(
        self,
        name: str,
        settings: AgentCommand,
        *,
        image: str,
        memory: str = "512m",
        cpus: str = "1",
        pids_limit: int = 128,
        network_policy: str = "none",
        docker_binary: str = "docker",
        output_max_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if not _DIGEST.search(image):
            raise ValueError("sandbox image must be pinned by an @sha256:<64 hex> digest")
        if pids_limit <= 0:
            raise ValueError("pids_limit must be positive")
        if (
            not network_policy.strip()
            or any(char in network_policy for char in ",\r\n")
            or _UNSAFE_NETWORK.fullmatch(network_policy)
        ):
            raise ValueError("network_policy must be none, bridge, or an explicit named Docker network")
        if output_max_bytes <= 0:
            raise ValueError("output_max_bytes must be positive")
        self.name = name
        self.settings = settings
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.network_policy = network_policy
        self.docker_binary = docker_binary
        self.output_max_bytes = output_max_bytes

    def _docker_command(self, repository: Path, context_file: Path, output_dir: Path, container_name: str) -> list[str]:
        git_dir = repository / ".git"
        mount_paths = (repository, git_dir, context_file, output_dir)
        if any(any(char in str(path) for char in ",\r\n") for path in mount_paths):
            raise ValueError("sandbox paths must not contain commas or newlines")
        if any(path.is_symlink() for path in (repository, git_dir, context_file, output_dir)):
            raise ValueError("sandbox mount paths must not be symlinks")
        if not git_dir.is_dir():
            raise ValueError("sandbox requires a standalone checkout with a .git directory; linked worktrees are unsupported")
        uid, gid = _runtime_identity()
        return [
            self.docker_binary,
            "run",
            "--pull",
            "never",
            "--name",
            container_name,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--network",
            self.network_policy,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={repository},dst=/workspace",
            "--mount",
            f"type=bind,src={git_dir},dst=/workspace/.git,readonly",
            "--mount",
            f"type=bind,src={context_file},dst=/dev_autopilot/context.json,readonly",
            "--mount",
            f"type=bind,src={output_dir},dst=/dev_autopilot/output",
            "--workdir",
            "/workspace",
            "--env",
            "DEV_AUTOPILOT_CONTEXT_FILE=/dev_autopilot/context.json",
            "--env",
            "DEV_AUTOPILOT_OUTPUT_FILE=/dev_autopilot/output/output.json",
            "--env",
            "DEV_AUTOPILOT_REPOSITORY=/workspace",
            "--user",
            f"{uid}:{gid}",
            self.image,
            *self.settings.command,
        ]

    def execute(
        self,
        *,
        task: str,
        repository: Path,
        context: dict[str, object],
        output_contract: str,
    ) -> ExecutionResult:
        redactions = sensitive_values({"task": task, "context": context})
        if shutil.which(self.docker_binary) is None:
            return ExecutionResult(
                status=ResultStatus.ERROR,
                summary=redact("sandbox Docker binary is unavailable; install Docker on Linux", redactions),
            )
        if repository.is_symlink():
            return ExecutionResult(
                status=ResultStatus.ERROR,
                summary=redact("sandbox repository must not be a symlink", redactions),
            )
        repo = repository.resolve()
        if not repo.is_dir():
            return ExecutionResult(
                status=ResultStatus.ERROR,
                summary=redact(f"sandbox repository does not exist: {repo}", redactions),
            )
        container_name = f"dev-autopilot-{uuid.uuid4().hex}"
        try:
            with tempfile.TemporaryDirectory(prefix="dev-autopilot-sandbox-") as temp:
                root = Path(temp)
                context_file = root / "context.json"
                output_dir = root / "output"
                output_dir.mkdir()
                context_file.write_text(
                    json.dumps({"task": task, "context": context, "output_contract": output_contract}, sort_keys=True),
                    encoding="utf-8",
                )
                try:
                    command = self._docker_command(repo, context_file, output_dir, container_name)
                except (RuntimeError, ValueError) as exc:
                    return ExecutionResult(
                        status=ResultStatus.ERROR,
                        summary=redact(f"sandbox preflight failed: {exc}", redactions),
                    )
                try:
                    process = subprocess.Popen(
                        command,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                except OSError as exc:
                    return ExecutionResult(
                        status=ResultStatus.ERROR,
                        summary=redact(f"sandbox could not start Docker: {exc}", redactions),
                    )
                try:
                    stdout, stderr = process.communicate(timeout=self.settings.timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        stdout, stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        stdout, stderr = "", "docker client did not exit after timeout"
                    return ExecutionResult(
                        status=ResultStatus.TIMEOUT,
                        summary=redact(f"{self.name} container timed out", redactions),
                        stdout=redact(stdout or "", redactions),
                        stderr=redact(stderr or "", redactions),
                    )
                if process.returncode != 0:
                    return ExecutionResult(
                        status=ResultStatus.ERROR,
                        summary=redact(f"{self.name} container exited with {process.returncode}", redactions),
                        stdout=redact(stdout or "", redactions),
                        stderr=redact(stderr or "", redactions),
                    )
                output_file = output_dir / "output.json"
                if output_file.is_symlink() or not output_file.is_file():
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary=redact(f"{self.name} container did not create output JSON", redactions),
                        stdout=redact(stdout, redactions),
                        stderr=redact(stderr, redactions),
                    )
                try:
                    output_size = output_file.stat().st_size
                except OSError as exc:
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary=redact(f"{self.name} output could not be inspected: {exc}", redactions),
                        stdout=redact(stdout, redactions),
                        stderr=redact(stderr, redactions),
                    )
                if output_size > self.output_max_bytes:
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary=redact(f"{self.name} container output exceeds size limit", redactions),
                        stdout=redact(stdout, redactions),
                        stderr=redact(stderr, redactions),
                    )
                try:
                    output = json.loads(output_file.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary=redact(f"{self.name} produced invalid JSON: {exc}", redactions),
                        stdout=redact(stdout, redactions),
                        stderr=redact(stderr, redactions),
                    )
                if not isinstance(output, dict):
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary=redact(f"{self.name} container output must be a JSON object", redactions),
                        stdout=redact(stdout, redactions),
                        stderr=redact(stderr, redactions),
                    )
                return ExecutionResult(
                    status=ResultStatus.SUCCESS,
                    summary=redact(f"{self.name} container completed", redactions),
                    output=redact(output, redactions),
                    stdout=redact(stdout, redactions),
                    stderr=redact(stderr, redactions),
                    exit_code=process.returncode,
                )
        finally:
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run([self.docker_binary, "rm", "-f", container_name], capture_output=True, check=False, timeout=30)
