from __future__ import annotations

import subprocess
from pathlib import Path

import dev_autopilot.retrieval as retrieval
from dev_autopilot.retrieval import RetrievalIndex


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_build_reads_many_files_from_immutable_git_snapshot(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    for index in range(120):
        (tmp_path / f"file-{index}.py").write_text(f"def item_{index}():\n    return {index}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    index = RetrievalIndex.build(tmp_path)
    assert len(index.passages) == 120
    assert index.search(tmp_path, "item_42")["matches"][0]["path"] == "file-42.py"


def test_oversized_blob_is_rejected_before_content_batch(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "large.py").write_bytes(b"x" * 300_000)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    monkeypatch.setattr(retrieval, "_batch_blobs", lambda *args: (_ for _ in ()).throw(AssertionError("content read")))
    index = RetrievalIndex.build(tmp_path)
    assert index.passages == ()


def test_total_budget_rejected_before_content_batch(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-q")
    for index in range(2):
        (tmp_path / f"file-{index}.py").write_bytes(bytes([120 + index]) * 100)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    monkeypatch.setattr(retrieval, "_batch_blobs", lambda *args: (_ for _ in ()).throw(AssertionError("content read")))
    try:
        RetrievalIndex.build(tmp_path, max_total_bytes=150)
    except ValueError as error:
        assert "budget" in str(error)
    else:
        raise AssertionError("expected total budget failure")


def test_total_budget_counts_duplicate_paths_before_deduplicated_read(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    content = b"x" * 100
    (tmp_path / "file-a.py").write_bytes(content)
    (tmp_path / "file-b.py").write_bytes(content)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    try:
        RetrievalIndex.build(tmp_path, max_total_bytes=150)
    except ValueError as error:
        assert "budget" in str(error)
    else:
        raise AssertionError("expected duplicate path budget failure")
