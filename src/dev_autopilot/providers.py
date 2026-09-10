"""Budgeted provider adapter wrapper with metadata-only telemetry."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from dev_autopilot.budget import BudgetConfig, BudgetStore
from dev_autopilot.models import ExecutionResult
from dev_autopilot.telemetry import TraceRecord, TraceSink, emit_trace, now


class Agent(Protocol):
    def execute(self, *, task: str, repository: Path, context: dict[str, object], output_contract: str) -> ExecutionResult: ...


class BudgetedAgentAdapter:
    def __init__(
        self,
        adapter: Agent,
        *,
        provider: str,
        model: str | None,
        budget: BudgetStore,
        config: BudgetConfig,
        sink: TraceSink | None = None,
        run_id: str | None = None,
        phase: str | None = None,
        estimated_micro_usd: int = 0,
    ) -> None:
        self.adapter = adapter
        self.name = provider
        self.provider, self.model, self.budget, self.config, self.sink = provider, model, budget, config, sink
        self.run_id, self.phase = run_id, phase
        self.estimated_micro_usd = estimated_micro_usd

    def execute(self, *, task: str, repository: Path, context: dict[str, object], output_contract: str) -> ExecutionResult:
        invocation_id = context.get("invocation_id")
        if not isinstance(invocation_id, str) or not invocation_id:
            raise ValueError("context.invocation_id is required for budgeted execution")
        identity = f"{self.config.project_id}\0{self.config.milestone_id}\0{self.provider}\0{invocation_id}\0{task}"
        call_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        estimated_micro_usd = self.estimated_micro_usd
        started = now()
        self.budget.reserve(self.config, call_id=call_id, estimated_micro_usd=estimated_micro_usd)
        self.budget.claim_execution(call_id)
        try:
            result = self.adapter.execute(task=task, repository=repository, context=context, output_contract=output_contract)
            # Billing is trusted only when supplied by the adapter result, never by context.
            usage = getattr(result, "usage_micro_usd", None)
            actual_micro_usd = usage if isinstance(usage, int) and not isinstance(usage, bool) else None
            self.budget.settle(call_id, actual_micro_usd)
            status = getattr(result, "status", "unknown")
            with suppress(Exception):
                emit_trace(
                    self.sink,
                    TraceRecord(
                        call_id,
                        self.provider,
                        self.model,
                        str(status),
                        (now() - started) * 1000,
                        actual_micro_usd,
                        self.config.project_id,
                        self.config.milestone_id,
                        self.run_id,
                        self.phase,
                    ),
                )
            return result
        except Exception:
            with suppress(Exception):
                emit_trace(
                    self.sink,
                    TraceRecord(
                        call_id,
                        self.provider,
                        self.model,
                        "error",
                        (now() - started) * 1000,
                        None,
                        self.config.project_id,
                        self.config.milestone_id,
                        self.run_id,
                        self.phase,
                    ),
                )
            raise
