"""Fail-closed repository scope and policy gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev_autopilot.adapters.base import CommandAdapter
from dev_autopilot.errors import ErrorClass
from dev_autopilot.models import ExecutionResult, JobSpecification, ResultStatus


@dataclass(frozen=True)
class GateFailure:
    error_class: ErrorClass
    reason: str


class GateEvaluator:
    def __init__(self, command_adapter: CommandAdapter) -> None:
        self.command_adapter = command_adapter

    def validate_scope(self, job: JobSpecification) -> GateFailure | None:
        repository = Path(job.repository)
        changed = self.command_adapter.changed_files(repository=repository)
        if len(changed) > job.gates.max_changed_files:
            return GateFailure(
                ErrorClass.SCOPE_VIOLATION,
                f"changed file count {len(changed)} exceeds limit {job.gates.max_changed_files}",
            )
        denied = tuple(path for path in changed if not job.allows_path(path))
        if denied:
            return GateFailure(
                ErrorClass.SCOPE_VIOLATION,
                "unauthorized changed paths: " + ", ".join(denied),
            )
        return None

    def run_test(self, job: JobSpecification, command: str) -> ExecutionResult:
        return self.command_adapter.run(command, repository=Path(job.repository), timeout_seconds=3600)

    @staticmethod
    def classify_command(result: ExecutionResult) -> GateFailure | None:
        if result.status is ResultStatus.SUCCESS:
            return None
        if result.status is ResultStatus.TIMEOUT:
            return GateFailure(ErrorClass.RETRYABLE_TIMEOUT, result.summary)
        return GateFailure(ErrorClass.TEST_FAILURE, result.summary)
