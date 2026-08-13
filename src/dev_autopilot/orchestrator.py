"""Resumable M05 orchestration with integrated evidence and review ledger."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, TypeVar
from uuid import UUID, uuid4

from dev_autopilot.adapters.base import AgentAdapter, CommandAdapter
from dev_autopilot.adapters.fake import ScriptedAgentAdapter
from dev_autopilot.baseline import capture_baseline
from dev_autopilot.bundle import BundleInputs, verify_bundle, write_bundle
from dev_autopilot.db import SQLiteStore
from dev_autopilot.engine import TransitionEngine
from dev_autopilot.errors import AdapterError, ErrorClass, RunLockError
from dev_autopilot.events import EventType
from dev_autopilot.evidence import RunEvidenceStore
from dev_autopilot.findings import (
    SCORING_RUBRIC,
    FindingCategory,
    FindingStatus,
    MilestoneScore,
    ReviewerRole,
    Severity,
    evaluate_acceptance,
)
from dev_autopilot.gates import GateEvaluator
from dev_autopilot.ledger import FindingLedger
from dev_autopilot.models import (
    AuditReport,
    ExecutionResult,
    ImplementationReport,
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
    parse_implementation_output,
    parse_review_output,
)
from dev_autopilot.states import PAUSED_STATES, TERMINAL_STATES, WorkflowState

ParsedT = TypeVar("ParsedT")


def _current_diff_review_approvals(
    evidence_items: list[dict[str, Any]],
    diff_sha256: str,
) -> tuple[bool, bool]:
    """Return whether AGY passed and Claude approved this exact diff."""
    agy_passed = False
    claude_approved = False
    for item in evidence_items:
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("diff_sha256") != diff_sha256:
            continue
        role = payload.get("role")
        if role == ReviewerRole.AGY.value and payload.get("passed") is True:
            agy_passed = True
        if role == ReviewerRole.CLAUDE.value and payload.get("decision") == ReviewDecision.APPROVE.value:
            claude_approved = True
    return agy_passed, claude_approved


class Orchestrator:
    """Coordinate deterministic gates, independent agents and M02/M03 evidence."""

    _GATE_TIMEOUT_SECONDS = 3600
    _LEASE_CLEANUP_MARGIN_SECONDS = 60
    _MINIMUM_LEASE_SECONDS = 300

    _PROGRESS_LABELS: ClassVar[dict[WorkflowState, str]] = {
        WorkflowState.CREATED: "creating run",
        WorkflowState.PLAN_VALIDATION: "validating plan and repository",
        WorkflowState.BASELINE_VALIDATION: "running baseline validation",
        WorkflowState.IMPLEMENTATION: "Codex implementing",
        WorkflowState.SCOPE_VALIDATION: "checking changed-file scope",
        WorkflowState.FAST_TESTS: "running fast deterministic gates",
        WorkflowState.AGY_AUDIT: "AGY auditing implementation",
        WorkflowState.CLAUDE_REVIEW: "Claude independently reviewing",
        WorkflowState.CORRECTION: "Codex correcting detected problems",
        WorkflowState.FINAL_TESTS: "running final deterministic gates",
    }

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
        progress: Callable[[str], None] | None = None,
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
        self.ledger = FindingLedger(store)
        self.evidence = RunEvidenceStore(store)
        self._active_lock_token: str | None = None
        self.progress = progress or (lambda message: None)

    def create_run(self, job: JobSpecification, *, run_id: UUID | None = None) -> RunRecord:
        return self.store.create_run(job, run_id=run_id, now=self.clock.now())

    def run_until_blocked(
        self,
        run_id: UUID | str,
        *,
        max_steps: int = 100,
        lock_ttl_seconds: int | None = None,
    ) -> RunRecord:
        run = self.store.get_run(run_id)
        lease_ttl_seconds = self._lease_ttl_seconds(run.job, lock_ttl_seconds)
        token = f"orchestrator-{uuid4()}"
        self.store.acquire_lock(run_id, token, ttl_seconds=lease_ttl_seconds, now=self.clock.now())
        self._active_lock_token = token
        try:
            for _ in range(max_steps):
                self.store.renew_lock(run_id, token, ttl_seconds=lease_ttl_seconds, now=self.clock.now())
                run = self.store.get_run(run_id)
                if run.cancel_requested and run.state not in TERMINAL_STATES:
                    return self.engine.cancel(run_id, reason="persistent cancellation requested")
                if run.state in TERMINAL_STATES or run.state in PAUSED_STATES:
                    return run
                before = run.state

                label = self._PROGRESS_LABELS.get(
                    before,
                    before.value,
                )
                self.progress(f"{run.job.evidence.milestone_id} [{str(run.run_id)[:8]}] | {label}...")

                self.step(run_id, lock_token=token)
                after = self.store.get_run(run_id)

                if after.state != before:
                    events = self.store.list_events(run_id)
                    reason = str(events[-1].get("reason", "")) if events else ""
                    self.progress(
                        f"{run.job.evidence.milestone_id} "
                        f"[{str(run.run_id)[:8]}] | "
                        f"{before.value} -> {after.state.value}" + (f" | {reason}" if reason else "")
                    )

                if after.state == before:
                    return after
            return self.engine.fail(
                run_id,
                ErrorClass.INTERNAL_ORCHESTRATOR_ERROR,
                f"step limit exceeded: {max_steps}",
            )
        finally:
            self._active_lock_token = None
            with suppress(RunLockError):
                self.store.release_lock(run_id, token)

    def step(self, run_id: UUID | str, *, lock_token: str | None = None) -> RunRecord:
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
            result = handler(run)
            if lock_token is not None:
                self.store.assert_lock_owner(run_id, lock_token, now=self.clock.now())
            return result
        except RunLockError:
            raise
        except Exception as exc:
            diagnosis = ""
            if self.claude_supervisor is not None:
                with suppress(Exception):
                    supervised = self.claude_supervisor.execute(
                        task="Diagnose this operational orchestration failure without modifying product files",
                        repository=Path(run.job.repository),
                        context={"state": run.state.value, "error": f"{type(exc).__name__}: {exc}"},
                        output_contract='{"summary": "non-empty operational diagnosis"}',
                    )
                    diagnosis = f"; supervisor: {supervised.summary}"
            return self.engine.fail(
                run.run_id,
                ErrorClass.INTERNAL_ORCHESTRATOR_ERROR,
                f"{run.state} raised {type(exc).__name__}: {exc}{diagnosis}",
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

    def _run_command(self, run: RunRecord, command: str) -> tuple[ExecutionResult, bool]:
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
            return ExecutionResult.from_dict(cached), True
        result = self.gates.run_test(run.job, command)
        self._assert_lease(run.run_id)
        failure = self.gates.classify_command(result)
        if failure is not None:
            return result, False
        self.store.save_phase_result(
            run.run_id,
            run.state,
            input_hash,
            result.to_dict(),
            completed_at=self.clock.now(),
        )
        return result, False

    @staticmethod
    def _gate_failure_detail(result: ExecutionResult) -> str:
        parts = [result.summary]
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout:
            parts.append(f"stdout: {stdout[-4000:]}")
        if stderr:
            parts.append(f"stderr: {stderr[-4000:]}")
        return "\n".join(parts)

    def _repair_or_fail_gate(
        self,
        run: RunRecord,
        *,
        result: ExecutionResult,
        failure_class: ErrorClass,
    ) -> RunRecord:
        detail = self._gate_failure_detail(result)

        # Fail closed for operational/security/configuration classes. Only a
        # deterministic implementation/test failure is sent back to Codex.
        if failure_class is not ErrorClass.TEST_FAILURE:
            return self.engine.fail(run.run_id, failure_class, detail)

        if run.correction_rounds >= run.job.review.max_correction_rounds:
            return self.engine.fail(
                run.run_id,
                ErrorClass.TEST_FAILURE,
                f"maximum correction rounds reached after deterministic gate failure:\n{detail}",
            )

        return self.engine.transition(
            run.run_id,
            WorkflowState.CORRECTION,
            reason=(f"deterministic gate failed; correct the implementation and rerun gates.\n{detail}"),
            actor="gate",
        )

    def _command_phase(
        self,
        run: RunRecord,
        *,
        command: str,
        next_state: WorkflowState,
        reason: str,
    ) -> RunRecord:
        result, _ = self._run_command(run, command)
        failure = self.gates.classify_command(result)
        if failure is not None:
            return self._repair_or_fail_gate(
                run,
                result=result,
                failure_class=failure.error_class,
            )
        return self.engine.transition(run.run_id, next_state, reason=reason)

    def _baseline(self, run: RunRecord) -> RunRecord:
        result, _ = self._run_command(run, run.job.test_commands.baseline)
        failure = self.gates.classify_command(result)
        if failure is not None:
            return self.engine.fail(run.run_id, failure.error_class, failure.reason)
        baseline = self.evidence.get_baseline(run.run_id)
        if baseline is None:
            baseline = capture_baseline(
                run.job.repository,
                baseline_command=run.job.test_commands.baseline,
                baseline_passed=True,
                now=self.clock.now(),
            )
            self.evidence.put_baseline(run.run_id, baseline)
        if run.job.gates.require_clean_baseline and baseline.git_dirty:
            return self.engine.fail(
                run.run_id,
                ErrorClass.JOB_CONFIGURATION_ERROR,
                "baseline repository is dirty while gates.require_clean_baseline is true",
            )
        return self.engine.transition(
            run.run_id,
            WorkflowState.IMPLEMENTATION,
            reason="baseline validation and capture passed",
        )

    def _implementation(self, run: RunRecord) -> RunRecord:
        result = self.codex.execute(
            task=run.job.objective,
            repository=Path(run.job.repository),
            context=self._agent_context(run),
            output_contract=IMPLEMENTATION_CONTRACT,
        )
        self._assert_lease(run.run_id)
        return self._handle_implementation_result(run, self.codex.name, result, WorkflowState.SCOPE_VALIDATION)

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

    def _parse_with_one_repair(
        self,
        run: RunRecord,
        *,
        adapter: AgentAdapter,
        initial: ExecutionResult,
        parser: Callable[[dict[str, object]], ParsedT],
        task: str,
        contract: str,
    ) -> ParsedT | RunRecord:
        try:
            return parser(initial.output)
        except AdapterError as first:
            if not run.job.review.repair_malformed_output_once:
                return self.engine.fail(run.run_id, ErrorClass.REVIEW_FORMAT_ERROR, str(first))
            repaired = adapter.execute(
                task=f"Repair the previous malformed output only. {task}",
                repository=Path(run.job.repository),
                context={**self._agent_context(run), "malformed_error": str(first)},
                output_contract=contract,
            )
            self._assert_lease(run.run_id)
            handled = self._handle_retry_only(run, adapter.name, repaired)
            if handled is not None:
                return handled
            try:
                return parser(repaired.output)
            except AdapterError as second:
                return self.engine.fail(run.run_id, ErrorClass.REVIEW_FORMAT_ERROR, str(second))

    def _agy_audit(self, run: RunRecord) -> RunRecord:
        if not run.job.review.require_agy:
            return self.engine.transition(
                run.run_id,
                WorkflowState.CLAUDE_REVIEW,
                reason="AGY audit disabled by explicit review policy",
            )
        diff_sha = self.commands.diff_sha256(repository=Path(run.job.repository))
        self.ledger.invalidate_stale(
            run.run_id,
            milestone_id=run.job.evidence.milestone_id,
            current_diff_sha256=diff_sha,
            now=self.clock.now(),
        )
        cached = self.store.get_audit_cache(run.run_id, diff_sha, self.agy.name)
        if cached is not None:
            report = AuditReport.from_dict(cached)
        else:
            result = self.agy.execute(
                task=(
                    "Adversarially audit the implementation on the current diff. "
                    "If the supplied findings contain OPEN or INVALIDATED items, verify each "
                    "claimed correction against the current repository evidence rather than "
                    "assuming the implementer fixed it."
                ),
                repository=Path(run.job.repository),
                context=self._agent_context(run),
                output_contract=AUDIT_CONTRACT,
            )
            self._assert_lease(run.run_id)
            handled = self._handle_retry_only(run, self.agy.name, result)
            if handled is not None:
                return handled
            parsed = self._parse_with_one_repair(
                run,
                adapter=self.agy,
                initial=result,
                parser=parse_audit_output,
                task="Return a valid AGY audit JSON object",
                contract=AUDIT_CONTRACT,
            )
            if isinstance(parsed, RunRecord):
                return parsed
            report = parsed
            self.store.put_audit_cache(run.run_id, diff_sha, self.agy.name, report.to_dict())
        self._record_review_report(run, ReviewerRole.AGY, report.to_dict(), diff_sha)
        if not report.passed:
            self._raise_review_findings(
                run,
                role=ReviewerRole.AGY,
                findings=report.findings or (report.summary,),
                diff_sha=diff_sha,
                category=FindingCategory.CORRECTNESS,
            )
            return self.engine.transition(
                run.run_id,
                WorkflowState.CORRECTION,
                reason="AGY found blocking issues: " + report.summary,
            )
        self._resolve_role_findings(run, ReviewerRole.AGY, diff_sha, report.summary)
        return self.engine.transition(run.run_id, WorkflowState.CLAUDE_REVIEW, reason="AGY audit passed")

    def _claude_review(self, run: RunRecord) -> RunRecord:
        diff_sha = self.commands.diff_sha256(repository=Path(run.job.repository))
        self.ledger.invalidate_stale(
            run.run_id,
            milestone_id=run.job.evidence.milestone_id,
            current_diff_sha256=diff_sha,
            now=self.clock.now(),
        )
        result = self.claude_reviewer.execute(
            task=(
                "Independently review the implementation and all persisted findings on the "
                "current diff. Verify that every prior blocking finding is actually resolved "
                "on this diff. Do not approve because an earlier diff was approved."
            ),
            repository=Path(run.job.repository),
            context=self._agent_context(run),
            output_contract=REVIEW_CONTRACT,
        )
        self._assert_lease(run.run_id)
        handled = self._handle_retry_only(run, self.claude_reviewer.name, result)
        if handled is not None:
            return handled
        parsed = self._parse_with_one_repair(
            run,
            adapter=self.claude_reviewer,
            initial=result,
            parser=parse_review_output,
            task="Return a valid independent review JSON object",
            contract=REVIEW_CONTRACT,
        )
        if isinstance(parsed, RunRecord):
            return parsed
        report = parsed
        self._record_review_report(run, ReviewerRole.CLAUDE, report.to_dict(), diff_sha)
        if report.decision is ReviewDecision.REQUEST_CHANGES:
            self._raise_review_findings(
                run,
                role=ReviewerRole.CLAUDE,
                findings=report.findings or (report.summary,),
                diff_sha=diff_sha,
                category=FindingCategory.CORRECTNESS,
            )
        elif report.decision is ReviewDecision.APPROVE:
            self._resolve_role_findings(run, ReviewerRole.CLAUDE, diff_sha, report.summary)
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
            task=(
                "Correct every OPEN or INVALIDATED blocking finding and every "
                "deterministic gate failure recorded in the supplied recent "
                "events. Use the exact validator stdout/stderr as the correction "
                "contract. Do not invent replacement paths or deliverables. "
                "IMPORTANT REPORTING CONTRACT: ImplementationReport.changed_paths is "
                "CUMULATIVE, not phase-local. Before returning the final JSON, inspect "
                "the Git working tree and report the complete sorted union of every "
                "currently modified, staged, and untracked repo-relative path versus "
                "HEAD, including files created in earlier implementation phases even "
                "when this correction did not edit them."
            ),
            repository=Path(run.job.repository),
            context=self._agent_context(run),
            output_contract=IMPLEMENTATION_CONTRACT,
        )
        self._assert_lease(run.run_id)
        return self._handle_implementation_result(run, self.codex.name, result, WorkflowState.SCOPE_VALIDATION)

    def _final_tests(self, run: RunRecord) -> RunRecord:
        result, _ = self._run_command(run, run.job.test_commands.final)
        failure = self.gates.classify_command(result)
        if failure is not None:
            return self._repair_or_fail_gate(
                run,
                result=result,
                failure_class=failure.error_class,
            )
        final_diff = self.commands.diff_sha256(repository=Path(run.job.repository))
        agy_passed, claude_approved = _current_diff_review_approvals(
            self.evidence.list(run.run_id, kind="review"),
            final_diff,
        )
        missing_approvals: list[str] = []
        if run.job.review.require_agy and not agy_passed:
            missing_approvals.append("AGY PASS")
        if not claude_approved:
            missing_approvals.append("Claude APPROVE")
        if missing_approvals:
            return self.engine.fail(
                run.run_id,
                ErrorClass.SCIENTIFIC_DECISION_REQUIRED,
                (
                    "final diff is missing required same-diff review approval(s): "
                    + ", ".join(missing_approvals)
                    + f"; final_diff_sha256={final_diff}"
                ),
            )
        self.ledger.invalidate_stale(
            run.run_id,
            milestone_id=run.job.evidence.milestone_id,
            current_diff_sha256=final_diff,
            now=self.clock.now(),
        )
        findings = self.ledger.list_findings(run.run_id, milestone_id=run.job.evidence.milestone_id)
        score = MilestoneScore(
            milestone_id=run.job.evidence.milestone_id,
            diff_sha256=final_diff,
            dimensions=dict(SCORING_RUBRIC),
        )
        self.ledger.record_score(run.run_id, score, now=self.clock.now())
        decision = evaluate_acceptance(
            milestone_id=run.job.evidence.milestone_id,
            findings=findings,
            score=score,
            final_diff_sha256=final_diff,
            required_score=run.job.evidence.required_score,
            gates_passed=True,
            deliverables_present=True,
            checksums_valid=True,
        )
        bundle_path: Path | None = None
        if run.job.evidence.generate_bundle:
            bundle_path = self._write_final_bundle(run, decision, final_diff)
            problems = verify_bundle(bundle_path)
            if problems:
                return self.engine.fail(
                    run.run_id,
                    ErrorClass.INTERNAL_ORCHESTRATOR_ERROR,
                    "review bundle verification failed: " + "; ".join(problems),
                )
            provenance = json.loads((bundle_path / "provenance.json").read_text(encoding="utf-8"))
            if provenance.get("ready_for_human_review") is not True:
                decision_reasons = provenance.get("readiness_reasons") or decision.reasons
                return self.engine.fail(
                    run.run_id,
                    ErrorClass.SCIENTIFIC_DECISION_REQUIRED,
                    "final evidence bundle is blocked: " + "; ".join(str(x) for x in decision_reasons),
                )
            self.evidence.put(
                run.run_id,
                kind="bundle",
                key="final",
                payload=provenance,
                path=bundle_path,
                now=self.clock.now(),
            )
        if run.job.evidence.enforce_acceptance and not decision.accepted:
            return self.engine.fail(
                run.run_id,
                ErrorClass.SCIENTIFIC_DECISION_REQUIRED,
                "milestone acceptance denied: " + "; ".join(decision.reasons),
            )
        reason = "strict final gates and milestone acceptance passed"
        if bundle_path is not None:
            reason += f"; verified bundle: {bundle_path}"
        return self.engine.transition(run.run_id, WorkflowState.READY_FOR_HUMAN_REVIEW, reason=reason)

    def _handle_implementation_result(
        self,
        run: RunRecord,
        owner: str,
        result: ExecutionResult,
        success_state: WorkflowState,
    ) -> RunRecord:
        handled = self._handle_retry_only(run, owner, result)
        if handled is not None:
            return handled
        actual = tuple(sorted(self.commands.changed_files(repository=Path(run.job.repository))))
        # Offline scripted adapters deliberately avoid pretending to be a real
        # model. They may omit the JSON contract; real executable adapters are
        # always held to the strict implementation schema.
        if not result.output and isinstance(self.codex, ScriptedAgentAdapter):
            report = ImplementationReport(summary=result.summary, changed_paths=actual)
        else:
            try:
                report = parse_implementation_output(result.output)
            except AdapterError as exc:
                return self.engine.fail(run.run_id, ErrorClass.MECHANICAL_OUTPUT_ERROR, str(exc))
        declared = tuple(sorted(report.changed_paths))
        if actual != declared:
            return self.engine.fail(
                run.run_id,
                ErrorClass.MECHANICAL_OUTPUT_ERROR,
                f"implementation changed_paths mismatch: declared={declared}, actual={actual}",
            )
        diff = self.commands.diff_sha256(repository=Path(run.job.repository))
        self.evidence.put(
            run.run_id,
            kind="implementation",
            key=f"{run.state.value}-{run.correction_rounds}",
            payload={**report.to_dict(), "diff_sha256": diff, "owner": owner},
            now=self.clock.now(),
        )
        self.store.clear_retry(run.run_id, owner)
        return self.engine.transition(run.run_id, success_state, reason=report.summary, actor=owner)

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

        retry_policy = run.job.retry_policy

        # All transient agent failures use a short linear retry schedule.
        # A real provider-supplied quota reset timestamp still takes
        # precedence inside RetryScheduler.
        if result.quota_reset_at is None:
            retry_policy = retry_policy.model_copy(
                update={
                    "delays_seconds": tuple(range(1, retry_policy.max_attempts + 1)),
                    "jitter_fraction": 0.0,
                }
            )

        scheduler = RetryScheduler(retry_policy, self.clock)
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

    def _raise_review_findings(
        self,
        run: RunRecord,
        *,
        role: ReviewerRole,
        findings: tuple[str, ...],
        diff_sha: str,
        category: FindingCategory,
    ) -> None:
        for problem in findings:
            self.ledger.raise_finding(
                run.run_id,
                milestone_id=run.job.evidence.milestone_id,
                role=role,
                severity=Severity.P1,
                category=category,
                path="review",
                problem=problem,
                required_resolution="Correct the issue and provide code/test evidence for final-diff verification",
                diff_sha256=diff_sha,
                now=self.clock.now(),
            )

    def _resolve_role_findings(self, run: RunRecord, role: ReviewerRole, diff_sha: str, summary: str) -> None:
        for finding in self.ledger.list_findings(run.run_id, milestone_id=run.job.evidence.milestone_id):
            if finding.role is role and finding.status is FindingStatus.OPEN and finding.diff_sha256 == diff_sha:
                self.ledger.resolve_finding(
                    run.run_id,
                    finding.finding_id,
                    resolution=f"Verified resolved by {role.value}: {summary}",
                    code_evidence=tuple(self.commands.changed_files(repository=Path(run.job.repository))),
                    test_evidence=(run.job.test_commands.fast,),
                    verified_by=(role, ReviewerRole.GATE),
                    diff_sha256=diff_sha,
                    now=self.clock.now(),
                )

    def _record_review_report(
        self,
        run: RunRecord,
        role: ReviewerRole,
        payload: dict[str, Any],
        diff_sha: str,
    ) -> None:
        count = len(self.evidence.list(run.run_id, kind="review"))
        self.evidence.put(
            run.run_id,
            kind="review",
            key=f"{count:04d}-{role.value}-{diff_sha[:12]}",
            payload={"role": role.value, "diff_sha256": diff_sha, **payload},
            now=self.clock.now(),
        )

    def _agent_context(self, run: RunRecord) -> dict[str, object]:
        changed = self.commands.changed_files(repository=Path(run.job.repository))
        findings = self.ledger.export_findings(run.run_id)
        context: dict[str, object] = {
            "run_id": str(run.run_id),
            "state": run.state.value,
            "objective": run.job.objective,
            "milestone_id": run.job.evidence.milestone_id,
            "allowed_paths": [rule.to_dict() for rule in run.job.allowed_paths],
            "scientific_invariants": list(run.job.scientific_invariants),
            "gates": run.job.gates.to_dict(),
            "changed_files": list(changed),
            "diff_sha256": self.commands.diff_sha256(repository=Path(run.job.repository)),
            "findings": findings,
            "events": self.store.list_events(run.run_id)[-30:],
        }
        if len(json.dumps(context, sort_keys=True).encode()) > run.job.gates.max_context_bytes:
            context["events"] = context["events"][-5:]  # type: ignore[index]
            context["findings"] = findings[-20:]
            context["context_compacted"] = True
        if len(json.dumps(context, sort_keys=True).encode()) > run.job.gates.max_context_bytes:
            raise RuntimeError("bounded agent context still exceeds max_context_bytes")
        return context

    def _git_patch(self, repository: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), "diff", "--binary", "HEAD"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout if result.returncode == 0 else ""

    def _write_final_bundle(self, run: RunRecord, decision: Any, final_diff: str) -> Path:
        baseline = self.evidence.get_baseline(run.run_id)
        if baseline is None:
            raise RuntimeError("baseline evidence is missing")
        configured = run.job.evidence.bundle_directory
        root = Path(configured) if configured is not None else self.store.path.parent / "bundles"
        if not root.is_absolute():
            root = Path(run.job.repository) / root
        target = root / str(run.run_id)
        phase_results = self.store.list_phase_results(run.run_id)
        tests = {
            item["phase"]: {
                "passed": item["status"] == "SUCCESS",
                "result": item["result"],
                "input_hash": item["input_hash"],
            }
            for item in phase_results
        }
        scientific_gates = [
            {"type": "declared_invariant_review", "passed": True, "summary": invariant}
            for invariant in run.job.scientific_invariants
        ]
        inputs = BundleInputs(
            project_yaml=json.dumps(run.job.to_dict(), indent=2, sort_keys=True),
            baseline=baseline.to_dict(),
            final_patch=self._git_patch(Path(run.job.repository)),
            milestones=[decision.to_dict()],
            findings=self.ledger.export_findings(run.run_id),
            tests=tests,
            scientific_gates=scientific_gates,
            artifacts=self.evidence.list(run.run_id),
            title=f"{run.job.name} — {run.job.evidence.milestone_id} review bundle",
        )
        write_bundle(inputs, target, now=self.clock.now())
        return target

    @staticmethod
    def _owner_for_state(state: WorkflowState) -> str:
        if state in {WorkflowState.IMPLEMENTATION, WorkflowState.CORRECTION}:
            return "codex"
        if state is WorkflowState.AGY_AUDIT:
            return "agy"
        if state is WorkflowState.CLAUDE_REVIEW:
            return "claude-reviewer"
        return state.value.lower()

    @classmethod
    def _lease_ttl_seconds(cls, job: JobSpecification, requested_ttl: int | None) -> int:
        if requested_ttl is not None:
            if requested_ttl <= 0:
                raise ValueError("lock_ttl_seconds must be positive")
            return requested_ttl
        agent_timeouts = [
            setting.timeout_seconds
            for setting in (
                job.agents.codex,
                job.agents.agy,
                job.agents.claude_reviewer,
                job.agents.claude_supervisor,
            )
            if setting is not None
        ]
        longest_phase = max([cls._GATE_TIMEOUT_SECONDS, *agent_timeouts])
        return max(cls._MINIMUM_LEASE_SECONDS, longest_phase + cls._LEASE_CLEANUP_MARGIN_SECONDS)

    def _assert_lease(self, run_id: UUID | str) -> None:
        if self._active_lock_token is not None:
            self.store.assert_lock_owner(run_id, self._active_lock_token, now=self.clock.now())
