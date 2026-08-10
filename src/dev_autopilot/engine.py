"""Atomic, fail-closed workflow transition engine."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dev_autopilot.db import SQLiteStore
from dev_autopilot.errors import ErrorClass, TransitionError
from dev_autopilot.models import RunRecord
from dev_autopilot.states import PAUSED_STATES, TERMINAL_STATES, WorkflowState

LEGAL_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.CREATED: frozenset({WorkflowState.PLAN_VALIDATION, WorkflowState.CANCELLED, WorkflowState.FAILED}),
    WorkflowState.PLAN_VALIDATION: frozenset({WorkflowState.BASELINE_VALIDATION, WorkflowState.FAILED, WorkflowState.CANCELLED}),
    WorkflowState.BASELINE_VALIDATION: frozenset({WorkflowState.IMPLEMENTATION, WorkflowState.FAILED, WorkflowState.CANCELLED}),
    WorkflowState.IMPLEMENTATION: frozenset(
        {
            WorkflowState.SCOPE_VALIDATION,
            WorkflowState.PAUSED_QUOTA,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.SCOPE_VALIDATION: frozenset(
        {
            WorkflowState.FAST_TESTS,
            WorkflowState.CORRECTION,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.FAST_TESTS: frozenset(
        {
            WorkflowState.AGY_AUDIT,
            WorkflowState.CORRECTION,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.AGY_AUDIT: frozenset(
        {
            WorkflowState.CLAUDE_REVIEW,
            WorkflowState.CORRECTION,
            WorkflowState.PAUSED_QUOTA,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.CLAUDE_REVIEW: frozenset(
        {
            WorkflowState.CORRECTION,
            WorkflowState.FINAL_TESTS,
            WorkflowState.PAUSED_QUOTA,
            WorkflowState.PAUSED_HUMAN_DECISION,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.CORRECTION: frozenset(
        {
            WorkflowState.SCOPE_VALIDATION,
            WorkflowState.PAUSED_QUOTA,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.FINAL_TESTS: frozenset(
        {
            WorkflowState.READY_FOR_HUMAN_REVIEW,
            WorkflowState.CORRECTION,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.PAUSED_QUOTA: frozenset(
        {
            WorkflowState.IMPLEMENTATION,
            WorkflowState.AGY_AUDIT,
            WorkflowState.CLAUDE_REVIEW,
            WorkflowState.CORRECTION,
            WorkflowState.CANCELLED,
            WorkflowState.FAILED,
        }
    ),
    WorkflowState.PAUSED_HUMAN_DECISION: frozenset(
        {
            WorkflowState.CLAUDE_REVIEW,
            WorkflowState.CORRECTION,
            WorkflowState.FINAL_TESTS,
            WorkflowState.CANCELLED,
            WorkflowState.FAILED,
        }
    ),
    WorkflowState.READY_FOR_HUMAN_REVIEW: frozenset(),
    WorkflowState.FAILED: frozenset(),
    WorkflowState.CANCELLED: frozenset(),
}

# A human may pause any active phase. The previous state is persisted as resume_state.
for _state, _targets in tuple(LEGAL_TRANSITIONS.items()):
    if _state not in TERMINAL_STATES and _state not in PAUSED_STATES:
        LEGAL_TRANSITIONS[_state] = frozenset(set(_targets) | {WorkflowState.PAUSED_HUMAN_DECISION})
_active_states = frozenset(state for state in WorkflowState if state not in TERMINAL_STATES and state not in PAUSED_STATES)
LEGAL_TRANSITIONS[WorkflowState.PAUSED_HUMAN_DECISION] = frozenset(
    set(LEGAL_TRANSITIONS[WorkflowState.PAUSED_HUMAN_DECISION]) | set(_active_states)
)


class TransitionEngine:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def transition(
        self,
        run_id: UUID | str,
        to_state: WorkflowState,
        *,
        reason: str,
        actor: str = "engine",
        failure_class: ErrorClass | None = None,
        stop_reason: str | None = None,
        now: datetime | None = None,
    ) -> RunRecord:
        current = self.store.get_run(run_id)
        if current.state in TERMINAL_STATES:
            raise TransitionError(f"terminal run cannot transition from {current.state}")
        if to_state not in LEGAL_TRANSITIONS[current.state]:
            raise TransitionError(f"illegal transition {current.state} -> {to_state}")
        resume_state = current.state if to_state in PAUSED_STATES else None
        if to_state is WorkflowState.FAILED:
            if failure_class is None or not (stop_reason or reason).strip():
                raise TransitionError("FAILED requires a class and non-empty stop reason")
            stop_reason = stop_reason or reason
        elif to_state is WorkflowState.CANCELLED:
            stop_reason = stop_reason or reason
        else:
            failure_class = None
            stop_reason = None
        return self.store.update_state(
            run_id,
            from_state=current.state,
            to_state=to_state,
            actor=actor,
            reason=reason,
            resume_state=resume_state,
            stop_reason=stop_reason,
            failure_class=failure_class,
            now=now,
        )

    def fail(
        self,
        run_id: UUID | str,
        error_class: ErrorClass,
        reason: str,
        *,
        actor: str = "engine",
    ) -> RunRecord:
        return self.transition(
            run_id,
            WorkflowState.FAILED,
            reason=reason,
            stop_reason=reason,
            failure_class=error_class,
            actor=actor,
        )

    def cancel(self, run_id: UUID | str, *, reason: str = "cancelled by user") -> RunRecord:
        current = self.store.get_run(run_id)
        if current.state is WorkflowState.CANCELLED:
            return current
        return self.transition(run_id, WorkflowState.CANCELLED, reason=reason, actor="human")

    def resume(self, run_id: UUID | str, *, reason: str = "resume requested") -> RunRecord:
        current = self.store.get_run(run_id)
        if current.state not in PAUSED_STATES or current.resume_state is None:
            raise TransitionError(f"run {run_id} is not resumable from {current.state}")
        return self.transition(run_id, current.resume_state, reason=reason, actor="supervisor")
