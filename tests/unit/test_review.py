from __future__ import annotations

import pytest

from dev_autopilot.errors import AdapterError
from dev_autopilot.models import ReviewDecision
from dev_autopilot.review import parse_audit_output, parse_review_output


def test_strict_review_parsing() -> None:
    report = parse_review_output({"decision": "APPROVE", "summary": "looks good", "findings": []})
    assert report.decision is ReviewDecision.APPROVE
    assert parse_audit_output({"passed": True, "summary": "clean", "findings": []}).passed


def test_malformed_review_fails() -> None:
    with pytest.raises(AdapterError):
        parse_review_output({"decision": "YES", "summary": "x"})
