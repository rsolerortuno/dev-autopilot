"""Opt-in Docker isolation for agent execution.

The local subprocess adapter remains intentionally unsandboxed.  This adapter
requires an explicitly pinned image and constructs a restrictive Docker
invocation; live enforcement still needs a Linux host with Docker installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from dev_autopilot.models import AgentCommand, ExecutionResult, ResultStatus
from dev_autopilot.security import redact, sensitive_values

_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
PopenFactory = Callable[..., subprocess.Popen[str]]


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
    ) -> None:
        if not _DIGEST.search(image):
            raise ValueError("sandbox image must be pinned by an @sha256:<64 hex> digest")
        if pids_limit <= 0:
            raise ValueError("pids_limit must be positive")
        if network_policy != "none" and not network_policy.strip():
            raise ValueError("network_policy must be 'none' or an explicit Docker network")
        self.name = name
        self.settings = settings
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.network_policy = network_policy
        self.docker_binary = docker_binary

    def _docker_command(self, repository: Path, context_file: Path, output_dir: Path, container_name: str) -> list[str]:
        git_dir = repository / ".git"
        if not git_dir.is_dir():
            raise ValueError("sandbox requires a standalone checkout with a .git directory; linked worktrees are unsupported")
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
            "65532:65532",
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
        if shutil.which(self.docker_binary) is None:
            return ExecutionResult(
                status=ResultStatus.ERROR,
                summary="sandbox Docker binary is unavailable; install Docker on Linux",
            )
        repo = repository.resolve()
        if not repo.is_dir():
            return ExecutionResult(status=ResultStatus.ERROR, summary=f"sandbox repository does not exist: {repo}")
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
                command = self._docker_command(repo, context_file, output_dir, container_name)
                try:
                    process = subprocess.Popen(
                        command,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                except OSError as exc:
                    return ExecutionResult(status=ResultStatus.ERROR, summary=f"sandbox could not start Docker: {exc}")
                try:
                    stdout, stderr = process.communicate(timeout=self.settings.timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                    return ExecutionResult(
                        status=ResultStatus.TIMEOUT,
                        summary=f"{self.name} container timed out",
                        stdout=stdout or "",
                        stderr=stderr or "",
                    )
                if process.returncode != 0:
                    return ExecutionResult(
                        status=ResultStatus.ERROR,
                        summary=f"{self.name} container exited with {process.returncode}",
                        stdout=stdout or "",
                        stderr=stderr or "",
                    )
                output_file = output_dir / "output.json"
                if not output_file.is_file():
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary=f"{self.name} container did not create output JSON",
                        stdout=stdout,
                        stderr=stderr,
                    )
                output = json.loads(output_file.read_text(encoding="utf-8"))
                if not isinstance(output, dict):
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary=f"{self.name} container output must be a JSON object",
                    )
                redactions = sensitive_values(context)
                return ExecutionResult(
                    status=ResultStatus.SUCCESS,
                    summary=f"{self.name} container completed",
                    output=redact(output, redactions),
                    stdout=redact(stdout, redactions),
                    stderr=redact(stderr, redactions),
                    exit_code=process.returncode,
                )
        finally:
            subprocess.run([self.docker_binary, "rm", "-f", container_name], capture_output=True, check=False, timeout=30)
