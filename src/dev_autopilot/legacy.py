"""Import checkpoints from the deprecated Bash supervisor."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from dev_autopilot.db import SQLiteStore
from dev_autopilot.engine import TransitionEngine
from dev_autopilot.errors import ErrorClass
from dev_autopilot.events import EventType
from dev_autopilot.models import JobSpecification, RetryState, RunRecord
from dev_autopilot.states import WorkflowState

_PATHS: dict[WorkflowState, tuple[WorkflowState, ...]] = {
    WorkflowState.CREATED: (),
    WorkflowState.PLAN_VALIDATION: (WorkflowState.PLAN_VALIDATION,),
    WorkflowState.BASELINE_VALIDATION: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
    ),
    WorkflowState.IMPLEMENTATION: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
    ),
    WorkflowState.SCOPE_VALIDATION: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
        WorkflowState.SCOPE_VALIDATION,
    ),
    WorkflowState.FAST_TESTS: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
        WorkflowState.SCOPE_VALIDATION,
        WorkflowState.FAST_TESTS,
    ),
    WorkflowState.AGY_AUDIT: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
        WorkflowState.SCOPE_VALIDATION,
        WorkflowState.FAST_TESTS,
        WorkflowState.AGY_AUDIT,
    ),
    WorkflowState.CLAUDE_REVIEW: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
        WorkflowState.SCOPE_VALIDATION,
        WorkflowState.FAST_TESTS,
        WorkflowState.AGY_AUDIT,
        WorkflowState.CLAUDE_REVIEW,
    ),
    WorkflowState.CORRECTION: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
        WorkflowState.SCOPE_VALIDATION,
        WorkflowState.FAST_TESTS,
        WorkflowState.AGY_AUDIT,
        WorkflowState.CORRECTION,
    ),
    WorkflowState.FINAL_TESTS: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
        WorkflowState.SCOPE_VALIDATION,
        WorkflowState.FAST_TESTS,
        WorkflowState.AGY_AUDIT,
        WorkflowState.CLAUDE_REVIEW,
        WorkflowState.FINAL_TESTS,
    ),
    WorkflowState.READY_FOR_HUMAN_REVIEW: (
        WorkflowState.PLAN_VALIDATION,
        WorkflowState.BASELINE_VALIDATION,
        WorkflowState.IMPLEMENTATION,
        WorkflowState.SCOPE_VALIDATION,
        WorkflowState.FAST_TESTS,
        WorkflowState.AGY_AUDIT,
        WorkflowState.CLAUDE_REVIEW,
        WorkflowState.FINAL_TESTS,
        WorkflowState.READY_FOR_HUMAN_REVIEW,
    ),
}


def read_legacy_checkpoint(path: str | Path) -> dict[str, object]:
    source = Path(path)
    text = source.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("legacy checkpoint is empty")
    if text.startswith("{"):
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("legacy JSON checkpoint must be an object")
        return {str(key).lower(): item for key, item in value.items()}
    result: dict[str, object] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid legacy checkpoint line: {raw_line}")
        key, value = line.split("=", 1)
        result[key.strip().lower()] = value.strip().strip("'\"")
    return result


def _replay(
    engine: TransitionEngine,
    run: RunRecord,
    target: WorkflowState,
) -> RunRecord:
    try:
        path = _PATHS[target]
    except KeyError as exc:
        raise ValueError(f"legacy migration cannot replay active state {target}") from exc
    for next_state in path:
        run = engine.transition(
            run.run_id,
            next_state,
            reason="legacy checkpoint migration",
            actor="legacy-migrator",
        )
    return run


def _state_value(checkpoint: dict[str, object], key: str, default: str) -> WorkflowState:
    return WorkflowState(str(checkpoint.get(key, default)).strip().upper())


def _import_retry(
    store: SQLiteStore,
    run: RunRecord,
    checkpoint: dict[str, object],
) -> None:
    if "retry_count" not in checkpoint:
        return
    owner = str(checkpoint.get("retry_owner", "legacy-agent")).strip()
    count = int(str(checkpoint["retry_count"]))
    error_class = ErrorClass(str(checkpoint.get("error_class", ErrorClass.RETRYABLE_AGENT_ERROR.value)).strip().upper())
    raw_next = checkpoint.get("next_attempt_at")
    next_attempt = None if raw_next in {None, ""} else datetime.fromisoformat(str(raw_next))
    store.set_retry(
        run.run_id,
        RetryState(
            owner=owner,
            count=count,
            error_class=error_class,
            next_attempt_at=next_attempt,
            last_reason=str(checkpoint.get("stop_reason", "legacy retry")),
        ),
    )


def migrate_legacy_checkpoint(
    store: SQLiteStore,
    job: JobSpecification,
    checkpoint_path: str | Path,
    *,
    run_id: UUID | None = None,
) -> RunRecord:
    checkpoint = read_legacy_checkpoint(checkpoint_path)
    target = _state_value(checkpoint, "state", str(checkpoint.get("workflow_state", "CREATED")))
    run = store.create_run(job, run_id=run_id)
    engine = TransitionEngine(store)

    if target in _PATHS:
        run = _replay(engine, run, target)
    elif target in {WorkflowState.PAUSED_QUOTA, WorkflowState.PAUSED_HUMAN_DECISION}:
        default_resume = (
            WorkflowState.IMPLEMENTATION.value if target is WorkflowState.PAUSED_QUOTA else WorkflowState.CREATED.value
        )
        resume_state = _state_value(checkpoint, "resume_state", default_resume)
        run = _replay(engine, run, resume_state)
        run = engine.transition(
            run.run_id,
            target,
            reason=str(checkpoint.get("stop_reason", "legacy run paused")),
            actor="legacy-migrator",
        )
    elif target is WorkflowState.FAILED:
        failure_state = _state_value(checkpoint, "failure_state", WorkflowState.PLAN_VALIDATION.value)
        run = _replay(engine, run, failure_state)
        error_class = ErrorClass(str(checkpoint.get("error_class", ErrorClass.INTERNAL_ORCHESTRATOR_ERROR.value)).strip().upper())
        run = engine.fail(
            run.run_id,
            error_class=error_class,
            reason=str(checkpoint.get("stop_reason", "legacy run failed")),
            actor="legacy-migrator",
        )
    elif target is WorkflowState.CANCELLED:
        run = engine.cancel(run.run_id, reason="legacy run cancelled")
    else:
        raise ValueError(f"unsupported legacy state {target}")

    _import_retry(store, run, checkpoint)
    store.append_event(
        run.run_id,
        EventType.LEGACY_MIGRATED,
        actor="legacy-migrator",
        reason=f"imported {checkpoint_path}",
        payload={"checkpoint": checkpoint},
    )
    return store.get_run(run.run_id)
