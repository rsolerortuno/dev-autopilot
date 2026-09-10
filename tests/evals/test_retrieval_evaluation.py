import hashlib
import json
import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/evaluate_retrieval.py"
pytest.importorskip("dev_autopilot.retrieval")


def test_retrieval_evaluation_reports_metrics_and_provenance(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    report = module["evaluate"](tmp_path / "retrieval")
    assert report["provider_evaluation"] is False
    assert report["model_quality_claim"] is False
    assert report["query_count"] == 20
    assert report["fixture_commit"] == report["index_commit"]
    assert report["metrics"] == {"recall_at_5": 1.0, "mrr": 1.0}
    assert len(report["queries"]) == 20
    assert all(len(row["query_sha256"]) == 64 for row in report["queries"])
    expected_hash = hashlib.sha256(json.dumps(module["QUERIES"], sort_keys=True).encode()).hexdigest()
    assert report["queries_sha256"] == expected_hash
    persisted = json.loads((tmp_path / "retrieval" / "retrieval-report.json").read_text())
    assert persisted["metrics"] == report["metrics"]


def test_expected_paths_are_annotated_before_indexing():
    module = runpy.run_path(str(SCRIPT))
    queries = module["QUERIES"]
    assert len(queries) == 20
    assert all(query["expected_paths"] for query in queries)
    assert {query["id"] for query in queries} == {f"Q{i:02d}" for i in range(1, 21)}


def test_real_repository_queries_use_main_files():
    module = runpy.run_path(str(SCRIPT))
    queries = module["REAL_QUERIES"]
    assert len(queries) == 20
    assert all(query["expected_paths"][0].startswith(("src/dev_autopilot/", "docs/")) for query in queries)
    assert {query["id"] for query in queries} == {f"R{i:02d}" for i in range(1, 21)}
