from __future__ import annotations

from pathlib import Path

from dev_autopilot.adapters.fake import FakeCommandAdapter
from dev_autopilot.errors import ErrorClass
from dev_autopilot.gates import GateEvaluator
from dev_autopilot.models import ExecutionResult, ResultStatus


def test_scope_gate_accepts_authorized_paths(job) -> None:
    gate = GateEvaluator(FakeCommandAdapter(changed=["src/a.py", "tests/test_a.py"]))
    assert gate.validate_scope(job) is None


def test_scope_gate_fails_closed(job) -> None:
    gate = GateEvaluator(FakeCommandAdapter(changed=["secrets.txt"]))
    failure = gate.validate_scope(job)
    assert failure is not None
    assert failure.error_class is ErrorClass.SCOPE_VIOLATION


def test_scope_limit_accepts_exact_limit_and_rejects_one_over(job) -> None:
    exact = job.model_copy(update={"gates": job.gates.model_copy(update={"max_changed_files": 2})})
    assert GateEvaluator(FakeCommandAdapter(changed=["src/a.py", "tests/a.py"])).validate_scope(exact) is None
    failure = GateEvaluator(FakeCommandAdapter(changed=["src/a.py", "tests/a.py", "README.md"])).validate_scope(exact)
    assert failure is not None
    assert failure.error_class is ErrorClass.SCOPE_VIOLATION
    assert failure.reason == "changed file count 3 exceeds limit 2"


def test_classify_command_covers_all_status_families() -> None:
    assert GateEvaluator.classify_command(ExecutionResult(status=ResultStatus.SUCCESS, summary="ok")) is None
    timeout = GateEvaluator.classify_command(ExecutionResult(status=ResultStatus.TIMEOUT, summary="late"))
    assert timeout is not None and timeout.error_class is ErrorClass.RETRYABLE_TIMEOUT
    for status in (ResultStatus.ERROR, ResultStatus.MALFORMED, ResultStatus.SECURITY, ResultStatus.QUOTA):
        failure = GateEvaluator.classify_command(ExecutionResult(status=status, summary=status.value))
        assert failure is not None and failure.error_class is ErrorClass.TEST_FAILURE


def test_run_test_forwards_command_repository_timeout_and_shell(job) -> None:
    class RecordingAdapter(FakeCommandAdapter):
        received: tuple[object, ...] | None = None

        def run(self, command, *, repository, timeout_seconds, allow_shell=False):
            self.received = (command, repository, timeout_seconds, allow_shell)
            return super().run(command, repository=repository, timeout_seconds=timeout_seconds, allow_shell=allow_shell)

    adapter = RecordingAdapter()
    shell_job = job.model_copy(update={"test_commands": job.test_commands.model_copy(update={"allow_shell": True})})
    GateEvaluator(adapter).run_test(shell_job, "echo ok")
    assert adapter.received == ("echo ok", Path(shell_job.repository), 3600, True)
