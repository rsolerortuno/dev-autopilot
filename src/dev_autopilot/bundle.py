"""Integrity-protected human review bundles (M03).

The bundle is the final evidence packet for one run.  A single fail-closed
readiness evaluation drives both the machine-readable provenance and the HTML
verdict.  Every human-facing and machine-facing payload, including
``report.html``, is checksummed.  ``verify_bundle`` recomputes all file digests,
the folded content digest, and the final bundle digest.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BUNDLE_VERSION = "1.1"
_BASE_PAYLOAD_FILES = (
    "project.yaml",
    "baseline.json",
    "final.patch",
    "milestones.json",
    "findings.json",
    "tests.json",
    "scientific_gates.json",
    "artifacts.json",
)
_HASHED_FILES = (*_BASE_PAYLOAD_FILES, "report.html")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _fold(checksums: dict[str, str], names: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(checksums[name].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass
class BundleInputs:
    project_yaml: str
    baseline: dict[str, Any]
    final_patch: str
    milestones: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    tests: dict[str, Any]
    scientific_gates: list[dict[str, Any]]
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    title: str = "Dev Autopilot review bundle"


def _payload_map(inputs: BundleInputs) -> dict[str, bytes]:
    return {
        "project.yaml": inputs.project_yaml.encode("utf-8"),
        "baseline.json": _canonical_json(inputs.baseline),
        "final.patch": inputs.final_patch.encode("utf-8"),
        "milestones.json": _canonical_json(inputs.milestones),
        "findings.json": _canonical_json(inputs.findings),
        "tests.json": _canonical_json(inputs.tests),
        "scientific_gates.json": _canonical_json(inputs.scientific_gates),
        "artifacts.json": _canonical_json(inputs.artifacts),
    }


def _all_test_records(value: Any, prefix: str = "tests") -> list[tuple[str, bool]]:
    """Extract pass/fail leaves from nested test data without optimistic defaults."""
    records: list[tuple[str, bool]] = []
    if isinstance(value, dict):
        if "passed" in value:
            records.append((prefix, value.get("passed") is True))
        else:
            for key, child in value.items():
                records.extend(_all_test_records(child, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            records.extend(_all_test_records(child, f"{prefix}[{index}]"))
    return records


def readiness_reasons(inputs: BundleInputs) -> tuple[str, ...]:
    """Return every reason the bundle is not ready for human release review."""
    reasons: list[str] = []
    if not inputs.milestones:
        reasons.append("no milestone acceptance decision is present")
    for milestone in inputs.milestones:
        identifier = milestone.get("milestone_id", "unknown")
        if milestone.get("accepted") is not True:
            detail = milestone.get("reasons") or "acceptance decision is false"
            reasons.append(f"milestone {identifier} is not accepted: {detail}")
        total = milestone.get("total_score")
        required = milestone.get("required_score")
        if isinstance(total, int) and isinstance(required, int) and total < required:
            reasons.append(f"milestone {identifier} score {total} is below {required}")

    for finding in inputs.findings:
        severity = finding.get("severity")
        status = finding.get("status")
        if severity in ("P0", "P1") and status != "RESOLVED":
            reasons.append(f"blocking finding {finding.get('finding_id', 'unknown')} is {status or 'UNKNOWN'}")

    test_records = _all_test_records(inputs.tests)
    if not test_records:
        reasons.append("no deterministic test result is present")
    reasons.extend(f"deterministic test {name} failed" for name, passed in test_records if not passed)

    for gate in inputs.scientific_gates:
        if gate.get("passed") is not True:
            reasons.append(f"scientific gate {gate.get('type', 'unknown')} failed")
    if any(not (artifact.get("sha256") or artifact.get("digest")) for artifact in inputs.artifacts):
        reasons.append("one or more artifact records lack a SHA-256")
    return tuple(reasons)


def _render_report(inputs: BundleInputs, *, content_sha256: str, generated_at: str, reasons: tuple[str, ...]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value))

    rows = "".join(
        f"<tr><td>{esc(f.get('finding_id'))}</td><td>{esc(f.get('severity'))}</td>"
        f"<td>{esc(f.get('status'))}</td><td>{esc(f.get('category'))}</td>"
        f"<td>{esc(f.get('problem'))}</td></tr>"
        for f in inputs.findings
    )
    gate_rows = "".join(
        f"<tr><td>{esc(g.get('type'))}</td><td>{esc(g.get('passed'))}</td><td>{esc(g.get('summary', ''))}</td></tr>"
        for g in inputs.scientific_gates
    )
    milestone_rows = "".join(
        f"<tr><td>{esc(m.get('milestone_id'))}</td><td>{esc(m.get('accepted'))}</td>"
        f"<td>{esc(m.get('total_score', ''))}/{esc(m.get('required_score', ''))}</td>"
        f"<td>{esc('; '.join(str(x) for x in m.get('reasons', [])))}</td></tr>"
        for m in inputs.milestones
    )
    verdict = "READY FOR HUMAN REVIEW" if not reasons else "BLOCKED"
    reason_items = "".join(f"<li>{esc(reason)}</li>" for reason in reasons)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{esc(inputs.title)}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
 h1 {{ font-size: 1.4rem; }}
 .verdict {{ padding: .6rem 1rem; border-radius: 6px; display: inline-block; font-weight: 600; }}
 .ok {{ background: #e6f4ea; color: #137333; }}
 .blocked {{ background: #fce8e6; color: #c5221f; }}
 table {{ border-collapse: collapse; margin: 1rem 0; width: 100%; }}
 th, td {{ border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; font-size: .9rem; }}
 th {{ background: #f5f5f5; }} code {{ font-size: .8rem; }}
</style></head><body>
<h1>{esc(inputs.title)}</h1>
<p class="verdict {"ok" if not reasons else "blocked"}">{esc(verdict)}</p>
<p>Content digest <code>{esc(content_sha256)}</code>, generated {esc(generated_at)}.</p>
{"<h2>Blocking reasons</h2><ul>" + reason_items + "</ul>" if reasons else ""}
<h2>Milestones</h2>
<table><tr><th>Milestone</th><th>Accepted</th><th>Score</th><th>Reasons</th></tr>
{milestone_rows or '<tr><td colspan="4">none</td></tr>'}</table>
<h2>Findings</h2>
<table><tr><th>ID</th><th>Severity</th><th>Status</th><th>Category</th><th>Problem</th></tr>
{rows or '<tr><td colspan="5">none</td></tr>'}</table>
<h2>Scientific gates</h2>
<table><tr><th>Gate</th><th>Passed</th><th>Summary</th></tr>{gate_rows or '<tr><td colspan="3">none</td></tr>'}</table>
</body></html>"""


def write_bundle(inputs: BundleInputs, destination: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    payloads = _payload_map(inputs)
    base_checksums = {name: _sha256_bytes(payloads[name]) for name in _BASE_PAYLOAD_FILES}
    content_sha256 = _fold(base_checksums, _BASE_PAYLOAD_FILES)
    reasons = readiness_reasons(inputs)
    report = _render_report(
        inputs,
        content_sha256=content_sha256,
        generated_at=timestamp.isoformat(),
        reasons=reasons,
    ).encode("utf-8")
    payloads["report.html"] = report
    checksums = {name: _sha256_bytes(payloads[name]) for name in _HASHED_FILES}
    provenance = {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": timestamp.isoformat(),
        "ready_for_human_review": not reasons,
        "readiness_reasons": list(reasons),
        "content_sha256": content_sha256,
        "checksums": checksums,
        "bundle_sha256": _fold(checksums, _HASHED_FILES),
    }
    for name in _HASHED_FILES:
        (target / name).write_bytes(payloads[name])
    (target / "provenance.json").write_bytes(_canonical_json(provenance))
    return provenance


def verify_bundle(bundle_dir: Path | str) -> list[str]:
    target = Path(bundle_dir)
    provenance_path = target / "provenance.json"
    if not provenance_path.is_file():
        return [f"missing provenance.json in {target}"]
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid provenance.json: {exc}"]
    problems: list[str] = []
    expected_entries = set(_HASHED_FILES) | {"provenance.json"}
    if not target.is_dir():
        return [f"bundle path is not a directory: {target}"]
    for entry in sorted(target.rglob("*")):
        relative = entry.relative_to(target).as_posix()
        if entry.is_symlink():
            problems.append(f"bundle contains unsupported symlink: {relative}")
        elif entry.is_dir():
            problems.append(f"bundle contains unexpected directory: {relative}")
        elif relative not in expected_entries:
            problems.append(f"bundle contains unexpected file: {relative}")
    checksums = provenance.get("checksums")
    if not isinstance(checksums, dict):
        return ["provenance checksums must be an object"]
    actual_checksums: dict[str, str] = {}
    for name in _HASHED_FILES:
        path = target / name
        if not path.is_file():
            problems.append(f"missing payload file: {name}")
            continue
        actual = _sha256_bytes(path.read_bytes())
        actual_checksums[name] = actual
        expected = checksums.get(name)
        if not isinstance(expected, str):
            problems.append(f"provenance has no checksum for {name}")
        elif actual != expected:
            problems.append(f"checksum mismatch for {name}: expected {expected[:12]}…, found {actual[:12]}…")
    if len(actual_checksums) == len(_HASHED_FILES):
        base = {name: actual_checksums[name] for name in _BASE_PAYLOAD_FILES}
        content = _fold(base, _BASE_PAYLOAD_FILES)
        if content != provenance.get("content_sha256"):
            problems.append("content_sha256 mismatch")
        bundle = _fold(actual_checksums, _HASHED_FILES)
        if bundle != provenance.get("bundle_sha256"):
            problems.append("bundle_sha256 mismatch")
    return problems
