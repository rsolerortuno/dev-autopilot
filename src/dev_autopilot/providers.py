"""Budgeted provider adapter wrapper with metadata-only telemetry."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Protocol
from uuid import uuid4

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
    ) -> None:
        self.adapter = adapter
        self.name = provider
        self.provider, self.model, self.budget, self.config, self.sink = provider, model, budget, config, sink

    def execute(self, *, task: str, repository: Path, context: dict[str, object], output_contract: str) -> ExecutionResult:
        call_id = str(uuid4())
        estimated_value = context.get("estimated_micro_usd", 0)
        estimated_micro_usd = estimated_value if isinstance(estimated_value, int) else 0
        actual_micro_usd = context.get("actual_micro_usd")
        actual_micro_usd = actual_micro_usd if isinstance(actual_micro_usd, int) else None
        started = now()
        self.budget.reserve(self.config, call_id=call_id, estimated_micro_usd=estimated_micro_usd)
        try:
            result = self.adapter.execute(task=task, repository=repository, context=context, output_contract=output_contract)
            self.budget.settle(call_id, actual_micro_usd)
            status = getattr(result, "status", "unknown")
            with suppress(Exception):
                emit_trace(
                    self.sink,
                    TraceRecord(call_id, self.provider, self.model, str(status), (now() - started) * 1000, actual_micro_usd),
                )
            return result
        except Exception:
            with suppress(Exception):
                emit_trace(
                    self.sink,
                    TraceRecord(call_id, self.provider, self.model, "error", (now() - started) * 1000, actual_micro_usd),
                )
            raise
