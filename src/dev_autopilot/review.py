"""Strict audit and review output parsing."""

from __future__ import annotations

import json

from pydantic import ValidationError

from dev_autopilot.errors import AdapterError
from dev_autopilot.models import AuditReport, ReviewReport


def parse_audit_output(output: dict[str, object]) -> AuditReport:
    try:
        return AuditReport.model_validate_json(json.dumps(output))
    except ValidationError as exc:
        raise AdapterError(f"invalid AGY audit output: {exc}") from exc


def parse_review_output(output: dict[str, object]) -> ReviewReport:
    try:
        return ReviewReport.model_validate_json(json.dumps(output))
    except ValidationError as exc:
        raise AdapterError(f"invalid Claude review output: {exc}") from exc


AUDIT_CONTRACT = """Write JSON to DEV_AUTOPILOT_OUTPUT_FILE:
{"passed": boolean, "summary": non-empty string, "findings": [strings]}"""

REVIEW_CONTRACT = """Write JSON to DEV_AUTOPILOT_OUTPUT_FILE:
{"decision": "APPROVE"|"REQUEST_CHANGES"|"HUMAN_DECISION",
 "summary": non-empty string, "findings": [strings]}"""

IMPLEMENTATION_CONTRACT = """Modify only authorized repository paths, run no git
write operations, and write JSON to DEV_AUTOPILOT_OUTPUT_FILE:
{"summary": non-empty string, "changed_paths": [strings]}"""
