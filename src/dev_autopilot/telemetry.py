"""Metadata-only provider traces; prompts, outputs and environments are excluded."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TraceRecord:
    call_id: str
    provider: str
    model: str | None
    status: str
    latency_ms: float
    usage_micro_usd: int | None
    project_id: str | None = None
    milestone_id: str | None = None
    run_id: str | None = None
    phase: str | None = None


class TraceSink(Protocol):
    def emit(self, record: TraceRecord) -> None: ...


class MemoryTraceSink:
    def __init__(self) -> None:
        self.records: list[TraceRecord] = []

    def emit(self, record: TraceRecord) -> None:
        self.records.append(record)


class JsonlTraceSink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def emit(self, record: TraceRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(record), sort_keys=True) + "\n")


class OpenTelemetryTraceSink:
    """Optional OTel span sink; importing the optional dependency is deferred."""

    def __init__(self, tracer: object | None = None) -> None:
        if tracer is None:
            from opentelemetry import trace

            tracer = trace.get_tracer("dev_autopilot")
        self.tracer = tracer

    def emit(self, record: TraceRecord) -> None:
        span = self.tracer.start_span(
            "dev_autopilot.provider",
            attributes={
                "provider": record.provider,
                "model": record.model or "",
                "status": record.status,
                "call_id": record.call_id,
                "project_id": record.project_id or "",
                "milestone_id": record.milestone_id or "",
                "run_id": record.run_id or "",
                "phase": record.phase or "",
                "latency_ms": record.latency_ms,
                "usage_micro_usd": record.usage_micro_usd if record.usage_micro_usd is not None else -1,
            },
        )
        span.end()


def emit_trace(sink: TraceSink | None, record: TraceRecord) -> None:
    if sink is not None:
        sink.emit(record)


def now() -> float:
    return time.perf_counter()


def trace_dict(record: TraceRecord) -> dict[str, object]:
    return asdict(record)
