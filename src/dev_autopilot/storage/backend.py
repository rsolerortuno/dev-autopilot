"""Storage backend contract and local implementation (M04)."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Protocol, runtime_checkable

_READ_BLOCK = 4 * 1024 * 1024
_REPLACE_ATTEMPTS = 4


def _is_windows_runtime() -> bool:
    return os.name == "nt"


@runtime_checkable
class StorageBackend(Protocol):
    """Range-readable object storage with idempotent, streaming file upload."""

    def fork(self) -> StorageBackend:
        """Return an independent client session for concurrent heartbeat I/O."""
        ...

    def put_bytes(self, key: str, data: bytes) -> str: ...

    def put_file(self, key: str, source: Path | str) -> str:
        """Upload a local file without loading it fully into memory."""
        ...

    def append_upload(self, key: str, data: bytes, *, offset: int) -> int: ...

    def object_identity(self, key: str) -> str:
        """Return a stable identity that changes when the object is replaced."""
        ...

    def get_range(self, key: str, *, offset: int, length: int) -> bytes: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def size(self, key: str) -> int: ...

    def sha256(self, key: str) -> str: ...

    def list_prefix(self, prefix: str) -> tuple[str, ...]: ...

    def delete(self, key: str) -> None: ...


def validate_storage_key(key: str) -> str:
    if not key or key.startswith("/") or "\\" in key or "\x00" in key:
        raise ValueError(f"invalid storage key: {key!r}")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class LocalStorageBackend:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def fork(self) -> LocalStorageBackend:
        return LocalStorageBackend(self.root)

    def _path(self, key: str) -> Path:
        path = (self.root / validate_storage_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError(f"storage key escapes root: {key!r}")
        return path

    @staticmethod
    def _replace_with_retry(source: Path, destination: Path) -> None:
        """Atomically replace a path, tolerating brief Windows sharing locks."""
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if not _is_windows_runtime() or attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.01 * (attempt + 1))

    def put_bytes(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
        try:
            tmp.write_bytes(data)
            self._replace_with_retry(tmp, path)
        finally:
            with suppress(OSError):
                tmp.unlink()
        return hashlib.sha256(data).hexdigest()

    def put_file(self, key: str, source: Path | str) -> str:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
        digest = hashlib.sha256()
        try:
            with source_path.open("rb") as src, tmp.open("wb") as dst:
                for block in iter(lambda: src.read(_READ_BLOCK), b""):
                    dst.write(block)
                    digest.update(block)
                dst.flush()
                os.fsync(dst.fileno())
            self._replace_with_retry(tmp, path)
        finally:
            with suppress(OSError):
                tmp.unlink()
        return digest.hexdigest()

    def object_identity(self, key: str) -> str:
        path = self._path(key)
        stat = path.stat()
        return f"local:{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"

    def append_upload(self, key: str, data: bytes, *, offset: int) -> int:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.stat().st_size if path.exists() else 0
        if offset != current:
            raise ValueError(f"append offset {offset} does not match confirmed length {current}")
        with path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return offset + len(data)

    def get_range(self, key: str, *, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise ValueError("offset and length must be non-negative")
        with self._path(key).open("rb") as handle:
            handle.seek(offset)
            return handle.read(length)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def sha256(self, key: str) -> str:
        digest = hashlib.sha256()
        with self._path(key).open("rb") as handle:
            for block in iter(lambda: handle.read(_READ_BLOCK), b""):
                digest.update(block)
        return digest.hexdigest()

    def list_prefix(self, prefix: str) -> tuple[str, ...]:
        if prefix:
            validate_storage_key(prefix.rstrip("/"))
        results: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.name.endswith(".partial"):
                continue
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(prefix):
                results.append(rel)
        return tuple(results)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def put_json(self, key: str, value: object) -> str:
        data = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        return self.put_bytes(key, data)

    def get_json(self, key: str) -> object:
        return json.loads(self.get_bytes(key).decode("utf-8"))

    def move(self, source_key: str, dest_key: str) -> None:
        src = self._path(source_key)
        dst = self._path(dest_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
