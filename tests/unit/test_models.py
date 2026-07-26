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


def test_all_required_stable_enum_values() -> None:
    assert {state.value for state in WorkflowState} == {
        "CREATED",
        "PLAN_VALIDATION",
        "BASELINE_VALIDATION",
        "IMPLEMENTATION",
        "SCOPE_VALIDATION",
        "FAST_TESTS",
        "AGY_AUDIT",
        "CLAUDE_REVIEW",
        "CORRECTION",
        "FINAL_TESTS",
        "READY_FOR_HUMAN_REVIEW",
        "PAUSED_QUOTA",
        "PAUSED_HUMAN_DECISION",
        "FAILED",
        "CANCELLED",
    }
    assert {error.value for error in ErrorClass} == {
        "RETRYABLE_QUOTA",
        "RETRYABLE_TIMEOUT",
        "RETRYABLE_AGENT_ERROR",
        "MECHANICAL_OUTPUT_ERROR",
        "JOB_CONFIGURATION_ERROR",
        "SCOPE_VIOLATION",
        "TEST_FAILURE",
        "REVIEW_FORMAT_ERROR",
        "SCIENTIFIC_DECISION_REQUIRED",
        "SECURITY_VIOLATION",
        "INTERNAL_ORCHESTRATOR_ERROR",
    }


@pytest.mark.parametrize(
    "model",
    [
        RunIdentity(
            run_id=RUN_ID,
            job_name="job",
            configuration_id=CONFIGURATION_ID,
        ),
        TransitionEvent(
            sequence=1,
            run_id=RUN_ID,
            from_state=WorkflowState.CREATED,
            to_state=WorkflowState.PLAN_VALIDATION,
            occurred_at=NOW,
            reason="plan accepted",
        ),
        RetryState(
            owner="baseline-validation",
            count=2,
            error_class=ErrorClass.RETRYABLE_TIMEOUT,
        ),
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
            test_commands=Commands(
                baseline="pytest",
                fast="pytest tests/unit",
                final="pytest",
            ),
        ),
    ],
)
def test_model_json_round_trip_preserves_values(model: ContractModel) -> None:
    assert model.__class__.from_json(model.to_json()) == model
    assert model.__class__.from_dict(model.to_dict()) == model


def test_models_are_immutable() -> None:
    retry = RetryState(
        owner="implementation",
        count=0,
        error_class=ErrorClass.RETRYABLE_AGENT_ERROR,
    )

    with pytest.raises(ValidationError):
        retry.count = 1  # type: ignore[misc]


@pytest.mark.parametrize("reason", ["", "   "])
def test_failure_reason_must_be_non_empty(reason: str) -> None:
    with pytest.raises(ValidationError):
        FailureRecord(
            run_id=RUN_ID,
            state=WorkflowState.FAILED,
            error_class=ErrorClass.INTERNAL_ORCHESTRATOR_ERROR,
            reason=reason,
            occurred_at=NOW,
        )


def test_retry_owner_is_required_and_count_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        RetryState(
            owner="",
            count=0,
            error_class=ErrorClass.RETRYABLE_TIMEOUT,
        )
    with pytest.raises(ValidationError):
        RetryState(
            owner="agent",
            count=-1,
            error_class=ErrorClass.RETRYABLE_TIMEOUT,
        )
