"""Unit tests for M03 baseline capture and review-bundle assembly."""

from __future__ import annotations

import json

from dev_autopilot.baseline import capture_baseline, compute_tree_sha256
from dev_autopilot.bundle import BundleInputs, verify_bundle, write_bundle


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    return repo


def test_tree_hash_is_deterministic_and_content_sensitive(tmp_path):
    repo = _make_repo(tmp_path)
    first = compute_tree_sha256(repo)
    second = compute_tree_sha256(repo)
    assert first == second
    (repo / "src" / "a.py").write_text("print('b')\n", encoding="utf-8")
    assert compute_tree_sha256(repo) != first


def test_capture_baseline_produces_stable_id(tmp_path):
    repo = _make_repo(tmp_path)
    baseline = capture_baseline(repo, baseline_command="pytest -q", baseline_passed=True)
    assert baseline.baseline_passed is True
    assert len(baseline.baseline_id) == 64
    # Round-trips through JSON without loss.
    restored = type(baseline).from_json(baseline.to_json())
    assert restored.baseline_id == baseline.baseline_id


def _inputs() -> BundleInputs:
    return BundleInputs(
        project_yaml="project:\n  title: demo\n",
        baseline={"tree_sha256": "0" * 64, "baseline_passed": True},
        final_patch="diff --git a/src/a.py b/src/a.py\n",
        milestones=[{"milestone_id": "M03", "accepted": True, "total_score": 96, "required_score": 90}],
        findings=[
            {
                "finding_id": "M03-SCI-P001",
                "severity": "P1",
                "status": "RESOLVED",
                "category": "data_leakage",
                "problem": "leak",
            }
        ],
        tests={"final": {"passed": True}},
        scientific_gates=[{"type": "holdout_isolation", "passed": True, "summary": "ok"}],
        artifacts=[{"name": "model.pkl", "sha256": "1" * 64}],
    )


def test_write_bundle_creates_full_structure(tmp_path):
    provenance = write_bundle(_inputs(), tmp_path / "bundle")
    expected = {
        "project.yaml",
        "baseline.json",
        "final.patch",
        "milestones.json",
        "findings.json",
        "tests.json",
        "scientific_gates.json",
        "artifacts.json",
        "provenance.json",
        "report.html",
    }
    present = {p.name for p in (tmp_path / "bundle").iterdir()}
    assert expected <= present
    assert len(provenance["bundle_sha256"]) == 64
    assert set(provenance["checksums"]) == expected - {"provenance.json"}


def test_verify_bundle_passes_for_untouched_bundle(tmp_path):
    write_bundle(_inputs(), tmp_path / "bundle")
    assert verify_bundle(tmp_path / "bundle") == []


def test_verify_bundle_detects_tampering(tmp_path):
    write_bundle(_inputs(), tmp_path / "bundle")
    findings_path = tmp_path / "bundle" / "findings.json"
    data = json.loads(findings_path.read_text())
    data[0]["status"] = "OPEN"  # silently reopen a finding after the fact
    findings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    problems = verify_bundle(tmp_path / "bundle")
    assert any("findings.json" in problem for problem in problems)


def test_report_html_flags_open_blocker(tmp_path):
    inputs = _inputs()
    inputs.findings[0]["status"] = "OPEN"
    write_bundle(inputs, tmp_path / "bundle")
    report = (tmp_path / "bundle" / "report.html").read_text()
    assert "BLOCKED" in report


def test_report_html_is_integrity_protected(tmp_path):
    write_bundle(_inputs(), tmp_path / "bundle")
    report = tmp_path / "bundle" / "report.html"
    report.write_text("tampered", encoding="utf-8")
    assert any("report.html" in problem for problem in verify_bundle(tmp_path / "bundle"))


def test_report_blocks_failed_milestone_test_and_scientific_gate(tmp_path):
    inputs = _inputs()
    inputs.milestones[0]["accepted"] = False
    inputs.tests["final"]["passed"] = False
    inputs.scientific_gates[0]["passed"] = False
    provenance = write_bundle(inputs, tmp_path / "bundle")
    report = (tmp_path / "bundle" / "report.html").read_text(encoding="utf-8")
    assert provenance["ready_for_human_review"] is False
    assert "BLOCKED" in report
    assert "READY FOR HUMAN REVIEW" not in report


def test_verify_bundle_rejects_unexpected_file(tmp_path):
    write_bundle(_inputs(), tmp_path / "bundle")
    (tmp_path / "bundle" / "EXTRA_REPORT.html").write_text("looks official", encoding="utf-8")
    problems = verify_bundle(tmp_path / "bundle")
    assert "bundle contains unexpected file: EXTRA_REPORT.html" in problems


def test_verify_bundle_rejects_unexpected_directory_and_symlink(tmp_path):
    write_bundle(_inputs(), tmp_path / "bundle")
    extra_dir = tmp_path / "bundle" / "extra"
    extra_dir.mkdir()
    problems = verify_bundle(tmp_path / "bundle")
    assert "bundle contains unexpected directory: extra" in problems

    extra_dir.rmdir()
    link = tmp_path / "bundle" / "official-report.html"
    try:
        link.symlink_to(tmp_path / "bundle" / "report.html")
    except (OSError, NotImplementedError):
        return
    problems = verify_bundle(tmp_path / "bundle")
    assert "bundle contains unsupported symlink: official-report.html" in problems
