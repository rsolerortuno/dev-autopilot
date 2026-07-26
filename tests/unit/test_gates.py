from __future__ import annotations

from dev_autopilot.adapters.fake import FakeCommandAdapter
from dev_autopilot.errors import ErrorClass
from dev_autopilot.gates import GateEvaluator


def test_scope_gate_accepts_authorized_paths(job) -> None:
    gate = GateEvaluator(FakeCommandAdapter(changed=["src/a.py", "tests/test_a.py"]))
    assert gate.validate_scope(job) is None


def test_scope_gate_fails_closed(job) -> None:
    gate = GateEvaluator(FakeCommandAdapter(changed=["secrets.txt"]))
    failure = gate.validate_scope(job)
    assert failure is not None
    assert failure.error_class is ErrorClass.SCOPE_VIOLATION
