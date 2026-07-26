"""Local subprocess adapters. They never perform git writes themselves."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from dev_autopilot.models import AgentCommand, ExecutionResult, ResultStatus


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class LocalCommandAdapter:
    def run(
        self,
        command: str,
        *,
        repository: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        try:
            completed = subprocess.run(
                command,
                cwd=repository,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                status=ResultStatus.TIMEOUT,
                summary=f"command timed out after {timeout_seconds}s",
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr),
            )
        status = ResultStatus.SUCCESS if completed.returncode == 0 else ResultStatus.ERROR
        return ExecutionResult(
            status=status,
            summary=(
                f"command succeeded: {command}"
                if completed.returncode == 0
                else f"command failed with exit code {completed.returncode}: {command}"
            ),
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
        )

    def _git(self, repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def changed_files(self, *, repository: Path) -> tuple[str, ...]:
        paths: set[str] = set()
        for args in (
            ("diff", "--name-only", "--relative"),
            ("diff", "--cached", "--name-only", "--relative"),
            ("ls-files", "--others", "--exclude-standard"),
        ):
            result = self._git(repository, *args)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "git inspection failed")
            paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
        return tuple(sorted(paths))

    def diff_sha256(self, *, repository: Path) -> str:
        result = self._git(repository, "diff", "--binary", "HEAD")
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git diff failed")
        untracked = self.changed_files(repository=repository)
        manifest = result.stdout + "\n" + "\n".join(untracked)
        for path in untracked:
            candidate = repository / path
            if candidate.is_file():
                manifest += "\n" + path + ":" + hashlib.sha256(candidate.read_bytes()).hexdigest()
        return hashlib.sha256(manifest.encode()).hexdigest()


class ExecutableAgentAdapter:
    """Run an arbitrary executable with file-based bounded context and JSON output.

    The configured process receives DEV_AUTOPILOT_CONTEXT_FILE and
    DEV_AUTOPILOT_OUTPUT_FILE. It must write a JSON object matching
    ExecutionResult.output's phase-specific contract.
    """

    def __init__(self, name: str, settings: AgentCommand) -> None:
        self.name = name
        self.settings = settings

    @staticmethod
    def _git_metadata(repository: Path) -> tuple[str, str, str]:
        def run(*args: str) -> str:
            completed = subprocess.run(["git", *args], cwd=repository, text=True, capture_output=True, timeout=30, check=False)
            return completed.stdout if completed.returncode == 0 else ""

        index = repository / ".git" / "index"
        index_hash = hashlib.sha256(index.read_bytes()).hexdigest() if index.is_file() else ""
        return run("rev-parse", "HEAD").strip(), run("show-ref"), index_hash

    def execute(
        self,
        *,
        task: str,
        repository: Path,
        context: dict[str, object],
        output_contract: str,
    ) -> ExecutionResult:
        gates = context.get("gates", {})
        allow_git_writes = bool(gates.get("allow_git_writes", False)) if isinstance(gates, dict) else False
        before_git = None if allow_git_writes else self._git_metadata(repository)
        payload = {"task": task, "context": context, "output_contract": output_contract}
        with tempfile.TemporaryDirectory(prefix="dev-autopilot-") as temp_dir:
            temp = Path(temp_dir)
            context_file = temp / "context.json"
            output_file = temp / "output.json"
            context_file.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "DEV_AUTOPILOT_CONTEXT_FILE": str(context_file),
                    "DEV_AUTOPILOT_OUTPUT_FILE": str(output_file),
                    "DEV_AUTOPILOT_REPOSITORY": str(repository),
                }
            )
            try:
                completed = subprocess.run(
                    list(self.settings.command),
                    cwd=repository,
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=self.settings.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return ExecutionResult(
                    status=ResultStatus.TIMEOUT,
                    summary=f"{self.name} timed out",
                    stdout=_text(exc.stdout),
                    stderr=_text(exc.stderr),
                )
            if before_git is not None and self._git_metadata(repository) != before_git:
                return ExecutionResult(
                    status=ResultStatus.SECURITY,
                    summary=f"{self.name} modified git metadata while git writes were disabled",
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    exit_code=completed.returncode,
                )
            if completed.returncode != 0:
                combined = (completed.stderr + "\n" + completed.stdout).lower()
                status = (
                    ResultStatus.QUOTA
                    if any(token in combined for token in ("quota", "rate limit", "capacity"))
                    else ResultStatus.ERROR
                )
                return ExecutionResult(
                    status=status,
                    summary=f"{self.name} exited with {completed.returncode}",
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    exit_code=completed.returncode,
                )
            if not output_file.exists():
                return ExecutionResult(
                    status=ResultStatus.MALFORMED,
                    summary=f"{self.name} did not create output JSON",
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    exit_code=completed.returncode,
                )
            try:
                output = json.loads(output_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return ExecutionResult(
                    status=ResultStatus.MALFORMED,
                    summary=f"{self.name} produced invalid JSON: {exc}",
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    exit_code=completed.returncode,
                )
            if not isinstance(output, dict):
                return ExecutionResult(
                    status=ResultStatus.MALFORMED,
                    summary=f"{self.name} output must be a JSON object",
                    output={"raw": output},
                )
            return ExecutionResult(
                status=ResultStatus.SUCCESS,
                summary=f"{self.name} completed",
                output=output,
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
            )
