"""Dependency-free Drive API contract tests for M04/M05."""

from __future__ import annotations

import hashlib
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dev_autopilot.storage.drive_backend import DriveStorageBackend
from dev_autopilot.worker.job import Checkpoint, WorkerJob
from dev_autopilot.worker.queue import DriveQueue

FOLDER = "application/vnd.google-apps.folder"


class Request:
    def __init__(self, fn):
        self.fn = fn
        self.headers: dict[str, str] = {}

    def execute(self):
        return self.fn(self.headers)


class UploadRequest:
    def __init__(self, fn):
        self.fn = fn
        self.done = False

    def next_chunk(self, *, num_retries: int = 0):
        del num_retries
        if self.done:
            raise AssertionError("upload request completed more than once")
        self.done = True
        return None, self.fn()


class FakeMediaIoBaseUpload:
    def __init__(self, stream: io.BytesIO, **kwargs: Any) -> None:
        del kwargs
        self.data = stream.getvalue()


class FakeMediaFileUpload:
    def __init__(self, filename: str, **kwargs: Any) -> None:
        del kwargs
        self.data = Path(filename).read_bytes()


class FakeFiles:
    def __init__(self):
        self.items: dict[str, dict[str, Any]] = {
            "root": {"id": "root", "name": "root", "mimeType": FOLDER, "parents": []},
            "f-dev": {"id": "f-dev", "name": "devautopilot", "mimeType": FOLDER, "parents": ["root"]},
            "f-queues": {"id": "f-queues", "name": "queues", "mimeType": FOLDER, "parents": ["f-dev"]},
            "f-cpu": {"id": "f-cpu", "name": "cpu", "mimeType": FOLDER, "parents": ["f-queues"]},
            "j1": {
                "id": "j1",
                "name": "J-1.json",
                "mimeType": "application/json",
                "parents": ["f-cpu"],
                "data": b'{"job_id":"J-1"}',
                "version": "1",
            },
            "j2": {
                "id": "j2",
                "name": "J-2.json",
                "mimeType": "application/json",
                "parents": ["f-cpu"],
                "data": b'{"job_id":"J-2"}',
                "version": "1",
            },
        }
        self.create_calls = 0
        self.next_id = 1

    def _new_id(self) -> str:
        value = f"new-{self.next_id}"
        self.next_id += 1
        return value

    def _metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        data = bytes(item.get("data", b""))
        return {
            "id": item["id"],
            "name": item["name"],
            "mimeType": item["mimeType"],
            "parents": list(item.get("parents", [])),
            "version": str(item.get("version", "1")),
            "size": str(len(data)),
            "md5Checksum": hashlib.md5(data, usedforsecurity=False).hexdigest() if data else "",
            "modifiedTime": "2026-08-06T00:00:00Z",
        }

    def list(self, *, q, fields, pageSize, pageToken=None):
        del fields, pageSize, pageToken
        parent_match = re.search(r"'([^']+)' in parents", q)
        assert parent_match is not None
        parent = parent_match.group(1)
        name_match = re.search(r"name='([^']*)'", q)
        name = None if name_match is None else name_match.group(1)

        def response(headers):
            del headers
            values = []
            for item in self.items.values():
                if parent not in item.get("parents", []):
                    continue
                if name is not None and item["name"] != name:
                    continue
                values.append(self._metadata(item))
            return {"files": values}

        return Request(response)

    def create(self, *, body, fields, media_body=None):
        del fields
        self.create_calls += 1

        def commit():
            file_id = self._new_id()
            mime_type = str(body.get("mimeType", "application/octet-stream"))
            data = b"" if media_body is None else bytes(media_body.data)
            self.items[file_id] = {
                "id": file_id,
                "name": body["name"],
                "mimeType": mime_type,
                "parents": list(body.get("parents", [])),
                "data": data,
                "version": "1",
            }
            return {"id": file_id}

        return Request(lambda headers: commit()) if media_body is None else UploadRequest(commit)

    def update(self, *, fileId, media_body, fields):
        del fields

        def commit():
            item = self.items[fileId]
            item["data"] = bytes(media_body.data)
            item["version"] = str(int(item.get("version", "1")) + 1)
            return {"id": fileId}

        return UploadRequest(commit)

    def get(self, *, fileId, fields):
        del fields
        return Request(lambda headers: self._metadata(self.items[fileId]))

    def get_media(self, *, fileId):
        data = bytes(self.items[fileId].get("data", b""))

        def response(headers):
            range_header = headers.get("Range")
            if not range_header:
                return data
            start, end = map(int, range_header.removeprefix("bytes=").split("-"))
            return data[start : end + 1]

        return Request(response)

    def delete(self, *, fileId):
        return Request(lambda headers: self.items.pop(fileId, None) or {})


class FakeService:
    MediaFileUpload = FakeMediaFileUpload
    MediaIoBaseUpload = FakeMediaIoBaseUpload
    MediaIoBaseDownload = None

    def __init__(self):
        self.files_api = FakeFiles()

    def files(self):
        return self.files_api


def _backend():
    service = FakeService()
    return DriveStorageBackend("root", credentials=None, service=service), service


def test_list_prefix_recursively_lists_queue_files():
    backend, _ = _backend()
    assert backend.list_prefix("devautopilot/queues/cpu/") == (
        "devautopilot/queues/cpu/J-1.json",
        "devautopilot/queues/cpu/J-2.json",
    )


def test_missing_read_does_not_create_folders():
    backend, service = _backend()
    before = service.files_api.create_calls
    assert backend.exists("missing/path/file.json") is False
    assert service.files_api.create_calls == before


def test_drive_sha256_uses_range_reads():
    backend, _ = _backend()
    assert backend.sha256("devautopilot/queues/cpu/J-1.json") == hashlib.sha256(b'{"job_id":"J-1"}').hexdigest()


def test_drive_identity_is_stable_and_metadata_based():
    backend, _ = _backend()
    first = backend.object_identity("devautopilot/queues/cpu/J-1.json")
    second = backend.object_identity("devautopilot/queues/cpu/J-1.json")
    assert first == second
    assert first.startswith("j1:1:")


def test_drive_put_bytes_and_file_update_are_resumable_contracts(tmp_path):
    backend, _ = _backend()
    key = "devautopilot/state/run.json"
    expected = b'{"state":"CREATED"}'
    assert backend.put_bytes(key, expected) == hashlib.sha256(expected).hexdigest()
    assert backend.get_bytes(key) == expected

    source = tmp_path / "run.json"
    source.write_bytes(b'{"state":"READY"}')
    backend.put_file(key, source)
    assert backend.get_bytes(key) == source.read_bytes()


def test_drive_backend_queue_submit_claim_checkpoint_complete():
    backend, _ = _backend()
    queue = DriveQueue(backend, root="queue-test", single_writer=True)
    job = WorkerJob(
        job_id="J-M05",
        project_id="P-M05",
        milestone_id="M05",
        entrypoint=("python", "-c", "print('ok')"),
        output_prefix="projects/P-M05/outputs",
    )
    now = datetime(2026, 8, 6, tzinfo=UTC)
    queue.submit(job, now=now)
    assert queue.list_queued("cpu") == ("queue-test/queues/cpu/J-M05.json",)

    claim = queue.claim("cpu", "worker-1", ttl_seconds=60, now=now)
    assert claim is not None
    queue.checkpoint(claim, Checkpoint(job_id=job.job_id, sequence=1, progress={"rows": 10}), now=now)
    assert queue.load_checkpoint(job.job_id).progress == {"rows": 10}
    queue.complete(claim, result={"ok": True}, now=now)

    status = queue.job_status(job.job_id)
    assert status is not None
    assert status["status"] == "COMPLETED"
    assert status["result"] == {"ok": True}


class FakeCredentials:
    def __init__(self) -> None:
        self.expired = True
        self.refresh_calls = 0

    def refresh(self, request) -> None:
        assert request == "REQUEST"
        self.refresh_calls += 1
        self.expired = False


def test_drive_refreshes_expired_credentials_before_long_session_operations():
    service = FakeService()
    credentials = FakeCredentials()
    backend = DriveStorageBackend(
        "root",
        credentials,
        service=service,
        request_factory=lambda: "REQUEST",
        sleeper=lambda seconds: None,
    )
    assert backend.list_prefix("devautopilot/queues/cpu/")
    assert credentials.refresh_calls == 1


def test_drive_list_retries_transient_failure_with_backoff():
    backend, service = _backend()
    original = service.files_api.list
    calls = {"count": 0}
    sleeps: list[float] = []

    def flaky_list(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return Request(lambda headers: (_ for _ in ()).throw(OSError("temporary list failure")))
        return original(**kwargs)

    service.files_api.list = flaky_list
    backend._sleeper = sleeps.append
    backend._jitter_fraction = 0
    assert backend.list_prefix("devautopilot/queues/cpu/")
    assert calls["count"] >= 2
    assert sleeps == [backend._backoff_base_seconds]


def test_drive_put_bytes_retries_logically_without_duplicate_create(monkeypatch):
    backend, service = _backend()
    backend._sleeper = lambda seconds: None
    backend._jitter_fraction = 0
    original_upload = backend._drive_upload
    calls = {"count": 0}

    def response_lost_after_commit(request):
        calls["count"] += 1
        if calls["count"] == 1:
            _, response = request.next_chunk(num_retries=0)
            assert response is not None
            raise OSError("response lost after server commit")
        return original_upload(request)

    monkeypatch.setattr(backend, "_drive_upload", response_lost_after_commit)
    key = "devautopilot/state/idempotent.json"
    payload = b'{"state":"READY"}'
    backend.put_bytes(key, payload)

    matches = [item for item in service.files_api.items.values() if item.get("name") == "idempotent.json"]
    assert len(matches) == 1
    assert matches[0]["data"] == payload
    assert backend.get_bytes(key) == payload
