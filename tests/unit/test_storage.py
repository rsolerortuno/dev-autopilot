"""Unit tests for the M04 storage layer: profiles, split, reassemble, resume."""

from __future__ import annotations

import hashlib
import os

import pytest

from dev_autopilot.storage.backend import LocalStorageBackend
from dev_autopilot.storage.manifest import (
    STORAGE_PROFILES,
    ChunkManifest,
    FormatStrategy,
    resolve_part_size,
)
from dev_autopilot.storage.reassembler import (
    ReassemblyError,
    reassemble_from_prefix,
)
from dev_autopilot.storage.splitter import split_file, split_object, verify_parts


def _write(path, data: bytes):
    path.write_bytes(data)
    return path


def test_profiles_present_and_no_24gb_ceiling():
    assert STORAGE_PROFILES["chatgpt_upload"] == 95_000_000
    assert STORAGE_PROFILES["colab_processing"] == 1_073_741_824
    # Explicit sizes far beyond 24 GB are accepted -- there is no artificial cap.
    assert resolve_part_size(part_size_bytes=64 * 1024**3) == 64 * 1024**3


def test_resolve_part_size_rejects_unknown_profile():
    with pytest.raises(ValueError):
        resolve_part_size(profile="does_not_exist")


def test_split_and_reassemble_round_trip(tmp_path):
    data = os.urandom(10_000)
    source = _write(tmp_path / "big.bin", data)
    backend = LocalStorageBackend(tmp_path / "store")
    report = split_file(source, backend, key_prefix="proj/big", part_size_bytes=1024)
    assert report.manifest.total_size == 10_000
    assert len(report.manifest.parts) == 10  # 10 * 1024 covers 10_000 with a short last part
    assert report.manifest.whole_sha256 == hashlib.sha256(data).hexdigest()
    assert verify_parts(backend, report.manifest, key_prefix="proj/big") == []

    out = tmp_path / "restored.bin"
    digest = reassemble_from_prefix(backend, key_prefix="proj/big", destination=out)
    assert out.read_bytes() == data
    assert digest == report.manifest.whole_sha256


def test_split_never_modifies_source(tmp_path):
    data = os.urandom(4096)
    source = _write(tmp_path / "src.bin", data)
    before = source.stat().st_mtime_ns, source.read_bytes()
    backend = LocalStorageBackend(tmp_path / "store")
    split_file(source, backend, key_prefix="p/x", part_size_bytes=1000)
    after = source.stat().st_mtime_ns, source.read_bytes()
    assert before[1] == after[1]  # content unchanged
    assert before[0] == after[0]  # mtime unchanged (opened read-only)


def test_resume_reuses_verified_parts(tmp_path):
    data = os.urandom(5000)
    source = _write(tmp_path / "src.bin", data)
    backend = LocalStorageBackend(tmp_path / "store")
    first = split_file(source, backend, key_prefix="p/y", part_size_bytes=1000)
    assert first.parts_written == 5
    assert first.parts_reused == 0
    # Second pass: everything is present and verified, so nothing is rewritten.
    second = split_file(source, backend, key_prefix="p/y", part_size_bytes=1000)
    assert second.parts_written == 0
    assert second.parts_reused == 5


def test_resume_rewrites_only_corrupted_part(tmp_path):
    data = os.urandom(5000)
    source = _write(tmp_path / "src.bin", data)
    backend = LocalStorageBackend(tmp_path / "store")
    split_file(source, backend, key_prefix="p/z", part_size_bytes=1000)
    # Corrupt one stored part; a resume must detect and rewrite exactly that one.
    backend.put_bytes("p/z/part-000002.bin", b"corrupted")
    report = split_file(source, backend, key_prefix="p/z", part_size_bytes=1000)
    assert report.parts_written == 1
    assert report.parts_reused == 4


def test_reassembly_detects_corrupted_part(tmp_path):
    data = os.urandom(3000)
    source = _write(tmp_path / "src.bin", data)
    backend = LocalStorageBackend(tmp_path / "store")
    report = split_file(source, backend, key_prefix="p/c", part_size_bytes=1000)
    # Tamper with a part's bytes but keep the manifest's expected checksum.
    part0 = report.manifest.parts[0]
    backend.put_bytes(part0.name, b"x" * part0.size)
    with pytest.raises(ReassemblyError):
        reassemble_from_prefix(backend, key_prefix="p/c", destination=tmp_path / "out.bin")


def test_empty_file_round_trip(tmp_path):
    source = _write(tmp_path / "empty.bin", b"")
    backend = LocalStorageBackend(tmp_path / "store")
    report = split_file(source, backend, key_prefix="p/e", part_size_bytes=1000)
    assert report.manifest.total_size == 0
    out = tmp_path / "restored.bin"
    reassemble_from_prefix(backend, key_prefix="p/e", destination=out)
    assert out.read_bytes() == b""


def test_manifest_rejects_non_contiguous_parts():
    with pytest.raises(ValueError):
        ChunkManifest.model_validate(
            {
                "source_name": "x.bin",
                "total_size": 20,
                "whole_sha256": "0" * 64,
                "part_size_bytes": 10,
                "parts": [
                    {"index": 0, "name": "a", "offset": 0, "size": 10, "sha256": "1" * 64},
                    {"index": 1, "name": "b", "offset": 15, "size": 10, "sha256": "2" * 64},
                ],
            }
        )


def test_append_upload_resume_semantics(tmp_path):
    backend = LocalStorageBackend(tmp_path / "store")
    backend.append_upload("p/u/file.bin", b"hello ", offset=0)
    length = backend.append_upload("p/u/file.bin", b"world", offset=6)
    assert length == 11
    assert backend.get_bytes("p/u/file.bin") == b"hello world"


def test_split_object_streams_backend_key_without_full_local_source(tmp_path):
    source_backend = LocalStorageBackend(tmp_path / "source-store")
    destination_backend = LocalStorageBackend(tmp_path / "destination-store")
    data = os.urandom(11_111)
    source_backend.put_bytes("raw/big.bin", data)
    report = split_object(
        source_backend,
        "raw/big.bin",
        destination_backend,
        key_prefix="parts/big",
        part_size_bytes=1024,
    )
    assert report.manifest.whole_sha256 == hashlib.sha256(data).hexdigest()
    output = tmp_path / "remote-restored.bin"
    reassemble_from_prefix(destination_backend, key_prefix="parts/big", destination=output)
    assert output.read_bytes() == data


def test_unimplemented_format_strategy_fails_closed(tmp_path):
    source = _write(tmp_path / "table.parquet", b"not-a-real-parquet")
    backend = LocalStorageBackend(tmp_path / "store")
    with pytest.raises(NotImplementedError, match="byte_range only"):
        split_file(
            source,
            backend,
            key_prefix="p/parquet",
            part_size_bytes=10,
            strategy=FormatStrategy.PARQUET_ROW_GROUP,
        )
