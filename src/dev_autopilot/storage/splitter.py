"""Bounded-memory, resumable, checksum-verified splitting (M04)."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dev_autopilot.storage.backend import StorageBackend, validate_storage_key
from dev_autopilot.storage.manifest import ChunkEntry, ChunkManifest, FormatStrategy, resolve_part_size

_READ_BLOCK = 4 * 1024 * 1024


@dataclass(frozen=True)
class SplitReport:
    manifest: ChunkManifest
    parts_written: int
    parts_reused: int


def _part_key(prefix: str, index: int) -> str:
    return f"{prefix}/part-{index:06d}.bin"


def manifest_key(prefix: str) -> str:
    return f"{prefix}/manifest.json"


def _require_byte_range(strategy: FormatStrategy) -> None:
    if strategy is not FormatStrategy.BYTE_RANGE:
        raise NotImplementedError(
            f"format strategy {strategy.value!r} is declared for future adapters; M05 implements byte_range only"
        )


def _publish_part(
    backend: StorageBackend,
    *,
    key: str,
    temp_path: Path,
    size: int,
    digest: str,
) -> bool:
    if backend.exists(key) and backend.size(key) == size and backend.sha256(key) == digest:
        return False
    uploaded = backend.put_file(key, temp_path)
    if uploaded != digest or backend.size(key) != size:
        raise OSError(f"uploaded part verification failed for {key}")
    return True


def split_file(
    source: Path | str,
    backend: StorageBackend,
    *,
    key_prefix: str,
    profile: str | None = None,
    part_size_bytes: int | None = None,
    strategy: FormatStrategy = FormatStrategy.BYTE_RANGE,
) -> SplitReport:
    _require_byte_range(strategy)
    validate_storage_key(key_prefix.rstrip("/"))
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"source file not found: {path}")
    initial = path.stat()
    part_size = resolve_part_size(profile=profile, part_size_bytes=part_size_bytes)
    total_size = initial.st_size
    parts: list[ChunkEntry] = []
    written = 0
    reused = 0
    whole = hashlib.sha256()

    with path.open("rb") as handle:
        index = 0
        offset = 0
        while offset < total_size or (total_size == 0 and index == 0):
            expected = min(part_size, total_size - offset) if total_size else 0
            part_hash = hashlib.sha256()
            actual = 0
            with tempfile.NamedTemporaryFile(prefix="dev-autopilot-part-", delete=True) as temp:
                while actual < expected:
                    block = handle.read(min(_READ_BLOCK, expected - actual))
                    if not block:
                        raise RuntimeError(f"source changed or was truncated while splitting at byte {offset + actual}")
                    temp.write(block)
                    part_hash.update(block)
                    whole.update(block)
                    actual += len(block)
                temp.flush()
                key = _part_key(key_prefix, index)
                digest = part_hash.hexdigest()
                entry = ChunkEntry(index=index, name=key, offset=offset, size=actual, sha256=digest)
                if _publish_part(backend, key=key, temp_path=Path(temp.name), size=actual, digest=digest):
                    written += 1
                else:
                    reused += 1
            parts.append(entry)
            offset += actual
            index += 1
            if total_size == 0:
                break

    final = path.stat()
    if final.st_size != initial.st_size or final.st_mtime_ns != initial.st_mtime_ns:
        raise RuntimeError("source file changed during split; manifest was not published")
    manifest = ChunkManifest(
        source_name=path.name,
        total_size=total_size,
        whole_sha256=whole.hexdigest(),
        part_size_bytes=part_size,
        strategy=strategy,
        parts=tuple(parts),
    )
    backend.put_bytes(manifest_key(key_prefix), manifest.to_json().encode("utf-8"))
    return SplitReport(manifest=manifest, parts_written=written, parts_reused=reused)


def split_object(
    source_backend: StorageBackend,
    source_key: str,
    destination_backend: StorageBackend,
    *,
    key_prefix: str,
    source_name: str | None = None,
    profile: str | None = None,
    part_size_bytes: int | None = None,
    strategy: FormatStrategy = FormatStrategy.BYTE_RANGE,
) -> SplitReport:
    """Split an object by range without materialising the complete source locally."""
    _require_byte_range(strategy)
    validate_storage_key(source_key)
    validate_storage_key(key_prefix.rstrip("/"))
    identity_before = source_backend.object_identity(source_key)
    total_size = source_backend.size(source_key)
    part_size = resolve_part_size(profile=profile, part_size_bytes=part_size_bytes)
    parts: list[ChunkEntry] = []
    whole = hashlib.sha256()
    written = 0
    reused = 0
    index = 0
    offset = 0
    while offset < total_size or (total_size == 0 and index == 0):
        expected = min(part_size, total_size - offset) if total_size else 0
        actual = 0
        digest = hashlib.sha256()
        with tempfile.NamedTemporaryFile(prefix="dev-autopilot-remote-part-", delete=True) as temp:
            while actual < expected:
                block = source_backend.get_range(
                    source_key,
                    offset=offset + actual,
                    length=min(_READ_BLOCK, expected - actual),
                )
                if not block:
                    raise RuntimeError(f"remote source changed or ended at byte {offset + actual}; manifest not published")
                temp.write(block)
                digest.update(block)
                whole.update(block)
                actual += len(block)
            temp.flush()
            part_key = _part_key(key_prefix, index)
            part_digest = digest.hexdigest()
            if _publish_part(
                destination_backend,
                key=part_key,
                temp_path=Path(temp.name),
                size=actual,
                digest=part_digest,
            ):
                written += 1
            else:
                reused += 1
        parts.append(
            ChunkEntry(
                index=index,
                name=part_key,
                offset=offset,
                size=actual,
                sha256=part_digest,
            )
        )
        offset += actual
        index += 1
        if total_size == 0:
            break
    if source_backend.object_identity(source_key) != identity_before:
        raise RuntimeError("remote source identity changed during split; manifest was not published")
    manifest = ChunkManifest(
        source_name=source_name or source_key.rsplit("/", 1)[-1],
        total_size=total_size,
        whole_sha256=whole.hexdigest(),
        part_size_bytes=part_size,
        strategy=strategy,
        parts=tuple(parts),
    )
    destination_backend.put_bytes(manifest_key(key_prefix), manifest.to_json().encode("utf-8"))
    return SplitReport(manifest=manifest, parts_written=written, parts_reused=reused)


def verify_parts(backend: StorageBackend, manifest: ChunkManifest, *, key_prefix: str) -> list[str]:
    del key_prefix
    problems: list[str] = []
    for part in manifest.parts:
        if not backend.exists(part.name):
            problems.append(f"missing part {part.index} ({part.name})")
        elif backend.size(part.name) != part.size:
            problems.append(f"part {part.index} size mismatch")
        elif backend.sha256(part.name) != part.sha256:
            problems.append(f"part {part.index} checksum mismatch")
    return problems
