"""Adapter contracts for agents, commands and repository inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from dev_autopilot.models import ExecutionResult


class AgentAdapter(Protocol):
    name: str

    def execute(
        self,
        *,
        task: str,
        repository: Path,
        context: dict[str, object],
        output_contract: str,
    ) -> ExecutionResult: ...


class CommandAdapter(Protocol):
    def run(
        self,
        command: str | tuple[str, ...],
        *,
        repository: Path,
        timeout_seconds: int,
        allow_shell: bool = False,
    ) -> ExecutionResult: ...

    def changed_files(self, *, repository: Path) -> tuple[str, ...]: ...

    def diff_sha256(self, *, repository: Path) -> str: ...
