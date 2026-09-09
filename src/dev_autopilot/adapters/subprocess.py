"""Local subprocess adapters. They never perform git writes themselves."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from dev_autopilot.models import AgentCommand, ExecutionResult, ResultStatus, has_shell_syntax


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class LocalCommandAdapter:
    def run(
        self,
        command: str | tuple[str, ...],
        *,
        repository: Path,
        timeout_seconds: int,
        allow_shell: bool = False,
    ) -> ExecutionResult:
        try:
            argv: str | list[str]
            if allow_shell:
                if not isinstance(command, str):
                    return ExecutionResult(status=ResultStatus.MALFORMED, summary="shell commands must be configured as strings")
                argv = command
            else:
                if isinstance(command, str) and has_shell_syntax(command):
                    return ExecutionResult(
                        status=ResultStatus.MALFORMED,
                        summary="shell syntax requires allow_shell=True; configure test_commands.allow_shell: true",
                    )
                try:
                    argv = list(command) if isinstance(command, tuple) else shlex.split(command)
                except ValueError as exc:
                    return ExecutionResult(status=ResultStatus.MALFORMED, summary=f"invalid argv command: {exc}")
                if not argv:
                    return ExecutionResult(status=ResultStatus.MALFORMED, summary="empty argv command")
            process = subprocess.Popen(
                argv,
                cwd=repository,
                shell=allow_shell,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            ExecutableAgentAdapter._terminate_group(process)
            return ExecutionResult(
                status=ResultStatus.TIMEOUT,
                summary=f"command timed out after {timeout_seconds}s",
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr),
            )
        completed = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
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

    _SECURITY_POLL_SECONDS = 0.1

    def __init__(self, name: str, settings: AgentCommand) -> None:
        self.name = name
        self.settings = settings

    @staticmethod
    def _git_run(repository: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return completed.stdout if completed.returncode == 0 else ""

    @classmethod
    def _git_index_path(cls, repository: Path) -> Path:
        raw = cls._git_run(repository, "rev-parse", "--git-path", "index").strip()
        index = Path(raw)
        return index if index.is_absolute() else repository / index

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""

    @classmethod
    def _git_metadata(cls, repository: Path) -> tuple[str, str, str]:
        index_hash = cls._file_sha256(cls._git_index_path(repository))
        # JSON gives a stable, unambiguous representation even when a ref name
        # contains unusual whitespace.
        refs = tuple(sorted(line for line in cls._git_run(repository, "show-ref", "--head").splitlines() if line))
        return (
            cls._git_run(repository, "rev-parse", "HEAD").strip(),
            json.dumps(refs, separators=(",", ":")),
            index_hash,
        )

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str]) -> None:
        """Boundedly terminate the complete session, descendants and pipes."""
        if os.name != "posix":
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=2)
            return
        try:
            killpg = vars(os)["killpg"]
            killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            with suppress(ProcessLookupError):
                vars(os)["killpg"](process.pid, vars(signal).get("SIGKILL", signal.SIGTERM))
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        finally:
            # TimeoutExpired carries any already-read output. Closing the pipe
            # objects here prevents descriptor leaks after a killed process.
            for stream in (process.stdout, process.stderr, process.stdin):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()

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
        protected_index = None if before_git is None else self._git_index_path(repository)
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
            process = subprocess.Popen(
                list(self.settings.command),
                cwd=repository,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            deadline = time.monotonic() + self.settings.timeout_seconds
            stdout = ""
            stderr = ""
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_group(process)
                    if before_git is not None and self._git_metadata(repository) != before_git:
                        return ExecutionResult(
                            status=ResultStatus.SECURITY,
                            summary=f"{self.name} modified git metadata while git writes were disabled",
                            stdout=stdout,
                            stderr=stderr,
                        )
                    return ExecutionResult(
                        status=ResultStatus.TIMEOUT,
                        summary=f"{self.name} timed out",
                        stdout=stdout,
                        stderr=stderr,
                    )
                try:
                    stdout, stderr = process.communicate(timeout=min(self._SECURITY_POLL_SECONDS, remaining))
                    break
                except subprocess.TimeoutExpired as exc:
                    stdout = _text(exc.stdout)
                    stderr = _text(exc.stderr)
                    if (
                        before_git is not None
                        and protected_index is not None
                        and self._file_sha256(protected_index) != before_git[2]
                    ):
                        self._terminate_group(process)
                        return ExecutionResult(
                            status=ResultStatus.SECURITY,
                            summary=f"{self.name} modified git metadata while git writes were disabled",
                            stdout=stdout,
                            stderr=stderr,
                        )
            completed = subprocess.CompletedProcess(list(self.settings.command), process.returncode, stdout, stderr)
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

                bridge_parse_failure = (
                    completed.returncode == 3
                    and "dev_autopilot.bridge" in self.settings.command
                    and "agent stdout did not contain a json object" in combined
                )

                strong_quota_markers = (
                    "insufficient_quota",
                    "quota exceeded",
                    "usage limit reached",
                    "usage limit exceeded",
                    "rate limit exceeded",
                    "too many requests",
                    "http 429",
                    "status 429",
                    "you've hit your usage limit",
                    "you have reached your usage limit",
                )

                if bridge_parse_failure:
                    status = ResultStatus.ERROR
                    summary = f"{self.name} bridge output malformed (exit {completed.returncode})"
                else:
                    status = (
                        ResultStatus.QUOTA if any(marker in combined for marker in strong_quota_markers) else ResultStatus.ERROR
                    )
                    summary = f"{self.name} exited with {completed.returncode}"

                return ExecutionResult(
                    status=status,
                    summary=summary,
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
