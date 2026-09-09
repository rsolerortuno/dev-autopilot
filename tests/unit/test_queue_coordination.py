from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from dev_autopilot.storage.backend import LocalStorageBackend
from dev_autopilot.worker.coordination import SQLiteCoordinator
from dev_autopilot.worker.job import JobStatus, ResourceClass, WorkerJob
from dev_autopilot.worker.queue import DriveQueue, LostLeaseError, QueueError

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _job(job_id: str) -> WorkerJob:
    return WorkerJob(
        job_id=job_id,
        project_id="project",
        resource_class=ResourceClass.CPU,
        entrypoint=("python", "-m", "noop"),
        output_prefix=f"outputs/{job_id}",
    )


@pytest.mark.parametrize("iteration", range(40))
def test_two_claimers_have_one_winner(iteration, tmp_path):
    backend = LocalStorageBackend(tmp_path / "store")
    coordinator = SQLiteCoordinator(tmp_path / "coordination.sqlite3")
    first = DriveQueue(backend, coordinator=coordinator)
    second = DriveQueue(backend, coordinator=SQLiteCoordinator(tmp_path / "coordination.sqlite3"))
    first.submit(_job(f"race-{iteration}"), now=T0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda queue: queue.claim("cpu", "worker", now=T0), (first, second)))
    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.parametrize("iteration", range(40))
def test_takeover_fences_old_finish(iteration, tmp_path):
    backend = LocalStorageBackend(tmp_path / "store")
    queue = DriveQueue(backend, coordinator=SQLiteCoordinator(tmp_path / "coordination.sqlite3"))
    queue.submit(_job(f"takeover-{iteration}"), now=T0)
    old = queue.claim("cpu", "old", ttl_seconds=1, now=T0)
    assert old is not None
    assert queue.reclaim_expired(now=T0 + timedelta(seconds=2)) == (old.job_id,)
    new = queue.claim("cpu", "new", now=T0 + timedelta(seconds=2))
    assert new is not None
    with pytest.raises(LostLeaseError):
        queue.complete(old, now=T0 + timedelta(seconds=2))
    queue.complete(new, now=T0 + timedelta(seconds=3))


@pytest.mark.parametrize("iteration", range(40))
def test_terminal_finish_wins_over_expiry_cleanup(iteration, tmp_path):
    backend = LocalStorageBackend(tmp_path / "store")
    queue = DriveQueue(backend, coordinator=SQLiteCoordinator(tmp_path / "coordination.sqlite3"))
    queue.submit(_job(f"finish-{iteration}"), now=T0)
    claim = queue.claim("cpu", "worker", ttl_seconds=2, now=T0)
    assert claim is not None
    queue.complete(claim, now=T0 + timedelta(seconds=1))
    assert queue.reclaim_expired(now=T0 + timedelta(seconds=2)) == ()
    status = queue.job_status(claim.job_id)
    assert status is not None and status["status"] == "COMPLETED"


def test_remote_drive_backend_requires_explicit_coordination():
    backend = type("DriveStorageBackend", (), {"_injected_service": False})()
    with pytest.raises(QueueError, match="shared SQLite coordinator"):
        DriveQueue(backend)


def test_terminal_publication_survives_cleanup_crash(tmp_path):
    class CrashOnCleanup(LocalStorageBackend):
        def delete(self, key):
            if not self.exists("devautopilot/completed/crash.json"):
                return super().delete(key)
            raise OSError("simulated crash after terminal publication")

    backend = CrashOnCleanup(tmp_path / "store")
    queue = DriveQueue(backend, coordinator=SQLiteCoordinator(tmp_path / "coordination.sqlite3"))
    queue.submit(_job("crash"), now=T0)
    claim = queue.claim("cpu", "worker", ttl_seconds=10, now=T0)
    assert claim is not None
    with pytest.raises(OSError, match="simulated crash"):
        queue.complete(claim, now=T0 + timedelta(seconds=1))
    assert queue.job_status("crash")["status"] == "COMPLETED"


def test_reclaim_cleans_stale_running_record_without_resurrecting_terminal(tmp_path):
    backend = LocalStorageBackend(tmp_path / "store")
    queue = DriveQueue(backend, coordinator=SQLiteCoordinator(tmp_path / "coordination.sqlite3"))
    queue.submit(_job("stale"), now=T0)
    claim = queue.claim("cpu", "worker", ttl_seconds=1, now=T0)
    assert claim is not None
    backend.put_json(queue._terminal_key(JobStatus.COMPLETED, "stale"), {"status": "COMPLETED"})
    assert queue.reclaim_expired(now=T0 + timedelta(seconds=2)) == ()
    assert queue.job_status("stale")["status"] == "COMPLETED"
    assert not backend.exists(queue._running_key("stale"))


def test_barrier_competing_mutations_are_serialized(tmp_path):
    """A real thread interleaving cannot create two queue records."""
    backend = LocalStorageBackend(tmp_path / "store")
    coordinator_path = tmp_path / "coordination.sqlite3"
    first = DriveQueue(backend, coordinator=SQLiteCoordinator(coordinator_path))
    second = DriveQueue(backend, coordinator=SQLiteCoordinator(coordinator_path))
    barrier = Barrier(2)
    first.submit(_job("barrier"), now=T0)

    def claim(queue, owner):
        barrier.wait(timeout=5)
        return queue.claim("cpu", owner, now=T0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda args: claim(*args), ((first, "one"), (second, "two"))))
    assert sum(item is not None for item in claims) == 1


def test_barrier_crash_after_publish_does_not_resurrect_job(tmp_path):
    class CrashOnCleanup(LocalStorageBackend):
        def delete(self, key):
            if key.endswith("/running/crash-barrier.json") and self.exists("devautopilot/completed/crash-barrier.json"):
                raise OSError("simulated crash after terminal publication")
            return super().delete(key)

    backend = CrashOnCleanup(tmp_path / "store")
    queue = DriveQueue(backend, coordinator=SQLiteCoordinator(tmp_path / "coordination.sqlite3"))
    queue.submit(_job("crash-barrier"), now=T0)
    claim = queue.claim("cpu", "worker", now=T0)
    assert claim is not None
    barrier = Barrier(2)

    def finish_and_reclaim():
        barrier.wait(timeout=5)
        with pytest.raises(OSError):
            queue.complete(claim, now=T0 + timedelta(seconds=1))

    def recover():
        barrier.wait(timeout=5)
        return queue.reclaim_expired(now=T0 + timedelta(seconds=2))

    with ThreadPoolExecutor(max_workers=2) as pool:
        finish, recovery = pool.map(lambda fn: fn(), (finish_and_reclaim, recover))
    assert finish is None
    assert recovery == ()
    assert queue.job_status("crash-barrier")["status"] == "COMPLETED"
