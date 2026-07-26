"""Resumable end-to-end orchestration loop."""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

from dev_autopilot.adapters.base import AgentAdapter, CommandAdapter
from dev_autopilot.db import SQLiteStore
from dev_autopilot.engine import TransitionEngine
from dev_autopilot.errors import AdapterError, ErrorClass, RunLockError
from dev_autopilot.events import EventType
from dev_autopilot.gates import GateEvaluator
from dev_autopilot.models import (
    AuditReport,
    ExecutionResult,
    JobSpecification,
    ResultStatus,
    ReviewDecision,
    ReviewReport,
    RunRecord,
)
from dev_autopilot.retries import Clock, RetryScheduler, SystemClock
from dev_autopilot.review import (
    AUDIT_CONTRACT,
    IMPLEMENTATION_CONTRACT,
    REVIEW_CONTRACT,
    parse_audit_output,
    parse_review_output,
)
from dev_autopilot.states import PAUSED_STATES, TERMINAL_STATES, WorkflowState


class Orchestrator:
    """Coordinate deterministic gates and independent agent adapters."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        command_adapter: CommandAdapter,
        codex: AgentAdapter,
        agy: AgentAdapter,
        claude_reviewer: AgentAdapter,
        claude_supervisor: AgentAdapter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.store = store
        self.engine = TransitionEngine(store)
        self.commands = command_adapter
        self.gates = GateEvaluator(command_adapter)
        self.codex = codex
        self.agy = agy
        self.claude_reviewer = claude_reviewer
        self.claude_supervisor = claude_supervisor
        self.clock = clock or SystemClock()

    def create_run(self, job: JobSpecification, *, run_id: UUID | None = None) -> RunRecord:
        return self.store.create_run(job, run_id=run_id, now=self.clock.now())

    def run_until_blocked(
        self,
        run_id: UUID | str,
        *,
        max_steps: int = 100,
        lock_ttl_seconds: int = 300,
    ) -> RunRecord:
        token = f"orchestrator-{uuid4()}"
        self.store.acquire_lock(run_id, token, ttl_seconds=lock_ttl_seconds, now=self.clock.now())
        try:
            for _ in range(max_steps):
                run = self.store.get_run(run_id)
                if run.cancel_requested and run.state not in TERMINAL_STATES:
                    return self.engine.cancel(run_id, reason="persistent cancellation requested")
                if run.state in TERMINAL_STATES or run.state in PAUSED_STATES:
                    return run
                before = run.state
                self.step(run_id)
                after = self.store.get_run(run_id)
                if after.state == before:
                    return after
            return self.engine.fail(
                run_id,
                ErrorClass.INTERNAL_ORCHESTRATOR_ERROR,
                f"step limit exceeded: {max_steps}",
            )
        finally:
            with suppress(RunLockError):
                self.store.release_lock(run_id, token)

    def step(self, run_id: UUID | str) -> RunRecord:
        run = self.store.get_run(run_id)
        handlers = {
            WorkflowState.CREATED: self._created,
            WorkflowState.PLAN_VALIDATION: self._plan_validation,
            WorkflowState.BASELINE_VALIDATION: self._baseline,
            WorkflowState.IMPLEMENTATION: self._implementation,
            WorkflowState.SCOPE_VALIDATION: self._scope,
            WorkflowState.FAST_TESTS: self._fast_tests,
            WorkflowState.AGY_AUDIT: self._agy_audit,
            WorkflowState.CLAUDE_REVIEW: self._claude_review,
            WorkflowState.CORRECTION: self._correction,
            WorkflowState.FINAL_TESTS: self._final_tests,
        }
        handler = handlers.get(run.state)
        if handler is None:
            return run
        try:
            return handler(run)
        except Exception as exc:
            return self.engine.fail(
                run.run_id,
                ErrorClass.INTERNAL_ORCHESTRATOR_ERROR,
                f"{run.state} raised {type(exc).__name__}: {exc}",
            )

    def resume_if_due(self, run_id: UUID | str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state is not WorkflowState.PAUSED_QUOTA or run.resume_state is None:
            return run
        retry = self.store.get_retry(run_id, self._owner_for_state(run.resume_state))
        if retry is None or RetryScheduler(run.job.retry_policy, self.clock).due(retry):
            return self.engine.resume(run_id, reason="retry deadline reached")
        return run

    def _created(self, run: RunRecord) -> RunRecord:
        return self.engine.transition(run.run_id, WorkflowState.PLAN_VALIDATION, reason="begin plan validation")

    def _plan_validation(self, run: RunRecord) -> RunRecord:
        repository = Path(run.job.repository)
        if not repository.exists() or not repository.is_dir():
            return self.engine.fail(
                run.run_id,
                ErrorClass.JOB_CONFIGURATION_ERROR,
                f"repository does not exist or is not a directory: {repository}",
            )
        return self.engine.transition(
            run.run_id,
            WorkflowState.BASELINE_VALIDATION,
            reason="job and repository validated",
        )

    def _phase_hash(self, run: RunRecord, state: WorkflowState, extra: str = "") -> str:
        diff = ""
        if state not in {
            WorkflowState.CREATED,
            WorkflowState.PLAN_VALIDATION,
            WorkflowState.BASELINE_VALIDATION,
        }:
            diff = self.commands.diff_sha256(repository=Path(run.job.repository))
        raw = f"{run.job.configuration_id}:{state.value}:{diff}:{extra}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _command_phase(
        self,
        run: RunRecord,
        *,
        command: str,
        next_state: WorkflowState,
        reason: str,
    ) -> RunRecord:
        input_hash = self._phase_hash(run, run.state, command)
        cached = self.store.get_phase_result(run.run_id, run.state, input_hash)
        if cached is not None:
            self.store.append_event(
                run.run_id,
                EventType.PHASE_REUSED,
                actor="engine",
                reason=f"reused {run.state} for unchanged input",
                payload={"input_hash": input_hash},
                now=self.clock.now(),
            )
            return self.engine.transition(run.run_id, next_state, reason=reason)
        result = self.gates.run_test(run.job, command)
        failure = self.gates.classify_command(result)
        if failure is not None:
            return self.engine.fail(run.run_id, failure.error_class, failure.reason)
        self.store.save_phase_result(run.run_id, run.state, input_hash, result.to_dict(), completed_at=self.clock.now())
        return self.engine.transition(run.run_id, next_state, reason=reason)

    def _baseline(self, run: RunRecord) -> RunRecord:
        return self._command_phase(
            run,
            command=run.job.test_commands.baseline,
            next_state=WorkflowState.IMPLEMENTATION,
            reason="baseline validation passed",
        )

    def _implementation(self, run: RunRecord) -> RunRecord:
        result = self.codex.execute(
            task=run.job.objective,
            repository=Path(run.job.repository),
            context=self._agent_context(run),
            output_contract=IMPLEMENTATION_CONTRACT,
        )
        return self._handle_agent_result(run, self.codex.name, result, WorkflowState.SCOPE_VALIDATION)

    def _scope(self, run: RunRecord) -> RunRecord:
        failure = self.gates.validate_scope(run.job)
        if failure is not None:
            return self.engine.fail(run.run_id, failure.error_class, failure.reason)
        return self.engine.transition(run.run_id, WorkflowState.FAST_TESTS, reason="changed paths are authorized")

    def _fast_tests(self, run: RunRecord) -> RunRecord:
        return self._command_phase(
            run,
            command=run.job.test_commands.fast,
            next_state=WorkflowState.AGY_AUDIT,
            reason="fast tests passed",
        )

    def _agy_audit(self, run: RunRecord) -> RunRecord:
        diff_sha = self.commands.diff_sha256(repository=Path(run.job.repository))
        cached = self.store.get_audit_cache(run.run_id, diff_sha, self.agy.name)
        if cached is not None:
            report = AuditReport.from_dict(cached)
        else:
            result = self.agy.execute(
                task="Adversarially audit the implementation",
                repository=Path(run.job.repository),
                context=self._agent_context(run),
                output_contract=AUDIT_CONTRACT,
            )
            handled = self._handle_retry_only(run, self.agy.name, result)
            if handled is not None:
                return handled
            try:
                report = parse_audit_output(result.output)
            except AdapterError as exc:
                return self.engine.fail(run.run_id, ErrorClass.MECHANICAL_OUTPUT_ERROR, str(exc))
            self.store.put_audit_cache(run.run_id, diff_sha, self.agy.name, report.to_dict())
        if not report.passed:
            return self.engine.transition(
                run.run_id,
                WorkflowState.CORRECTION,
                reason="AGY found blocking issues: " + report.summary,
            )
        return self.engine.transition(run.run_id, WorkflowState.CLAUDE_REVIEW, reason="AGY audit passed")

    def _claude_review(self, run: RunRecord) -> RunRecord:
        result = self.claude_reviewer.execute(
            task="Independently review the implementation and AGY findings",
            repository=Path(run.job.repository),
            context=self._agent_context(run),
            output_contract=REVIEW_CONTRACT,
        )
        handled = self._handle_retry_only(run, self.claude_reviewer.name, result)
        if handled is not None:
            return handled
        try:
            report = parse_review_output(result.output)
        except AdapterError as exc:
            return self.engine.fail(run.run_id, ErrorClass.REVIEW_FORMAT_ERROR, str(exc))
        return self._apply_review(run, report)

    def _apply_review(self, run: RunRecord, report: ReviewReport) -> RunRecord:
        if report.decision is ReviewDecision.APPROVE:
            return self.engine.transition(run.run_id, WorkflowState.FINAL_TESTS, reason=report.summary)
        if report.decision is ReviewDecision.HUMAN_DECISION:
            return self.engine.transition(
                run.run_id,
                WorkflowState.PAUSED_HUMAN_DECISION,
                reason=report.summary,
            )
        if run.correction_rounds >= run.job.review.max_correction_rounds:
            return self.engine.fail(
                run.run_id,
                ErrorClass.SCIENTIFIC_DECISION_REQUIRED,
                "maximum correction rounds reached: " + report.summary,
            )
        return self.engine.transition(run.run_id, WorkflowState.CORRECTION, reason=report.summary)

    def _correction(self, run: RunRecord) -> RunRecord:
        self.store.increment_correction_rounds(run.run_id)
        result = self.codex.execute(
            task="Correct all blocking AGY and Claude review findings",
            repository=Path(run.job.repository),
            context=self._agent_context(run),
            output_contract=IMPLEMENTATION_CONTRACT,
        )
        return self._handle_agent_result(run, self.codex.name, result, WorkflowState.SCOPE_VALIDATION)

    def _final_tests(self, run: RunRecord) -> RunRecord:
        return self._command_phase(
            run,
            command=run.job.test_commands.final,
            next_state=WorkflowState.READY_FOR_HUMAN_REVIEW,
            reason="strict final gates passed",
        )

    def _handle_agent_result(
        self,
        run: RunRecord,
        owner: str,
        result: ExecutionResult,
        success_state: WorkflowState,
    ) -> RunRecord:
        handled = self._handle_retry_only(run, owner, result)
        if handled is not None:
            return handled
        self.store.clear_retry(run.run_id, owner)
        return self.engine.transition(run.run_id, success_state, reason=result.summary, actor=owner)

    def _handle_retry_only(self, run: RunRecord, owner: str, result: ExecutionResult) -> RunRecord | None:
        if result.status is ResultStatus.SUCCESS:
            self.store.clear_retry(run.run_id, owner)
            return None
        if result.status is ResultStatus.SECURITY:
            return self.engine.fail(run.run_id, ErrorClass.SECURITY_VIOLATION, result.summary)
        retry_class = {
            ResultStatus.QUOTA: ErrorClass.RETRYABLE_QUOTA,
            ResultStatus.TIMEOUT: ErrorClass.RETRYABLE_TIMEOUT,
            ResultStatus.ERROR: ErrorClass.RETRYABLE_AGENT_ERROR,
        }.get(result.status)
        if retry_class is None:
            return self.engine.fail(run.run_id, ErrorClass.MECHANICAL_OUTPUT_ERROR, result.summary)
        previous = self.store.get_retry(run.run_id, owner)
        scheduler = RetryScheduler(run.job.retry_policy, self.clock)
        try:
            retry = scheduler.schedule(
                owner=owner,
                previous_count=0 if previous is None else previous.count,
                error_class=retry_class,
                reason=result.summary,
                run_id=str(run.run_id),
                explicit_reset_at=result.quota_reset_at,
            )
        except RuntimeError as exc:
            return self.engine.fail(run.run_id, retry_class, str(exc))
        self.store.set_retry(run.run_id, retry, now=self.clock.now())
        self.store.append_event(
            run.run_id,
            EventType.RETRY_SCHEDULED,
            actor="supervisor",
            reason=result.summary,
            payload=retry.to_dict(),
            now=self.clock.now(),
        )
        return self.engine.transition(
            run.run_id,
            WorkflowState.PAUSED_QUOTA,
            reason=f"retry scheduled for {owner} at {retry.next_attempt_at}",
            actor="supervisor",
        )

    def _agent_context(self, run: RunRecord) -> dict[str, object]:
        changed = self.commands.changed_files(repository=Path(run.job.repository))
        context: dict[str, object] = {
            "run_id": str(run.run_id),
            "state": run.state.value,
            "objective": run.job.objective,
            "allowed_paths": [rule.to_dict() for rule in run.job.allowed_paths],
            "scientific_invariants": list(run.job.scientific_invariants),
            "gates": run.job.gates.to_dict(),
            "changed_files": list(changed),
            "diff_sha256": self.commands.diff_sha256(repository=Path(run.job.repository)),
            "events": self.store.list_events(run.run_id)[-30:],
        }
        encoded = json.dumps(context, sort_keys=True).encode()
        if len(encoded) > run.job.gates.max_context_bytes:
            context["events"] = context["events"][-5:]  # type: ignore[index]
            context["context_compacted"] = True
        if len(json.dumps(context, sort_keys=True).encode()) > run.job.gates.max_context_bytes:
            raise RuntimeError("bounded agent context still exceeds max_context_bytes")
        return context

    @staticmethod
    def _owner_for_state(state: WorkflowState) -> str:
        if state in {WorkflowState.IMPLEMENTATION, WorkflowState.CORRECTION}:
            return "codex"
        if state is WorkflowState.AGY_AUDIT:
            return "agy"
        if state is WorkflowState.CLAUDE_REVIEW:
            return "claude-reviewer"
        return state.value.lower()
