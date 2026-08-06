"""Traceable per-milestone review findings with diff-keyed invalidation.

A finding is a single, stably identified observation raised by a reviewer role
against one exact diff.  Findings are the unit that milestone acceptance scores
against: a milestone may only be accepted when no blocking finding is OPEN and
every recorded finding was verified against the *final* diff.

The core safety property is diff binding.  Every finding records the
``diff_sha256`` it was raised against.  When the implementation changes, the
diff hash changes, and any finding still bound to the previous diff is
automatically invalidated (its verification no longer describes the code that
exists).  A stale RESOLVED finding can therefore never silently keep a milestone
green after the code underneath it moved.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator, model_validator

from dev_autopilot.models import ContractModel, NonEmptyString

# Stable finding identifiers look like ``M03-SCI-P002``: milestone, reviewer
# role, and a zero-padded point index.  They are assigned deterministically per
# (milestone, role) so the same observation keeps its id across re-reviews.
_FINDING_ID = re.compile(r"^M\d{2,}-[A-Z]{2,6}-P\d{3,}$")
FindingId = Annotated[str, StringConstraints(pattern=r"^M\d{2,}-[A-Z]{2,6}-P\d{3,}$")]
MilestoneId = Annotated[str, StringConstraints(pattern=r"^M\d{2,}$")]
DiffSha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Severity(StrEnum):
    """Blocking severity.  P0 and P1 block milestone acceptance; P2/P3 do not."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @property
    def blocks_acceptance(self) -> bool:
        return self in (Severity.P0, Severity.P1)


class ReviewerRole(StrEnum):
    """Roles allowed to raise findings.  The token also seeds the finding id."""

    AGY = "AGY"
    CLAUDE = "CLAUDE"
    SCIENTIFIC = "SCI"
    SECURITY = "SEC"
    GATE = "GATE"

    @property
    def id_token(self) -> str:
        return self.value


class FindingCategory(StrEnum):
    CORRECTNESS = "correctness"
    DATA_LEAKAGE = "data_leakage"
    REPRODUCIBILITY = "reproducibility"
    SCOPE = "scope"
    SECURITY = "security"
    NUMERICAL_REGRESSION = "numerical_regression"
    TEST_QUALITY = "test_quality"
    DOCUMENTATION = "documentation"
    PROVENANCE = "provenance"
    OPERATIONAL = "operational"


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    # A finding raised against a diff that has since changed.  Invalidated
    # findings are neither trusted (their code moved) nor silently dropped
    # (they must be re-raised or re-verified against the new diff).
    INVALIDATED = "INVALIDATED"


class Finding(ContractModel):
    """One stably identified review observation bound to one exact diff."""

    finding_id: FindingId
    milestone_id: MilestoneId
    role: ReviewerRole
    severity: Severity
    category: FindingCategory
    blocking: bool
    path: NonEmptyString
    line: Annotated[int, Field(ge=0)] | None = None
    problem: NonEmptyString
    required_resolution: NonEmptyString
    diff_sha256: DiffSha256
    status: FindingStatus = FindingStatus.OPEN
    resolution: str | None = None
    code_evidence: tuple[str, ...] = ()
    test_evidence: tuple[str, ...] = ()
    verified_by: tuple[ReviewerRole, ...] = ()

    @field_validator("code_evidence", "test_evidence", "verified_by", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def check_finding_id_scope(self) -> Finding:
        if not self.finding_id.startswith(f"{self.milestone_id}-{self.role.id_token}-P"):
            raise ValueError(
                f"finding_id {self.finding_id!r} does not match milestone {self.milestone_id} and role {self.role.id_token}"
            )
        if self.status is FindingStatus.RESOLVED:
            if not self.resolution or not self.resolution.strip():
                raise ValueError("a RESOLVED finding requires a non-empty resolution")
            if not self.verified_by:
                raise ValueError("a RESOLVED finding requires at least one verifier")
        return self

    @property
    def is_open_blocker(self) -> bool:
        return self.status is FindingStatus.OPEN and self.severity.blocks_acceptance


def next_finding_id(milestone_id: str, role: ReviewerRole, existing: int) -> str:
    """Return the next deterministic finding id for a (milestone, role) pair."""
    if not re.fullmatch(r"M\d{2,}", milestone_id):
        raise ValueError(f"invalid milestone id: {milestone_id!r}")
    if existing < 0:
        raise ValueError("existing point count must be non-negative")
    candidate = f"{milestone_id}-{role.id_token}-P{existing + 1:03d}"
    if not _FINDING_ID.fullmatch(candidate):
        raise ValueError(f"generated an invalid finding id: {candidate!r}")
    return candidate


# ---------------------------------------------------------------------------
# Milestone scoring
# ---------------------------------------------------------------------------

# The rubric from the product charter.  Dimensions are scored 0..max and a
# milestone normally needs 90/100 in addition to the hard gates below.
SCORING_RUBRIC: dict[str, int] = {
    "functional_correctness": 25,
    "scientific_validity": 20,
    "security_scope": 15,
    "reproducibility": 15,
    "test_quality": 10,
    "operational_resilience": 10,
    "documentation": 5,
}
MAX_SCORE = sum(SCORING_RUBRIC.values())
DEFAULT_ACCEPTANCE_SCORE = 90


class MilestoneScore(ContractModel):
    """A scored rubric for one milestone against one final diff."""

    milestone_id: MilestoneId
    diff_sha256: DiffSha256
    dimensions: dict[str, int]

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls, value: dict[str, int]) -> dict[str, int]:
        missing = set(SCORING_RUBRIC) - set(value)
        if missing:
            raise ValueError(f"missing scoring dimensions: {sorted(missing)}")
        extra = set(value) - set(SCORING_RUBRIC)
        if extra:
            raise ValueError(f"unknown scoring dimensions: {sorted(extra)}")
        for name, points in value.items():
            if points < 0 or points > SCORING_RUBRIC[name]:
                raise ValueError(f"dimension {name} score {points} outside 0..{SCORING_RUBRIC[name]}")
        return value

    @property
    def total(self) -> int:
        return sum(self.dimensions.values())


class AcceptanceDecision(ContractModel):
    """The auditable result of evaluating whether a milestone may be accepted."""

    milestone_id: MilestoneId
    accepted: bool
    total_score: Annotated[int, Field(ge=0)]
    required_score: Annotated[int, Field(ge=0)]
    open_blockers: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @field_validator("open_blockers", "reasons", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


def evaluate_acceptance(
    *,
    milestone_id: str,
    findings: tuple[Finding, ...],
    score: MilestoneScore,
    final_diff_sha256: str,
    required_score: int = DEFAULT_ACCEPTANCE_SCORE,
    gates_passed: bool = True,
    deliverables_present: bool = True,
    checksums_valid: bool = True,
) -> AcceptanceDecision:
    """Decide milestone acceptance under the charter's conjunctive rule.

    A milestone is accepted only when *all* of these hold: the score reaches the
    threshold, no P0/P1 finding is OPEN, every recorded finding is bound to the
    exact final diff, all gates pass, and every deliverable and checksum is
    present.  Any single failure denies acceptance and is reported.
    """
    reasons: list[str] = []

    open_blockers = tuple(f.finding_id for f in findings if f.milestone_id == milestone_id and f.is_open_blocker)
    if open_blockers:
        reasons.append(f"{len(open_blockers)} blocking finding(s) still OPEN")

    # Every finding must be verified against the exact final diff. INVALIDATED
    # findings are deliberately blocking: they represent review conclusions that
    # became stale after the implementation changed and have not yet been
    # superseded by a fresh review of the final diff.
    stale = tuple(
        f.finding_id
        for f in findings
        if f.milestone_id == milestone_id and (f.status is FindingStatus.INVALIDATED or f.diff_sha256 != final_diff_sha256)
    )
    if stale:
        reasons.append(f"{len(stale)} finding(s) require final-diff re-review: {', '.join(stale)}")

    if score.diff_sha256 != final_diff_sha256:
        reasons.append("milestone score was computed against a non-final diff")

    if score.total < required_score:
        reasons.append(f"score {score.total}/{MAX_SCORE} below required {required_score}")

    if not gates_passed:
        reasons.append("deterministic gates did not all pass")
    if not deliverables_present:
        reasons.append("one or more deliverables are missing")
    if not checksums_valid:
        reasons.append("one or more checksums are invalid")

    return AcceptanceDecision(
        milestone_id=milestone_id,
        accepted=not reasons,
        total_score=score.total,
        required_score=required_score,
        open_blockers=open_blockers,
        reasons=tuple(reasons),
    )


def finding_from_dict(value: dict[str, Any]) -> Finding:
    return Finding.from_dict(value)
