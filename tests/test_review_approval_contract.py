from __future__ import annotations

from dev_autopilot.orchestrator import _current_diff_review_approvals


def _review(role: str, diff: str, **payload: object) -> dict[str, object]:
    return {
        "kind": "review",
        "key": f"{role}-{diff[:8]}",
        "payload": {"role": role, "diff_sha256": diff, **payload},
    }


def test_current_diff_requires_agy_and_claude_on_same_diff() -> None:
    diff = "a" * 64
    evidence = [
        _review("AGY", diff, passed=True),
        _review("CLAUDE", diff, decision="APPROVE"),
    ]
    assert _current_diff_review_approvals(evidence, diff) == (True, True)


def test_stale_agy_approval_does_not_count() -> None:
    current = "b" * 64
    stale = "a" * 64
    evidence = [
        _review("AGY", stale, passed=True),
        _review("CLAUDE", current, decision="APPROVE"),
    ]
    assert _current_diff_review_approvals(evidence, current) == (False, True)


def test_request_changes_is_not_an_approval() -> None:
    diff = "c" * 64
    evidence = [
        _review("AGY", diff, passed=True),
        _review("CLAUDE", diff, decision="REQUEST_CHANGES"),
    ]
    assert _current_diff_review_approvals(evidence, diff) == (True, False)
