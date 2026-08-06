"""Google Drive storage backend with read-only lookup and resumable transfers (M04)."""

from __future__ import annotations

import hashlib
import io
import random
import tempfile
import threading
import time
from _thread import LockType
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from dev_autopilot.storage.backend import validate_storage_key

_READ_BLOCK = 4 * 1024 * 1024
_FOLDER_MIME = "application/vnd.google-apps.folder"
T = TypeVar("T")


class DriveStorageBackend:
    """Map storage keys onto a Drive folder tree rooted at ``root_folder_id``.

    Read operations never create folders.  Uploads use the Drive resumable
    protocol and retry individual chunks.  The object-level manifest means a
    Colab restart resumes at the first missing/corrupt part; an interruption
    within one upload is handled by the active resumable session.
    """

    def __init__(
        self,
        root_folder_id: str,
        credentials: Any,
        *,
        service: Any | None = None,
        retry_attempts: int = 5,
        backoff_base_seconds: float = 0.25,
        backoff_cap_seconds: float = 8.0,
        jitter_fraction: float = 0.2,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
        request_factory: Callable[[], Any] | None = None,
        credential_lock: LockType | None = None,
    ) -> None:
        if not root_folder_id.strip():
            raise ValueError("root_folder_id must not be empty")
        if retry_attempts <= 0:
            raise ValueError("retry_attempts must be positive")
        if backoff_base_seconds < 0 or backoff_cap_seconds < 0:
            raise ValueError("backoff values must be non-negative")
        if not 0 <= jitter_fraction <= 1:
            raise ValueError("jitter_fraction must be between 0 and 1")
        self._credentials = credentials
        self._injected_service = service is not None
        self._retry_attempts = retry_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_cap_seconds = backoff_cap_seconds
        self._jitter_fraction = jitter_fraction
        self._sleeper = sleeper
        self._random_source = random_source
        self._request_factory = request_factory
        self._credential_lock = credential_lock or threading.Lock()
        if service is None:
            try:
                from googleapiclient.discovery import build
                from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("DriveStorageBackend requires the 'drive' extra: pip install 'dev-autopilot[drive]'") from exc
            self._MediaFileUpload = MediaFileUpload
            self._MediaIoBaseDownload = MediaIoBaseDownload
            self._MediaIoBaseUpload = MediaIoBaseUpload
            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        else:
            # Dependency-free injection point used by deterministic Drive API
            # contract tests. Upload methods require the real Google media
            # classes and fail clearly if called through a read-only fake.
            self._MediaFileUpload = getattr(service, "MediaFileUpload", None)
            self._MediaIoBaseDownload = getattr(service, "MediaIoBaseDownload", None)
            self._MediaIoBaseUpload = getattr(service, "MediaIoBaseUpload", None)
            self._service = service
        self.root_folder_id = root_folder_id
        self._folder_cache: dict[str, str] = {"": root_folder_id}
        self._file_cache: dict[str, str] = {}

    def fork(self) -> DriveStorageBackend:
        """Create a separate Google API client for a heartbeat thread."""
        if self._injected_service:
            return self
        return DriveStorageBackend(
            self.root_folder_id,
            self._credentials,
            retry_attempts=self._retry_attempts,
            backoff_base_seconds=self._backoff_base_seconds,
            backoff_cap_seconds=self._backoff_cap_seconds,
            jitter_fraction=self._jitter_fraction,
            sleeper=self._sleeper,
            random_source=self._random_source,
            request_factory=self._request_factory,
            credential_lock=self._credential_lock,
        )

    def _refresh_credentials_if_needed(self) -> None:
        credentials = self._credentials
        if credentials is None or not bool(getattr(credentials, "expired", False)):
            return
        # Main execution and the heartbeat backend can share one credential
        # object. Serialize refresh and re-check inside the lock so expiry does
        # not trigger concurrent refresh requests in long Colab runs.
        with self._credential_lock:
            if not bool(getattr(credentials, "expired", False)):
                return
            refresh = getattr(credentials, "refresh", None)
            if not callable(refresh):
                raise RuntimeError("Drive credentials expired and cannot be refreshed")
            if self._request_factory is not None:
                request = self._request_factory()
            else:
                try:
                    from google.auth.transport.requests import Request
                except ImportError as exc:  # pragma: no cover - drive extra missing
                    raise RuntimeError("google-auth is required to refresh Drive credentials") from exc
                request = Request()
            refresh(request)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return True
        response = getattr(exc, "resp", None)
        status = getattr(response, "status", None)
        return status in {408, 409, 429, 500, 502, 503, 504}

    def _retry(self, operation: str, function: Callable[[], T]) -> T:
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                self._refresh_credentials_if_needed()
                return function()
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= self._retry_attempts or not self._is_retryable(exc):
                    raise
                base = min(
                    self._backoff_cap_seconds,
                    self._backoff_base_seconds * (2**attempt),
                )
                jitter = 1 + ((2 * self._random_source()) - 1) * self._jitter_fraction
                self._sleeper(max(0.0, base * jitter))
        raise RuntimeError(f"Drive operation failed without an exception: {operation}") from last_error

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _find_children(self, parent_id: str, *, name: str | None = None) -> list[dict[str, Any]]:
        clauses = [f"'{self._escape_query(parent_id)}' in parents", "trashed=false"]
        if name is not None:
            clauses.append(f"name='{self._escape_query(name)}'")
        query = " and ".join(clauses)

        def list_all() -> list[dict[str, Any]]:
            token: str | None = None
            results: list[dict[str, Any]] = []
            while True:
                response = (
                    self._service.files()
                    .list(
                        q=query,
                        fields="nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime,version)",
                        pageSize=1000,
                        pageToken=token,
                    )
                    .execute()
                )
                results.extend(response.get("files", []))
                token = response.get("nextPageToken")
                if not token:
                    return results

        return self._retry("list Drive children", list_all)

    def _resolve_folder_id(self, folder_path: str, *, create: bool) -> str | None:
        if folder_path in self._folder_cache:
            return self._folder_cache[folder_path]
        current_id = self.root_folder_id
        current_path = ""
        for part in (item for item in folder_path.split("/") if item):
            current_path = f"{current_path}/{part}".strip("/")
            cached = self._folder_cache.get(current_path)
            if cached is not None:
                current_id = cached
                continue
            matches = [item for item in self._find_children(current_id, name=part) if item["mimeType"] == _FOLDER_MIME]
            if matches:
                current_id = str(sorted(matches, key=lambda item: item["id"])[0]["id"])
            elif create:
                parent_id = current_id

                def ensure_folder(parent_id: str = parent_id, part: str = part) -> str:
                    # Re-list on every logical retry. If Drive committed a
                    # previous create but the response was lost, this finds the
                    # existing folder instead of creating a duplicate.
                    existing = [item for item in self._find_children(parent_id, name=part) if item["mimeType"] == _FOLDER_MIME]
                    if existing:
                        return str(sorted(existing, key=lambda item: item["id"])[0]["id"])
                    created = (
                        self._service.files()
                        .create(
                            body={"name": part, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
                            fields="id",
                        )
                        .execute()
                    )
                    return str(created["id"])

                current_id = self._retry(f"ensure Drive folder {current_path}", ensure_folder)
            else:
                return None
            self._folder_cache[current_path] = current_id
        return current_id

    def _resolve_file_id(self, key: str) -> str | None:
        validate_storage_key(key)
        cached = self._file_cache.get(key)
        if cached is not None:
            return cached
        folder_path, _, name = key.rpartition("/")
        parent = self._resolve_folder_id(folder_path, create=False)
        if parent is None:
            return None
        matches = [item for item in self._find_children(parent, name=name) if item["mimeType"] != _FOLDER_MIME]
        if not matches:
            return None
        file_id = str(sorted(matches, key=lambda item: item["id"])[0]["id"])
        self._file_cache[key] = file_id
        return file_id

    def _drive_upload(self, request: Any) -> dict[str, Any]:
        response: dict[str, Any] | None = None
        while response is None:
            self._refresh_credentials_if_needed()
            _, response = request.next_chunk(num_retries=5)
        return response

    def put_bytes(self, key: str, data: bytes) -> str:
        validate_storage_key(key)
        digest = hashlib.sha256(data).hexdigest()
        folder_path, _, name = key.rpartition("/")
        parent = self._resolve_folder_id(folder_path, create=True)
        assert parent is not None
        if self._MediaIoBaseUpload is None:
            raise RuntimeError("Drive upload media classes are unavailable in the injected service")

        def put_logical_object() -> str:
            # Resolve by the stable logical key on each retry. A create that was
            # committed server-side before a lost response is discovered here
            # and converted into an idempotent update rather than duplicated.
            self._file_cache.pop(key, None)
            existing = self._resolve_file_id(key)
            media = self._MediaIoBaseUpload(
                io.BytesIO(data),
                mimetype="application/octet-stream",
                chunksize=_READ_BLOCK,
                resumable=True,
            )
            if existing is None:
                request = self._service.files().create(
                    body={
                        "name": name,
                        "parents": [parent],
                        "appProperties": {"devAutopilotKey": key},
                    },
                    media_body=media,
                    fields="id",
                )
            else:
                request = self._service.files().update(fileId=existing, media_body=media, fields="id")
            response = self._drive_upload(request)
            file_id = str(response.get("id") or existing or "")
            if not file_id:
                raise OSError(f"Drive upload returned no file ID for {key}")
            self._file_cache[key] = file_id
            return digest

        return self._retry(f"put Drive object {key}", put_logical_object)

    def put_file(self, key: str, source: Path | str) -> str:
        validate_storage_key(key)
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        digest = hashlib.sha256()
        with source_path.open("rb") as handle:
            for block in iter(lambda: handle.read(_READ_BLOCK), b""):
                digest.update(block)
        expected_digest = digest.hexdigest()
        folder_path, _, name = key.rpartition("/")
        parent = self._resolve_folder_id(folder_path, create=True)
        assert parent is not None
        if self._MediaFileUpload is None:
            raise RuntimeError("Drive upload media classes are unavailable in the injected service")

        def put_logical_object() -> str:
            self._file_cache.pop(key, None)
            existing = self._resolve_file_id(key)
            media = self._MediaFileUpload(
                str(source_path),
                mimetype="application/octet-stream",
                chunksize=_READ_BLOCK,
                resumable=True,
            )
            if existing is None:
                request = self._service.files().create(
                    body={
                        "name": name,
                        "parents": [parent],
                        "appProperties": {"devAutopilotKey": key},
                    },
                    media_body=media,
                    fields="id",
                )
            else:
                request = self._service.files().update(fileId=existing, media_body=media, fields="id")
            response = self._drive_upload(request)
            file_id = str(response.get("id") or existing or "")
            if not file_id:
                raise OSError(f"Drive upload returned no file ID for {key}")
            self._file_cache[key] = file_id
            return expected_digest

        return self._retry(f"put Drive file {key}", put_logical_object)

    def append_upload(self, key: str, data: bytes, *, offset: int) -> int:
        """Strict append implemented with a bounded temporary file.

        Large scientific payloads should use ``put_file`` or the splitter.  This
        method exists for queue/event records and preserves protocol semantics.
        """
        if offset < 0:
            raise ValueError("offset must be non-negative")
        current = self.size(key) if self.exists(key) else 0
        if current != offset:
            raise ValueError(f"append offset {offset} does not match confirmed length {current}")
        with tempfile.NamedTemporaryFile(prefix="dev-autopilot-drive-append-", delete=True) as temp:
            consumed = 0
            while consumed < current:
                block = self.get_range(key, offset=consumed, length=min(_READ_BLOCK, current - consumed))
                if not block:
                    raise OSError("Drive object ended during append staging")
                temp.write(block)
                consumed += len(block)
            temp.write(data)
            temp.flush()
            self.put_file(key, temp.name)
        return current + len(data)

    def get_range(self, key: str, *, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise ValueError("offset and length must be non-negative")
        if length == 0:
            return b""
        file_id = self._resolve_file_id(key)
        if file_id is None:
            raise FileNotFoundError(key)

        def read_range() -> bytes:
            request = self._service.files().get_media(fileId=file_id)
            request.headers["Range"] = f"bytes={offset}-{offset + length - 1}"
            return bytes(request.execute())

        return self._retry(f"read Drive range {key}", read_range)

    def get_bytes(self, key: str) -> bytes:
        file_id = self._resolve_file_id(key)
        if file_id is None:
            raise FileNotFoundError(key)
        if self._MediaIoBaseDownload is None:
            # Read-only fakes can still provide media bytes via execute().
            return self._retry(
                f"read Drive object {key}",
                lambda: bytes(self._service.files().get_media(fileId=file_id).execute()),
            )
        buffer = io.BytesIO()
        downloader = self._MediaIoBaseDownload(buffer, self._service.files().get_media(fileId=file_id), chunksize=_READ_BLOCK)
        done = False
        while not done:
            self._refresh_credentials_if_needed()
            _, done = downloader.next_chunk(num_retries=5)
        return buffer.getvalue()

    def exists(self, key: str) -> bool:
        return self._resolve_file_id(key) is not None

    def size(self, key: str) -> int:
        file_id = self._resolve_file_id(key)
        if file_id is None:
            raise FileNotFoundError(key)
        meta = self._retry(
            f"read Drive size {key}",
            lambda: self._service.files().get(fileId=file_id, fields="size").execute(),
        )
        return int(meta.get("size", 0))

    def object_identity(self, key: str) -> str:
        file_id = self._resolve_file_id(key)
        if file_id is None:
            raise FileNotFoundError(key)
        meta = self._retry(
            f"read Drive identity {key}",
            lambda: self._service.files().get(fileId=file_id, fields="id,size,md5Checksum,modifiedTime,version").execute(),
        )
        return ":".join(str(meta.get(name, "")) for name in ("id", "version", "size", "md5Checksum", "modifiedTime"))

    def sha256(self, key: str) -> str:
        total = self.size(key)
        digest = hashlib.sha256()
        offset = 0
        while offset < total:
            block = self.get_range(key, offset=offset, length=min(_READ_BLOCK, total - offset))
            if not block:
                raise OSError(f"Drive object {key!r} ended at {offset}, expected {total}")
            digest.update(block)
            offset += len(block)
        return digest.hexdigest()

    def list_prefix(self, prefix: str) -> tuple[str, ...]:
        """Recursively list files whose storage key starts with ``prefix``."""
        clean = prefix.rstrip("/")
        if clean:
            validate_storage_key(clean)
        folder_part, _, name_prefix = clean.rpartition("/")
        # A trailing slash means the complete value is a folder path.
        if prefix.endswith("/"):
            folder_part, name_prefix = clean, ""
        folder_id = self._resolve_folder_id(folder_part, create=False)
        if folder_id is None:
            return ()
        results: list[str] = []

        def walk(parent_id: str, path_prefix: str) -> None:
            for item in sorted(self._find_children(parent_id), key=lambda value: (value["name"], value["id"])):
                name = str(item["name"])
                key = f"{path_prefix}/{name}".strip("/")
                if item["mimeType"] == _FOLDER_MIME:
                    walk(str(item["id"]), key)
                elif key.startswith(prefix):
                    self._file_cache[key] = str(item["id"])
                    results.append(key)

        walk(folder_id, folder_part)
        if name_prefix:
            return tuple(key for key in results if key.startswith(prefix))
        return tuple(results)

    def delete(self, key: str) -> None:
        file_id = self._resolve_file_id(key)
        if file_id is not None:
            self._retry(
                f"delete Drive object {key}",
                lambda: self._service.files().delete(fileId=file_id).execute(),
            )
            self._file_cache.pop(key, None)
