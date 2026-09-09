"""Metadata-only provider traces; prompts, outputs and environments are excluded."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class TraceRecord:
    call_id: str
    provider: str
    model: str | None
    status: str
    latency_ms: float
    usage_micro_usd: int | None


class TraceSink(Protocol):
    def emit(self, record: TraceRecord) -> None: ...


class MemoryTraceSink:
    def __init__(self) -> None:
        self.records: list[TraceRecord] = []

    def emit(self, record: TraceRecord) -> None:
        self.records.append(record)


def emit_trace(sink: TraceSink | None, record: TraceRecord) -> None:
    if sink is not None:
        sink.emit(record)


def now() -> float:
    return time.perf_counter()


def trace_dict(record: TraceRecord) -> dict[str, object]:
    return asdict(record)
