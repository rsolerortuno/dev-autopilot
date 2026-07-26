from __future__ import annotations

import pytest

from dev_autopilot.db import SQLiteStore
from dev_autopilot.engine import TransitionEngine
from dev_autopilot.errors import ErrorClass, TransitionError
from dev_autopilot.states import WorkflowState


def test_valid_and_invalid_transitions(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = store.create_run(job)
    engine = TransitionEngine(store)
    run = engine.transition(run.run_id, WorkflowState.PLAN_VALIDATION, reason="start")
    assert run.state is WorkflowState.PLAN_VALIDATION
    with pytest.raises(TransitionError):
        engine.transition(run.run_id, WorkflowState.FINAL_TESTS, reason="skip")


def test_failed_transition_requires_reason_and_class(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = store.create_run(job)
    engine = TransitionEngine(store)
    with pytest.raises(TransitionError):
        engine.transition(run.run_id, WorkflowState.FAILED, reason="x")
    failed = engine.fail(run.run_id, ErrorClass.TEST_FAILURE, "tests failed")
    assert failed.stop_reason == "tests failed"
    with pytest.raises(TransitionError):
        engine.transition(failed.run_id, WorkflowState.PLAN_VALIDATION, reason="revive")


def test_manual_pause_and_resume(tmp_path, job) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = store.create_run(job)
    engine = TransitionEngine(store)
    run = engine.transition(run.run_id, WorkflowState.PAUSED_HUMAN_DECISION, reason="pause")
    assert run.resume_state is WorkflowState.CREATED
    run = engine.resume(run.run_id)
    assert run.state is WorkflowState.CREATED
