"""Durable Drive-compatible queue with leases, fencing and recovery (M05)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar, cast
from uuid import uuid4

from dev_autopilot.storage.backend import LocalStorageBackend
from dev_autopilot.worker.coordination import SQLiteCoordinator
from dev_autopilot.worker.job import (
    Blocker,
    Checkpoint,
    JobClaim,
    JobStatus,
    ResourceClass,
    WorkerJob,
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class QueueError(RuntimeError):
    pass


class LostLeaseError(QueueError):
    pass


P = ParamSpec("P")
R = TypeVar("R")


def _coordinated(method: Callable[Concatenate[DriveQueue, P], R]) -> Callable[Concatenate[DriveQueue, P], R]:
    def wrapped(self: DriveQueue, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.coordinator.lock():
            return method(self, *args, **kwargs)

    wrapped.__name__ = method.__name__
    wrapped.__doc__ = method.__doc__
    return wrapped


class DriveQueue:
    def __init__(
        self,
        backend: Any,
        *,
        root: str = "devautopilot",
        coordinator: SQLiteCoordinator | None = None,
        single_writer: bool = False,
    ) -> None:
        self.backend = backend
        self.root = root.rstrip("/")
        if coordinator is None and isinstance(backend, LocalStorageBackend):
            coordinator = SQLiteCoordinator(backend.root / ".queue-coordination.sqlite3")
        if coordinator is None and not isinstance(backend, LocalStorageBackend) and not single_writer:
            raise QueueError("non-local queue requires a shared SQLite coordinator or explicit single_writer=True")
        self.coordinator = coordinator or SQLiteCoordinator(Path(".dev-autopilot-queue-coordination.sqlite3"))

    def fork(self) -> DriveQueue:
        fork = getattr(self.backend, "fork", None)
        backend = fork() if callable(fork) else self.backend
        return DriveQueue(backend, root=self.root, coordinator=self.coordinator, single_writer=True)

    @staticmethod
    def _resource_class(value: str) -> str:
        return ResourceClass(value).value

    @staticmethod
    def _owner_marker(owner_token: str) -> str:
        if not owner_token.strip():
            raise ValueError("owner_token must not be empty")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", owner_token).strip("._-") or "worker"
        digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()[:12]
        return f"{slug[:48]}-{digest}"

    def _worker_key(self, resource_class: str, owner_token: str) -> str:
        resource = self._resource_class(resource_class)
        return f"workers/{resource}/{self._owner_marker(owner_token)}.READY.json"

    def _worker_alias_key(self, resource_class: str) -> str:
        return f"workers/{self._resource_class(resource_class)}/READY.json"

    @_coordinated
    def register_worker(
        self,
        resource_class: str,
        owner_token: str,
        *,
        ttl_seconds: int = 300,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        timestamp = now or datetime.now(UTC)
        payload = {
            "resource_class": resource_class,
            "owner_token": owner_token,
            "heartbeat_at": _iso(timestamp),
            "expires_at": _iso(timestamp + timedelta(seconds=ttl_seconds)),
            "metadata": metadata or {},
        }
        self._put(self._worker_key(resource_class, owner_token), payload)
        self._put(self._worker_alias_key(resource_class), payload)

    @_coordinated
    def unregister_worker(self, resource_class: str, owner_token: str) -> None:
        self.backend.delete(self._worker_key(resource_class, owner_token))
        alias = self._worker_alias_key(resource_class)
        if self.backend.exists(alias):
            record = self._get(alias)
            if record.get("owner_token") == owner_token:
                self.backend.delete(alias)

    def list_state(self, state: str) -> tuple[str, ...]:
        normalized = state.lower()
        if normalized not in {"running", "completed", "failed", "blocked", "checkpoints"}:
            raise ValueError(f"unknown queue state: {state}")
        return tuple(
            key
            for key in self.backend.list_prefix(f"{self.root}/{normalized}/")
            if key.endswith(".json") and not key.endswith((".lease.json", ".heartbeat.json"))
        )

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        for state in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.BLOCKED):
            key = self._terminal_key(state, job_id)
            if self.backend.exists(key):
                return self._get(key)
        running = self._running_key(job_id)
        if self.backend.exists(running):
            return {**self._get(running), "status": JobStatus.RUNNING.value}
        for resource in ("cpu", "high_ram", "gpu", "tpu", "storage"):
            queued = self._queued_key(resource, job_id)
            if self.backend.exists(queued):
                return {**self._get(queued), "status": JobStatus.QUEUED.value}
        return None

    def _queued_key(self, resource_class: str, job_id: str) -> str:
        return f"{self.root}/queues/{resource_class}/{job_id}.json"

    def _running_key(self, job_id: str) -> str:
        return f"{self.root}/running/{job_id}.json"

    def _lease_key(self, job_id: str) -> str:
        return f"{self.root}/running/{job_id}.lease.json"

    def _heartbeat_key(self, job_id: str) -> str:
        return f"{self.root}/running/{job_id}.heartbeat.json"

    def _checkpoint_key(self, job_id: str) -> str:
        return f"{self.root}/checkpoints/{job_id}.json"

    def _terminal_key(self, status: JobStatus, job_id: str) -> str:
        folder = {
            JobStatus.COMPLETED: "completed",
            JobStatus.FAILED: "failed",
            JobStatus.BLOCKED: "blocked",
        }[status]
        return f"{self.root}/{folder}/{job_id}.json"

    def _put(self, key: str, value: Any) -> None:
        self.backend.put_bytes(
            key,
            (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )

    def _get(self, key: str) -> dict[str, Any]:
        value = json.loads(self.backend.get_bytes(key).decode("utf-8"))
        if not isinstance(value, dict):
            raise QueueError(f"queue record {key!r} must contain a JSON object")
        return cast(dict[str, Any], value)

    def _job_exists(self, job: WorkerJob) -> bool:
        direct = [
            *(self._queued_key(resource.value, job.job_id) for resource in ResourceClass),
            self._running_key(job.job_id),
            self._terminal_key(JobStatus.COMPLETED, job.job_id),
            self._terminal_key(JobStatus.FAILED, job.job_id),
            self._terminal_key(JobStatus.BLOCKED, job.job_id),
        ]
        return any(self.backend.exists(key) for key in direct)

    @_coordinated
    def submit(self, job: WorkerJob, *, now: datetime | None = None) -> None:
        if self._job_exists(job):
            raise QueueError(f"job already exists: {job.job_id}")
        timestamp = now or datetime.now(UTC)
        self._put(
            self._queued_key(job.resource_class.value, job.job_id),
            {"job": json.loads(job.to_json()), "attempt": 0, "queued_at": _iso(timestamp)},
        )

    def list_queued(self, resource_class: str) -> tuple[str, ...]:
        resource = self._resource_class(resource_class)
        prefix = f"{self.root}/queues/{resource}/"
        return tuple(key for key in self.backend.list_prefix(prefix) if key.endswith(".json"))

    @staticmethod
    def _parse_queued(raw: Any) -> tuple[WorkerJob, int]:
        # Backward-compatible read of the initial v0.2 queue format.
        if isinstance(raw, dict) and "job" in raw:
            return WorkerJob.from_dict(raw["job"]), int(raw.get("attempt", 0))
        return WorkerJob.from_dict(raw), 0

    @_coordinated
    def claim(
        self,
        resource_class: str,
        owner_token: str,
        *,
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> JobClaim | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        timestamp = now or datetime.now(UTC)
        resource_class = self._resource_class(resource_class)
        for key in self.list_queued(resource_class):
            job, attempt = self._parse_queued(self._get(key))
            if any(
                self.backend.exists(self._terminal_key(status, job.job_id))
                for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.BLOCKED)
            ):
                # A stale queue listing must never resurrect a terminal job.
                self.backend.delete(key)
                continue
            # Drive listings are eventually consistent. A queued object may
            # briefly reappear after the first claimant deleted it. Respect a
            # still-live lease before attempting to replace it, otherwise two
            # expensive workers can start the same job even though fencing
            # prevents the stale one from publishing.
            lease_key = self._lease_key(job.job_id)
            if self.backend.exists(lease_key):
                try:
                    existing_lease = self._get(lease_key)
                    if _parse(str(existing_lease["expires_at"])) > timestamp:
                        continue
                except FileNotFoundError:
                    # The lease disappeared between exists() and get(); proceed
                    # as an unleased queued job.
                    pass
                except (KeyError, TypeError, ValueError, OSError):
                    # A malformed or unreadable lease is not safe to overwrite
                    # automatically. The watchdog/operator can repair it.
                    continue
            if attempt >= job.max_attempts:
                self._put(
                    self._terminal_key(JobStatus.FAILED, job.job_id),
                    {
                        "job": json.loads(job.to_json()),
                        "status": JobStatus.FAILED.value,
                        "finished_at": _iso(timestamp),
                        "reason": f"maximum attempts reached: {attempt}/{job.max_attempts}",
                    },
                )
                self.backend.delete(key)
                continue
            fencing_token = f"fence-{uuid4()}"
            expires = timestamp + timedelta(seconds=ttl_seconds)
            lease = {
                "job_id": job.job_id,
                "owner_token": owner_token,
                "fencing_token": fencing_token,
                "attempt": attempt,
                "acquired_at": _iso(timestamp),
                "expires_at": _iso(expires),
            }
            self._put(lease_key, lease)
            confirmed = self._get(lease_key)
            if confirmed.get("owner_token") != owner_token or confirmed.get("fencing_token") != fencing_token:
                continue
            self._put(
                self._running_key(job.job_id),
                {"job": json.loads(job.to_json()), "attempt": attempt, "fencing_token": fencing_token},
            )
            self.backend.delete(key)
            claim = JobClaim(
                job=job,
                owner_token=owner_token,
                fencing_token=fencing_token,
                attempt=attempt,
                lease_expires_at=expires,
            )
            try:
                self.heartbeat(claim, ttl_seconds=ttl_seconds, now=timestamp)
            except LostLeaseError:
                # Google Drive does not offer compare-and-swap. A racing worker
                # may replace this lease between write and confirmation; the
                # fencing token makes that safe, and this claimant simply tries
                # the next visible job instead of surfacing a transient error.
                continue
            return claim
        return None

    def assert_ownership(self, claim: JobClaim, *, now: datetime | None = None) -> dict[str, Any]:
        timestamp = now or datetime.now(UTC)
        key = self._lease_key(claim.job.job_id)
        if not self.backend.exists(key):
            raise LostLeaseError(f"job {claim.job.job_id} has no active lease")
        lease = self._get(key)
        if lease.get("owner_token") != claim.owner_token or lease.get("fencing_token") != claim.fencing_token:
            raise LostLeaseError(f"lease for job {claim.job.job_id} is owned by another worker")
        if _parse(lease["expires_at"]) <= timestamp:
            raise LostLeaseError(f"lease for job {claim.job.job_id} expired")
        return lease

    @_coordinated
    def heartbeat(self, claim: JobClaim, *, ttl_seconds: int = 900, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        lease = self.assert_ownership(claim, now=timestamp)
        lease["expires_at"] = _iso(timestamp + timedelta(seconds=ttl_seconds))
        self._put(self._lease_key(claim.job.job_id), lease)
        self._put(
            self._heartbeat_key(claim.job.job_id),
            {
                "at": _iso(timestamp),
                "owner_token": claim.owner_token,
                "fencing_token": claim.fencing_token,
            },
        )

    @_coordinated
    def checkpoint(self, claim: JobClaim, checkpoint: Checkpoint, *, now: datetime | None = None) -> None:
        self.assert_ownership(claim, now=now)
        if checkpoint.job_id != claim.job.job_id:
            raise QueueError("checkpoint job_id does not match the claim")
        previous = self.load_checkpoint(checkpoint.job_id)
        if previous is not None and checkpoint.sequence <= previous.sequence:
            raise QueueError(f"checkpoint sequence {checkpoint.sequence} must be greater than {previous.sequence}")
        self._put(
            self._checkpoint_key(checkpoint.job_id),
            {
                "checkpoint": json.loads(checkpoint.to_json()),
                "owner_token": claim.owner_token,
                "fencing_token": claim.fencing_token,
                "updated_at": _iso(now or datetime.now(UTC)),
            },
        )

    def load_checkpoint(self, job_id: str) -> Checkpoint | None:
        key = self._checkpoint_key(job_id)
        if not self.backend.exists(key):
            return None
        raw = self._get(key)
        payload = raw.get("checkpoint", raw) if isinstance(raw, dict) else raw
        return Checkpoint.from_dict(payload)

    def complete(
        self,
        claim: JobClaim,
        *,
        result: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        self._finish(claim, JobStatus.COMPLETED, payload={"result": result or {}}, now=now)

    def fail(self, claim: JobClaim, *, reason: str, now: datetime | None = None) -> None:
        self._finish(claim, JobStatus.FAILED, payload={"reason": reason}, now=now)

    def block(self, claim: JobClaim, blocker: Blocker, *, now: datetime | None = None) -> None:
        self._finish(
            claim,
            JobStatus.BLOCKED,
            payload={"blocker": json.loads(blocker.to_json()), "attempt": claim.attempt},
            now=now,
        )

    @_coordinated
    def _finish(
        self,
        claim: JobClaim,
        status: JobStatus,
        *,
        payload: dict[str, Any],
        now: datetime | None,
    ) -> None:
        timestamp = now or datetime.now(UTC)
        self.assert_ownership(claim, now=timestamp)
        running_key = self._running_key(claim.job.job_id)
        if not self.backend.exists(running_key):
            raise QueueError(f"job {claim.job.job_id} is not running")
        record = {
            "job": json.loads(claim.job.to_json()),
            "status": status.value,
            "finished_at": _iso(timestamp),
            "attempt": claim.attempt,
            "owner_token": claim.owner_token,
            "fencing_token": claim.fencing_token,
            **payload,
        }
        # Re-check immediately before publishing the terminal record.  The
        # fencing token ensures a stale worker cannot commit after takeover.
        self.assert_ownership(claim, now=timestamp)
        self._put(self._terminal_key(status, claim.job.job_id), record)
        for key in (running_key, self._lease_key(claim.job.job_id), self._heartbeat_key(claim.job.job_id)):
            self.backend.delete(key)

    @_coordinated
    def reclaim_expired(self, *, now: datetime | None = None) -> tuple[str, ...]:
        timestamp = now or datetime.now(UTC)
        reclaimed: list[str] = []
        for key in self.backend.list_prefix(f"{self.root}/running/"):
            if not key.endswith(".json") or any(
                key.endswith(suffix) for suffix in (".lease.json", ".heartbeat.json", ".checkpoint.json")
            ):
                continue
            raw = self._get(key)
            job = WorkerJob.from_dict(raw.get("job", raw))
            if any(
                self.backend.exists(self._terminal_key(status, job.job_id))
                for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.BLOCKED)
            ):
                for stale in (key, self._lease_key(job.job_id), self._heartbeat_key(job.job_id)):
                    self.backend.delete(stale)
                continue
            attempt = int(raw.get("attempt", 0))
            lease_key = self._lease_key(job.job_id)
            expired = True
            if self.backend.exists(lease_key):
                expired = _parse(self._get(lease_key)["expires_at"]) <= timestamp
            if not expired:
                continue
            next_attempt = attempt + 1
            if next_attempt >= job.max_attempts:
                self._put(
                    self._terminal_key(JobStatus.FAILED, job.job_id),
                    {
                        "job": json.loads(job.to_json()),
                        "status": JobStatus.FAILED.value,
                        "finished_at": _iso(timestamp),
                        "attempt": next_attempt,
                        "reason": f"lease expired and maximum attempts reached: {next_attempt}/{job.max_attempts}",
                    },
                )
            else:
                self._put(
                    self._queued_key(job.resource_class.value, job.job_id),
                    {"job": json.loads(job.to_json()), "attempt": next_attempt, "queued_at": _iso(timestamp)},
                )
                reclaimed.append(job.job_id)
            for stale in (key, lease_key, self._heartbeat_key(job.job_id)):
                self.backend.delete(stale)
        return tuple(reclaimed)

    @_coordinated
    def resume_cleared_blockers(self, *, now: datetime | None = None) -> tuple[str, ...]:
        timestamp = now or datetime.now(UTC)
        resumed: list[str] = []
        for key in self.backend.list_prefix(f"{self.root}/blocked/"):
            if not key.endswith(".json"):
                continue
            record = self._get(key)
            blocker = Blocker.from_dict(record.get("blocker", {}))
            if not blocker.is_cleared(self.backend):
                continue
            job = WorkerJob.from_dict(record["job"])
            next_attempt = int(record.get("attempt", 0)) + 1
            if next_attempt >= job.max_attempts:
                continue
            self._put(
                self._queued_key(job.resource_class.value, job.job_id),
                {"job": json.loads(job.to_json()), "attempt": next_attempt, "queued_at": _iso(timestamp)},
            )
            self.backend.delete(key)
            resumed.append(job.job_id)
        return tuple(resumed)
