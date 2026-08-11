"""Strict implementation, audit and review output parsing."""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import ValidationError

from dev_autopilot.errors import AdapterError
from dev_autopilot.models import AuditReport, ContractModel, ImplementationReport, ReviewReport

ReportT = TypeVar("ReportT", bound=ContractModel)


def _parse(model: type[ReportT], output: dict[str, object], label: str) -> ReportT:
    try:
        return model.model_validate_json(json.dumps(output))
    except ValidationError as exc:
        raise AdapterError(f"invalid {label} output: {exc}") from exc


def parse_implementation_output(output: dict[str, object]) -> ImplementationReport:
    return _parse(ImplementationReport, output, "implementation")


def parse_audit_output(output: dict[str, object]) -> AuditReport:
    return _parse(AuditReport, output, "AGY audit")


def parse_review_output(output: dict[str, object]) -> ReviewReport:
    return _parse(ReviewReport, output, "Claude review")


AUDIT_CONTRACT = """Write JSON to DEV_AUTOPILOT_OUTPUT_FILE:
{"passed": boolean, "summary": non-empty string, "findings": [strings]}"""

REVIEW_CONTRACT = """Write JSON to DEV_AUTOPILOT_OUTPUT_FILE:
{"decision": "APPROVE"|"REQUEST_CHANGES"|"HUMAN_DECISION",
 "summary": non-empty string, "findings": [strings]}"""

IMPLEMENTATION_CONTRACT = """Modify only authorized repository paths, run no git
write operations, and write JSON to DEV_AUTOPILOT_OUTPUT_FILE:
{"summary": non-empty string, "changed_paths": [repo-relative strings],
 "tests_run": [strings], "assumptions": [strings], "unresolved_questions": [strings]}
IMPORTANT: changed_paths is cumulative for the current working tree, not phase-local.
It must list the complete sorted set of every modified, staged, and untracked
repo-relative path versus HEAD, including paths created in earlier correction rounds."""
