from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dev_autopilot.errors import ErrorClass
from dev_autopilot.models import (
    FailureRecord,
    JobSpecification,
    RetryState,
    validate_path_rule,
)


def test_configuration_hash_is_key_order_independent(repository) -> None:
    first = {
        "name": "x",
        "objective": "y",
        "repository": str(repository),
        "allowed_paths": [{"kind": "tree", "path": "src"}],
        "test_commands": {"baseline": "a", "fast": "b", "final": "c"},
    }
    second = {
        "test_commands": {"final": "c", "baseline": "a", "fast": "b"},
        "allowed_paths": [{"path": "src", "kind": "tree"}],
        "repository": str(repository),
        "objective": "y",
        "name": "x",
    }
    assert JobSpecification.model_validate(first).configuration_id == JobSpecification.model_validate(second).configuration_id


@pytest.mark.parametrize("value", ["/etc/passwd", "../secret", "src\\x.py", "src/"])
def test_path_rules_reject_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError):
        validate_path_rule({"kind": "file", "path": value})


def test_path_rule_semantics() -> None:
    assert validate_path_rule({"kind": "file", "path": "README.md"}).matches("README.md")
    assert not validate_path_rule({"kind": "file", "path": "README.md"}).matches("docs/README.md")
    assert validate_path_rule({"kind": "tree", "path": "src"}).matches("src/a/b.py")
    assert validate_path_rule({"kind": "glob", "pattern": "tests/test_*.py"}).matches("tests/test_a.py")
    assert not validate_path_rule({"kind": "glob", "pattern": "tests/test_*.py"}).matches("tests/unit/test_a.py")


def test_duplicate_rules_fail_closed(repository) -> None:
    with pytest.raises(ValidationError):
        JobSpecification.model_validate(
            {
                "name": "x",
                "objective": "y",
                "repository": str(repository),
                "allowed_paths": [
                    {"kind": "tree", "path": "src"},
                    {"kind": "tree", "path": "src"},
                ],
                "test_commands": {"baseline": "a", "fast": "b", "final": "c"},
            }
        )


def test_failure_reason_and_retry_count_are_validated() -> None:
    with pytest.raises(ValidationError):
        FailureRecord(
            run_id=uuid4(),
            state="FAILED",
            error_class=ErrorClass.TEST_FAILURE,
            reason=" ",
            occurred_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        RetryState(owner="codex", count=-1, error_class=ErrorClass.RETRYABLE_QUOTA)
