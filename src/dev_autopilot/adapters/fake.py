"""Deterministic fake adapters used by offline tests and demos."""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from dev_autopilot.models import ExecutionResult, ResultStatus


class ScriptedAgentAdapter:
    def __init__(self, name: str, results: Iterable[ExecutionResult]) -> None:
        self.name = name
        self._results = deque(results)
        self.calls = 0

    def execute(
        self,
        *,
        task: str,
        repository: Path,
        context: dict[str, object],
        output_contract: str,
    ) -> ExecutionResult:
        del task, repository, context, output_contract
        self.calls += 1
        if not self._results:
            return ExecutionResult(status=ResultStatus.SUCCESS, summary=f"{self.name} ok")
        return self._results.popleft()


class FakeCommandAdapter:
    def __init__(
        self,
        results: Iterable[ExecutionResult] | None = None,
        *,
        changed: Iterable[str] = (),
        diff_content: str = "",
    ) -> None:
        self._results = deque(results or ())
        self._changed = tuple(changed)
        self._diff_content = diff_content
        self.calls: list[str] = []

    def run(
        self,
        command: str,
        *,
        repository: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        del repository, timeout_seconds
        self.calls.append(command)
        if self._results:
            return self._results.popleft()
        return ExecutionResult(status=ResultStatus.SUCCESS, summary=f"passed: {command}")

    def changed_files(self, *, repository: Path) -> tuple[str, ...]:
        del repository
        return self._changed

    def diff_sha256(self, *, repository: Path) -> str:
        del repository
        return hashlib.sha256(self._diff_content.encode()).hexdigest()
