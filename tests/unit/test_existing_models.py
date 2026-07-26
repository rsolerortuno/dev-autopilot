from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from dev_autopilot.errors import ErrorClass
from dev_autopilot.models import (
    ContractModel,
    FailureRecord,
    FilePathRule,
    JobSpecification,
    RetryState,
    RunIdentity,
    TransitionEvent,
)
from dev_autopilot.models import TestCommands as Commands
from dev_autopilot.states import WorkflowState

RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
CONFIGURATION_ID = "a" * 64
NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def test_existing_contract_round_trips() -> None:
    models: list[ContractModel] = [
        RunIdentity(run_id=RUN_ID, job_name="job", configuration_id=CONFIGURATION_ID),
        TransitionEvent(
            sequence=1,
            run_id=RUN_ID,
            from_state=WorkflowState.CREATED,
            to_state=WorkflowState.PLAN_VALIDATION,
            occurred_at=NOW,
            reason="plan accepted",
        ),
        RetryState(owner="baseline-validation", count=2, error_class=ErrorClass.RETRYABLE_TIMEOUT),
        FailureRecord(
            run_id=RUN_ID,
            state=WorkflowState.FAST_TESTS,
            error_class=ErrorClass.TEST_FAILURE,
            reason="unit test failed",
            occurred_at=NOW,
            owner="fast-tests",
        ),
        JobSpecification(
            name="job",
            objective="objective",
            repository="/repo",
            allowed_paths=(FilePathRule(kind="file", path="README.md"),),
            test_commands=Commands(baseline="pytest", fast="pytest tests/unit", final="pytest"),
        ),
    ]
    for model in models:
        assert model.__class__.from_json(model.to_json()) == model
        assert model.__class__.from_dict(model.to_dict()) == model


def test_existing_enums_remain_stable() -> None:
    assert len(WorkflowState) == 15
    assert len(ErrorClass) == 11


def test_existing_immutability() -> None:
    retry = RetryState(owner="implementation", count=0, error_class=ErrorClass.RETRYABLE_AGENT_ERROR)
    with pytest.raises(ValidationError):
        retry.count = 1  # type: ignore[misc]
