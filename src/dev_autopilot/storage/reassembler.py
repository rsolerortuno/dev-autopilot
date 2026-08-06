"""Streaming reassembly with per-part and whole-file verification (M04)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from dev_autopilot.storage.backend import StorageBackend
from dev_autopilot.storage.manifest import ChunkManifest
from dev_autopilot.storage.splitter import manifest_key

_READ_BLOCK = 4 * 1024 * 1024


class ReassemblyError(RuntimeError):
    pass


def load_manifest(backend: StorageBackend, *, key_prefix: str) -> ChunkManifest:
    return ChunkManifest.from_json(backend.get_bytes(manifest_key(key_prefix)))


def reassemble(
    backend: StorageBackend,
    manifest: ChunkManifest,
    destination: Path | str,
    *,
    verify: bool = True,
) -> str:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".partial")
    whole = hashlib.sha256()
    try:
        with tmp.open("wb") as out:
            for part in manifest.parts:
                part_hash = hashlib.sha256()
                consumed = 0
                while consumed < part.size:
                    block = backend.get_range(
                        part.name,
                        offset=consumed,
                        length=min(_READ_BLOCK, part.size - consumed),
                    )
                    if not block:
                        raise ReassemblyError(f"part {part.index} ended at {consumed}, expected {part.size}")
                    out.write(block)
                    whole.update(block)
                    part_hash.update(block)
                    consumed += len(block)
                if consumed != part.size:
                    raise ReassemblyError(f"part {part.index} size {consumed} != manifest {part.size}")
                if verify and part_hash.hexdigest() != part.sha256:
                    raise ReassemblyError(f"part {part.index} failed checksum during reassembly")
            out.flush()
            os.fsync(out.fileno())
        digest = whole.hexdigest()
        if verify and digest != manifest.whole_sha256:
            raise ReassemblyError(f"reassembled whole-file checksum {digest[:12]}… != manifest {manifest.whole_sha256[:12]}…")
        os.replace(tmp, target)
        return digest
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def reassemble_from_prefix(
    backend: StorageBackend,
    *,
    key_prefix: str,
    destination: Path | str,
    verify: bool = True,
) -> str:
    return reassemble(backend, load_manifest(backend, key_prefix=key_prefix), destination, verify=verify)
