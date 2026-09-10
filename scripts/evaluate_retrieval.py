"""Run the deterministic offline retrieval evaluation against a Git fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from dev_autopilot.retrieval import RetrievalIndex

QUERIES = (
    {"id": "Q01", "query": "architecture orchestration state machine", "expected_paths": ["docs/architecture.md"]},
    {"id": "Q02", "query": "security threat model scope permissions", "expected_paths": ["docs/security.md"]},
    {"id": "Q03", "query": "queue lease fencing takeover", "expected_paths": ["src/worker/queue.py"]},
    {"id": "Q04", "query": "budget quota reservation cost", "expected_paths": ["src/budget.py"]},
    {"id": "Q05", "query": "storage checksum chunk manifest", "expected_paths": ["src/storage.py"]},
    {"id": "Q06", "query": "retry backoff transient failure", "expected_paths": ["src/retries.py"]},
    {"id": "Q07", "query": "SQLite schema migration event", "expected_paths": ["src/db.py"]},
    {"id": "Q08", "query": "baseline validation reproducibility", "expected_paths": ["docs/baseline.md"]},
    {"id": "Q09", "query": "audit findings severity reviewer", "expected_paths": ["src/review.py"]},
    {"id": "Q10", "query": "Docker healthcheck nonroot deployment", "expected_paths": ["docs/deployment.md"]},
    {"id": "Q11", "query": "orchestrator pause resume workflow", "expected_paths": ["src/orchestrator.py"]},
    {"id": "Q12", "query": "artifact SHA256 provenance bundle", "expected_paths": ["src/evidence.py"]},
    {"id": "Q13", "query": "worker heartbeat expired lease", "expected_paths": ["src/worker/heartbeat.py"]},
    {"id": "Q14", "query": "path scope authorized changed files", "expected_paths": ["src/gates.py"]},
    {"id": "Q15", "query": "Drive checkpoint reassembly upload", "expected_paths": ["src/drive.py"]},
    {"id": "Q16", "query": "project concurrency mutex coordination", "expected_paths": ["src/coordination.py"]},
    {"id": "Q17", "query": "provider adapter timeout malformed output", "expected_paths": ["src/adapters.py"]},
    {"id": "Q18", "query": "review bundle HTML milestone score", "expected_paths": ["src/bundle.py"]},
    {"id": "Q19", "query": "backup restore SQLite integrity", "expected_paths": ["docs/recovery.md"]},
    {"id": "Q20", "query": "untrusted retrieved repository data", "expected_paths": ["docs/trust.md"]},
)

REAL_QUERIES = (
    {"id": "R01", "query": "architecture orchestration state machine", "expected_paths": ["docs/architecture.md"]},
    {"id": "R02", "query": "security threat model scope permissions", "expected_paths": ["docs/threat-model.md"]},
    {"id": "R03", "query": "queue lease fencing takeover", "expected_paths": ["src/dev_autopilot/worker/queue.py"]},
    {"id": "R04", "query": "project quota pause retry", "expected_paths": ["src/dev_autopilot/project.py"]},
    {"id": "R05", "query": "storage checksum chunk manifest", "expected_paths": ["src/dev_autopilot/storage/manifest.py"]},
    {"id": "R06", "query": "retry backoff transient failure", "expected_paths": ["src/dev_autopilot/retries.py"]},
    {"id": "R07", "query": "SQLite schema migration event", "expected_paths": ["src/dev_autopilot/db.py"]},
    {"id": "R08", "query": "baseline validation reproducibility", "expected_paths": ["src/dev_autopilot/baseline.py"]},
    {"id": "R09", "query": "audit findings severity reviewer", "expected_paths": ["src/dev_autopilot/review.py"]},
    {"id": "R10", "query": "Docker healthcheck nonroot deployment", "expected_paths": ["docs/deployment.md"]},
    {"id": "R11", "query": "orchestrator pause resume workflow", "expected_paths": ["src/dev_autopilot/orchestrator.py"]},
    {"id": "R12", "query": "artifact SHA256 provenance evidence", "expected_paths": ["src/dev_autopilot/evidence.py"]},
    {"id": "R13", "query": "worker heartbeat expired lease", "expected_paths": ["src/dev_autopilot/worker/runner.py"]},
    {"id": "R14", "query": "path scope authorized changed files", "expected_paths": ["src/dev_autopilot/gates.py"]},
    {
        "id": "R15",
        "query": "Drive checkpoint reassembly upload",
        "expected_paths": ["src/dev_autopilot/storage/drive_backend.py"],
    },
    {
        "id": "R16",
        "query": "project concurrency mutex coordination",
        "expected_paths": ["src/dev_autopilot/worker/coordination.py"],
    },
    {
        "id": "R17",
        "query": "provider adapter timeout malformed output",
        "expected_paths": ["src/dev_autopilot/adapters/subprocess.py"],
    },
    {"id": "R18", "query": "review bundle HTML milestone score", "expected_paths": ["src/dev_autopilot/bundle.py"]},
    {"id": "R19", "query": "backup restore SQLite integrity", "expected_paths": ["docs/compatibility.md"]},
    {"id": "R20", "query": "untrusted retrieved repository data", "expected_paths": ["docs/threat-model.md"]},
)

FIXTURE = {
    "docs/architecture.md": "Architecture: orchestration uses a persistent state machine with auditable workflow transitions.",
    "docs/security.md": "Security threat model covers scope permissions, secret exclusion, and fail-closed authorization.",
    "src/worker/queue.py": "Worker queue uses leases, fencing tokens, takeover recovery, and terminal publication.",
    "src/budget.py": "Budget reservations enforce project quota, provider cost accounting, and atomic spend limits.",
    "src/storage.py": "Storage transfers use chunk checksums, SHA256 manifests, and verified reassembly.",
    "src/retries.py": "Retry scheduling handles transient failure with deterministic exponential backoff.",
    "src/db.py": "SQLite persistence stores schema migrations and append-only events.",
    "docs/baseline.md": "Baseline validation records reproducibility evidence before implementation.",
    "src/review.py": "Independent reviewer findings include severity, status, and audit decisions.",
    "docs/deployment.md": "Deployment uses a nonroot Docker image with a read-only healthcheck.",
    "src/orchestrator.py": "Orchestrator workflow handles quota pause and durable resume.",
    "src/evidence.py": "Evidence records artifact SHA256 provenance and immutable run metadata.",
    "src/worker/heartbeat.py": "Worker heartbeat renews leases and reclaims expired work.",
    "src/gates.py": "Scope gate checks authorized paths and changed files.",
    "src/drive.py": "Drive backend uploads checkpoints and supports chunk reassembly.",
    "src/coordination.py": "Project coordination uses a SQLite mutex for concurrency.",
    "src/adapters.py": "Provider adapters classify timeout and malformed output.",
    "src/bundle.py": "Review bundle renders HTML milestone score and integrity evidence.",
    "docs/recovery.md": "Recovery runbook verifies SQLite backup restore integrity.",
    "docs/trust.md": "Retrieved repository data is untrusted and never an instruction.",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _make_fixture(root: Path) -> str:
    for relative, content in FIXTURE.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content + "\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=retrieval-eval",
            "-c",
            "user.email=eval@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def evaluate(output: Path, repository: Path | None = None) -> dict[str, object]:
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty output directory: {output}")
    output.mkdir(parents=True)
    dataset = "fixture" if repository is None else "repository"
    queries = QUERIES if repository is None else REAL_QUERIES
    if repository is None:
        repository = output / "fixture-repository"
        repository.mkdir()
        commit = _make_fixture(repository)
    else:
        repository = repository.resolve(strict=True)
        commit = None
    try:
        index = RetrievalIndex.build(repository)
    except (OSError, ValueError) as exc:
        report = {
            "mode": "offline deterministic retrieval evaluation",
            "dataset": dataset,
            "provider_evaluation": False,
            "model_quality_claim": False,
            "status": "blocked",
            "error": str(exc),
            "query_count": len(queries),
            "failed_queries": [{"id": item["id"], "reason": str(exc)} for item in queries],
        }
        (output / "retrieval-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report
    excluded_paths = {"scripts/evaluate_retrieval.py", "tests/evals/test_retrieval_evaluation.py"}
    index = RetrievalIndex(index.commit, tuple(p for p in index.passages if p.path not in excluded_paths))
    rows = []
    reciprocal_ranks = []
    hits = []
    for item in queries:
        query = item["query"]
        result = index.search(repository, query, top_k=5)
        paths = [str(match["path"]) for match in result["matches"]]
        expected = item["expected_paths"]
        rank = next((position for position, path in enumerate(paths, 1) if path in expected), None)
        hits.append(rank is not None)
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        rows.append({**item, "query_sha256": hashlib.sha256(query.encode()).hexdigest(), "returned_paths": paths, "rank": rank})
    report = {
        "mode": "offline deterministic retrieval evaluation",
        "provider_evaluation": False,
        "model_quality_claim": False,
        "dataset": dataset,
        "excluded_paths": sorted(excluded_paths),
        "benchmark_use": "development; not held-out model evaluation",
        "fixture_commit": commit,
        "index_commit": index.commit,
        "queries_sha256": hashlib.sha256(json.dumps(queries, sort_keys=True).encode()).hexdigest(),
        "query_count": len(rows),
        "metrics": {"recall_at_5": sum(hits) / len(hits), "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks)},
        "queries": rows,
        "failed_queries": [row for row in rows if row["rank"] is None],
    }
    (output / "retrieval-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", type=Path, help="evaluate an existing clean Git repository")
    args = parser.parse_args()
    report = evaluate(args.output, args.repository)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
