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


@pytest.mark.parametrize("passed", [True, False])
def test_audit_accepts_pass_and_fail_reports(passed: bool) -> None:
    assert parse_audit_output({"passed": passed, "summary": "audited", "findings": ["finding"]}).passed is passed


@pytest.mark.parametrize("decision", ["APPROVE", "REQUEST_CHANGES", "HUMAN_DECISION"])
def test_review_accepts_every_decision(decision: str) -> None:
    assert parse_review_output({"decision": decision, "summary": "reviewed", "findings": []}).decision.value == decision


@pytest.mark.parametrize(
    ("parser", "output", "prefix"),
    [
        (parse_audit_output, {"passed": True}, "invalid AGY"),
        (parse_audit_output, {"passed": True, "summary": "", "findings": []}, "invalid AGY"),
        (parse_audit_output, {"passed": True, "summary": "ok", "findings": "bad"}, "invalid AGY"),
        (parse_audit_output, {"passed": True, "summary": "ok", "findings": [], "extra": 1}, "invalid AGY"),
        (parse_review_output, {"decision": "NO", "summary": "x", "findings": []}, "invalid Claude"),
        (parse_review_output, {"decision": "APPROVE", "findings": []}, "invalid Claude"),
        (parse_review_output, {"decision": "APPROVE", "summary": "", "findings": []}, "invalid Claude"),
        (parse_review_output, {"decision": "APPROVE", "summary": "x", "findings": "bad"}, "invalid Claude"),
        (parse_review_output, {"decision": "APPROVE", "summary": "x", "findings": [], "extra": 1}, "invalid Claude"),
    ],
)
def test_review_parsers_reject_malformed_contracts(parser, output, prefix: str) -> None:
    with pytest.raises(AdapterError, match=f"^{prefix}"):
        parser(output)
