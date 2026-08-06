"""Unit tests for the M02 findings ledger and milestone scoring."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dev_autopilot.db import SQLiteStore
from dev_autopilot.findings import (
    DEFAULT_ACCEPTANCE_SCORE,
    MAX_SCORE,
    Finding,
    FindingCategory,
    FindingStatus,
    MilestoneScore,
    ReviewerRole,
    Severity,
    evaluate_acceptance,
    next_finding_id,
)
from dev_autopilot.ledger import FindingLedger
from dev_autopilot.models import JobSpecification

DIFF_A = "a" * 64
DIFF_B = "b" * 64


def _job() -> JobSpecification:
    return JobSpecification.model_validate(
        {
            "name": "job",
            "objective": "do the thing",
            "repository": "/tmp/repo",
            "allowed_paths": [{"kind": "tree", "path": "src"}],
            "test_commands": {"baseline": "true", "fast": "true", "final": "true"},
        }
    )


def _finding(**overrides: object) -> Finding:
    base = {
        "finding_id": "M03-SCI-P001",
        "milestone_id": "M03",
        "role": ReviewerRole.SCIENTIFIC,
        "severity": Severity.P1,
        "category": FindingCategory.DATA_LEAKAGE,
        "blocking": True,
        "path": "src/training.py",
        "line": 184,
        "problem": "Holdout labels influence feature selection",
        "required_resolution": "Fit feature selection inside training folds",
        "diff_sha256": DIFF_A,
    }
    base.update(overrides)
    return Finding.model_validate(base)


def test_finding_id_format_is_enforced() -> None:
    with pytest.raises(ValidationError):
        _finding(finding_id="M03-SCI-2")


def test_finding_id_must_match_milestone_and_role() -> None:
    with pytest.raises(ValidationError):
        _finding(finding_id="M04-SCI-P001")
    with pytest.raises(ValidationError):
        _finding(finding_id="M03-SEC-P001")


def test_next_finding_id_is_deterministic_and_padded() -> None:
    assert next_finding_id("M03", ReviewerRole.SCIENTIFIC, 0) == "M03-SCI-P001"
    assert next_finding_id("M03", ReviewerRole.CLAUDE, 1) == "M03-CLAUDE-P002"


def test_severity_blocking_semantics() -> None:
    assert Severity.P0.blocks_acceptance
    assert Severity.P1.blocks_acceptance
    assert not Severity.P2.blocks_acceptance
    assert not Severity.P3.blocks_acceptance


def test_resolved_requires_resolution_and_verifier() -> None:
    with pytest.raises(ValidationError):
        _finding(status=FindingStatus.RESOLVED)
    resolved = _finding(
        status=FindingStatus.RESOLVED,
        resolution="moved feature selection inside folds",
        verified_by=[ReviewerRole.SCIENTIFIC, ReviewerRole.GATE],
    )
    assert resolved.status is FindingStatus.RESOLVED


def test_ledger_raise_assigns_sequential_ids(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    run = store.create_run(_job())
    ledger = FindingLedger(store)
    first = ledger.raise_finding(
        run.run_id,
        milestone_id="M03",
        role=ReviewerRole.SCIENTIFIC,
        severity=Severity.P1,
        category=FindingCategory.DATA_LEAKAGE,
        path="src/training.py",
        problem="leak",
        required_resolution="fix",
        diff_sha256=DIFF_A,
        line=184,
    )
    second = ledger.raise_finding(
        run.run_id,
        milestone_id="M03",
        role=ReviewerRole.SCIENTIFIC,
        severity=Severity.P2,
        category=FindingCategory.TEST_QUALITY,
        path="tests/test_x.py",
        problem="weak",
        required_resolution="strengthen",
        diff_sha256=DIFF_A,
    )
    assert first.finding_id == "M03-SCI-P001"
    assert second.finding_id == "M03-SCI-P002"
    assert len(ledger.list_findings(run.run_id, milestone_id="M03")) == 2


def test_ledger_resolve_records_evidence(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    run = store.create_run(_job())
    ledger = FindingLedger(store)
    raised = ledger.raise_finding(
        run.run_id,
        milestone_id="M03",
        role=ReviewerRole.SCIENTIFIC,
        severity=Severity.P1,
        category=FindingCategory.DATA_LEAKAGE,
        path="src/training.py",
        problem="leak",
        required_resolution="fix",
        diff_sha256=DIFF_A,
    )
    resolved = ledger.resolve_finding(
        run.run_id,
        raised.finding_id,
        resolution="feature selection moved inside each training fold",
        code_evidence=("src/training.py:172-211",),
        test_evidence=("tests/test_holdout_isolation.py",),
        verified_by=(ReviewerRole.SCIENTIFIC, ReviewerRole.GATE),
        diff_sha256=DIFF_A,
    )
    assert resolved.status is FindingStatus.RESOLVED
    assert not ledger.open_blockers(run.run_id, "M03")


def test_diff_change_invalidates_prior_findings(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    run = store.create_run(_job())
    ledger = FindingLedger(store)
    raised = ledger.raise_finding(
        run.run_id,
        milestone_id="M03",
        role=ReviewerRole.CLAUDE,
        severity=Severity.P1,
        category=FindingCategory.CORRECTNESS,
        path="src/a.py",
        problem="bug",
        required_resolution="fix",
        diff_sha256=DIFF_A,
    )
    ledger.resolve_finding(
        run.run_id,
        raised.finding_id,
        resolution="fixed",
        verified_by=(ReviewerRole.CLAUDE,),
        diff_sha256=DIFF_A,
    )
    # The implementation changed: the resolved finding is now bound to a stale diff.
    invalidated = ledger.invalidate_stale(run.run_id, milestone_id="M03", current_diff_sha256=DIFF_B)
    assert invalidated == (raised.finding_id,)
    assert ledger.get_finding(run.run_id, raised.finding_id).status is FindingStatus.INVALIDATED


def _full_score(diff: str = DIFF_A, **overrides: int) -> MilestoneScore:
    dims = {
        "functional_correctness": 25,
        "scientific_validity": 20,
        "security_scope": 15,
        "reproducibility": 15,
        "test_quality": 10,
        "operational_resilience": 10,
        "documentation": 5,
    }
    dims.update(overrides)
    return MilestoneScore(milestone_id="M03", diff_sha256=diff, dimensions=dims)


def test_score_rejects_out_of_range_dimension() -> None:
    with pytest.raises(ValidationError):
        _full_score(functional_correctness=26)


def test_full_rubric_totals_max() -> None:
    assert _full_score().total == MAX_SCORE == 100


def test_acceptance_denied_on_open_blocker() -> None:
    open_blocker = _finding(diff_sha256=DIFF_A)
    decision = evaluate_acceptance(
        milestone_id="M03",
        findings=(open_blocker,),
        score=_full_score(),
        final_diff_sha256=DIFF_A,
    )
    assert not decision.accepted
    assert open_blocker.finding_id in decision.open_blockers


def test_acceptance_denied_when_score_bound_to_stale_diff() -> None:
    decision = evaluate_acceptance(
        milestone_id="M03",
        findings=(),
        score=_full_score(diff=DIFF_B),
        final_diff_sha256=DIFF_A,
    )
    assert not decision.accepted
    assert any("non-final diff" in reason for reason in decision.reasons)


def test_acceptance_denied_below_threshold() -> None:
    decision = evaluate_acceptance(
        milestone_id="M03",
        findings=(),
        score=_full_score(functional_correctness=0),
        final_diff_sha256=DIFF_A,
    )
    assert not decision.accepted
    assert decision.total_score == 75


def test_acceptance_granted_when_all_conditions_met() -> None:
    resolved = _finding(
        status=FindingStatus.RESOLVED,
        resolution="fixed",
        verified_by=[ReviewerRole.SCIENTIFIC],
        diff_sha256=DIFF_A,
    )
    decision = evaluate_acceptance(
        milestone_id="M03",
        findings=(resolved,),
        score=_full_score(),
        final_diff_sha256=DIFF_A,
        required_score=DEFAULT_ACCEPTANCE_SCORE,
    )
    assert decision.accepted
    assert decision.reasons == ()


def test_invalidated_blocker_denies_acceptance():
    invalidated = _finding(status=FindingStatus.INVALIDATED, diff_sha256=DIFF_A)
    decision = evaluate_acceptance(milestone_id="M03", findings=(invalidated,), score=_full_score(), final_diff_sha256=DIFF_A)
    assert not decision.accepted
    assert any("re-review" in reason for reason in decision.reasons)


def test_ledger_rejects_empty_resolution_and_verifiers(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    run = store.create_run(_job())
    ledger = FindingLedger(store)
    finding = ledger.raise_finding(
        run.run_id,
        milestone_id="M03",
        role=ReviewerRole.SECURITY,
        severity=Severity.P1,
        category=FindingCategory.SECURITY,
        path="src/a.py",
        problem="unsafe",
        required_resolution="fix",
        diff_sha256=DIFF_A,
    )
    with pytest.raises(ValidationError):
        ledger.resolve_finding(run.run_id, finding.finding_id, resolution="", verified_by=(), diff_sha256=DIFF_A)
