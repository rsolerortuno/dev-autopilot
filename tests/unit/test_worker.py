"""M05 queue, fencing, recovery and worker runner tests."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from dev_autopilot.storage.backend import LocalStorageBackend
from dev_autopilot.storage.splitter import split_file
from dev_autopilot.worker.job import Blocker, Checkpoint, JobInput, ResourceClass, WorkerJob
from dev_autopilot.worker.queue import DriveQueue, LostLeaseError, QueueError
from dev_autopilot.worker.runner import _worker_environment, run_one

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _queue(tmp_path) -> DriveQueue:
    return DriveQueue(LocalStorageBackend(tmp_path / "store"))


def _job(job_id: str = "J-1", rc: ResourceClass = ResourceClass.CPU, **kw) -> WorkerJob:
    base = {
        "job_id": job_id,
        "project_id": "targetintel",
        "resource_class": rc,
        "entrypoint": ["python", "-m", "noop"],
        "output_prefix": f"projects/targetintel/out/{job_id}",
    }
    base.update(kw)
    return WorkerJob.model_validate(base)


def test_submit_and_claim(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    claim = queue.claim("cpu", "worker-a", now=T0)
    assert claim is not None and claim.job_id == "J-1"
    assert claim.fencing_token.startswith("fence-")
    assert queue.list_queued("cpu") == ()


def test_duplicate_submission_is_rejected(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    with pytest.raises(QueueError):
        queue.submit(_job(), now=T0)


def test_queue_rejects_unknown_resource_and_safely_names_worker_marker(tmp_path):
    queue = _queue(tmp_path)
    with pytest.raises(ValueError):
        queue.list_queued("../../escape")
    queue.register_worker("cpu", "../worker/with spaces", now=T0)
    markers = queue.backend.list_prefix("workers/cpu/")
    assert any(key.endswith(".READY.json") and ".." not in key for key in markers)


def test_duplicate_job_id_is_rejected_across_resource_queues(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(rc=ResourceClass.CPU), now=T0)
    with pytest.raises(QueueError):
        queue.submit(_job(rc=ResourceClass.GPU), now=T0)


def test_claim_returns_none_on_empty_queue(tmp_path):
    assert _queue(tmp_path).claim("cpu", "worker-a", now=T0) is None


def test_active_lease_and_fencing(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    claim = queue.claim("cpu", "worker-a", now=T0)
    assert claim is not None
    forged = claim.model_copy(update={"owner_token": "worker-b"})
    with pytest.raises(LostLeaseError):
        queue.heartbeat(forged, now=T0 + timedelta(seconds=5))
    queue.heartbeat(claim, now=T0 + timedelta(seconds=5))


def test_watchdog_reclaims_and_increments_attempt(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    first = queue.claim("cpu", "worker-a", ttl_seconds=10, now=T0)
    assert first is not None
    assert queue.reclaim_expired(now=T0 + timedelta(seconds=9)) == ()
    assert queue.reclaim_expired(now=T0 + timedelta(seconds=11)) == ("J-1",)
    second = queue.claim("cpu", "worker-b", now=T0 + timedelta(seconds=12))
    assert second is not None and second.attempt == 1
    with pytest.raises(LostLeaseError):
        queue.complete(first, result={"stale": True}, now=T0 + timedelta(seconds=12))


def test_checkpoint_requires_owner_and_monotonic_sequence(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    claim = queue.claim("cpu", "worker-a", now=T0)
    assert claim is not None
    queue.checkpoint(claim, Checkpoint(job_id="J-1", sequence=3, progress={"rows": 1000}), now=T0)
    loaded = queue.load_checkpoint("J-1")
    assert loaded is not None and loaded.sequence == 3
    with pytest.raises(QueueError):
        queue.checkpoint(claim, Checkpoint(job_id="J-1", sequence=3), now=T0)


def test_complete_moves_job_to_completed(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    claim = queue.claim("cpu", "worker-a", now=T0)
    assert claim is not None
    queue.complete(claim, result={"ok": True}, now=T0 + timedelta(seconds=10))
    assert not queue.backend.exists(queue._running_key("J-1"))
    assert queue.backend.exists("devautopilot/completed/J-1.json")


def test_blocker_is_automatically_requeued_when_cleared(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    claim = queue.claim("cpu", "worker-a", now=T0)
    assert claim is not None
    blocker = Blocker(
        blocker_id="B-004",
        reason="GPU runtime unavailable",
        single_required_action="Start the GPU Colab worker",
        resume_when_key_exists="workers/gpu/READY.json",
    )
    queue.block(claim, blocker, now=T0 + timedelta(seconds=5))
    assert queue.resume_cleared_blockers(now=T0 + timedelta(seconds=6)) == ()
    queue.backend.put_bytes("workers/gpu/READY.json", b"{}")
    assert queue.resume_cleared_blockers(now=T0 + timedelta(seconds=7)) == ("J-1",)


def test_safe_paths_reject_traversal():
    with pytest.raises(ValidationError):
        _job(job_id="../escape")
    with pytest.raises(ValidationError):
        JobInput(key_prefix="inputs/data", local_name="../../outside")


def test_run_one_reassembles_input_uploads_output_and_filters_env(tmp_path, monkeypatch):
    queue = _queue(tmp_path)
    source = tmp_path / "input.bin"
    source.write_bytes(b"scientific-data" * 100)
    split_file(source, queue.backend, key_prefix="projects/targetintel/inputs/data", part_size_bytes=64)
    monkeypatch.setenv("ALLOWED_TOKEN", "visible")
    monkeypatch.setenv("SECRET_TOKEN", "hidden")
    job = _job(
        inputs=[JobInput(key_prefix="projects/targetintel/inputs/data", local_name="data.bin")],
        env_allowlist=["ALLOWED_TOKEN"],
    )
    queue.submit(job)
    observed = {}

    def fake_executor(command, workdir, env, timeout):
        observed["data"] = (workdir / "data.bin").read_bytes()
        observed["env"] = dict(env)
        (workdir / "outputs").mkdir(exist_ok=True)
        (workdir / "outputs" / "result.txt").write_text("done", encoding="utf-8")
        return 0, "ok"

    result = run_one(queue, resource_class="cpu", owner_token="worker-a", workdir=tmp_path / "work", executor=fake_executor)
    assert result is not None and result.completed
    assert observed["data"] == source.read_bytes()
    assert observed["env"]["ALLOWED_TOKEN"] == "visible"
    assert "SECRET_TOKEN" not in observed["env"]
    uploaded = queue.backend.list_prefix("projects/targetintel/out/J-1/")
    assert len([key for key in uploaded if key.endswith("/result.txt")]) == 1


def test_worker_environment_resolves_relative_pythonpath(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", f"src{os.pathsep}/absolute")
    env = _worker_environment(_job(), tmp_path / "job", tmp_path / "checkpoint.json", tmp_path / "progress.json")
    entries = env["PYTHONPATH"].split(os.pathsep)
    assert entries[0] == str((tmp_path / "src").resolve())
    assert entries[1] == "/absolute"


def test_reclaimed_job_receives_previous_checkpoint(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(), now=T0)
    first = queue.claim("cpu", "worker-a", ttl_seconds=5, now=T0)
    assert first is not None
    queue.checkpoint(first, Checkpoint(job_id="J-1", sequence=7, progress={"rows": 500}), now=T0)
    queue.reclaim_expired(now=T0 + timedelta(seconds=6))
    observed = {}

    def executor(command, workdir, env, timeout):
        observed["checkpoint"] = json.loads(Path(env["DEV_AUTOPILOT_CHECKPOINT_FILE"]).read_text(encoding="utf-8"))
        observed["resume_sequence"] = env["DEV_AUTOPILOT_RESUME_SEQUENCE"]
        return 0, "resumed"

    result = run_one(queue, resource_class="cpu", owner_token="worker-b", workdir=tmp_path / "work", executor=executor)
    assert result is not None and result.completed
    assert result.resumed_from_sequence == 7
    assert observed["checkpoint"]["sequence"] == 7
    assert observed["resume_sequence"] == "7"


def test_periodic_heartbeat_keeps_short_lease_alive(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job(checkpoint_interval_seconds=1, timeout_seconds=5))

    def slow(command, workdir, env, timeout):
        time.sleep(0.25)
        return 0, "done"

    result = run_one(
        queue,
        resource_class="cpu",
        owner_token="worker-a",
        workdir=tmp_path / "work",
        executor=slow,
        lease_ttl_seconds=1,
        heartbeat_interval_seconds=0.05,
    )
    assert result is not None and result.completed
    checkpoint = queue.load_checkpoint("J-1")
    assert checkpoint is not None and checkpoint.sequence >= 2


def test_run_one_records_failure_on_nonzero_exit(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job())

    def failing(command, workdir, env, timeout):
        return 1, "boom"

    result = run_one(queue, resource_class="cpu", owner_token="worker-a", workdir=tmp_path / "work", executor=failing)
    assert result is not None and not result.completed and result.exit_code == 1
    assert queue.backend.exists("devautopilot/failed/J-1.json")


def test_background_heartbeat_covers_slow_input_staging(tmp_path):
    from dev_autopilot.storage.backend import LocalStorageBackend

    class SlowBackend(LocalStorageBackend):
        def get_range(self, key, *, offset, length):
            time.sleep(0.08)
            return super().get_range(key, offset=offset, length=length)

        def fork(self):
            return LocalStorageBackend(self.root)

    backend = SlowBackend(tmp_path / "store")
    queue = DriveQueue(backend)
    source = tmp_path / "slow.bin"
    source.write_bytes(b"x" * 2048)
    split_file(source, backend, key_prefix="inputs/slow", part_size_bytes=64)
    queue.submit(_job(inputs=[JobInput(key_prefix="inputs/slow", local_name="slow.bin")]))

    def executor(command, workdir, env, timeout):
        assert (workdir / "slow.bin").stat().st_size == 2048
        return 0, "done"

    result = run_one(
        queue,
        resource_class="cpu",
        owner_token="worker-a",
        workdir=tmp_path / "work",
        executor=executor,
        lease_ttl_seconds=1,
        heartbeat_interval_seconds=0.05,
    )
    assert result is not None and result.completed


def test_worker_ready_marker_honours_expiry(tmp_path):
    queue = _queue(tmp_path)
    blocker = Blocker(
        blocker_id="B-READY",
        reason="worker needed",
        single_required_action="start worker",
        resume_when_key_exists="workers/gpu/READY.json",
    )
    queue.backend.put_bytes(
        "workers/gpu/READY.json",
        json.dumps({"expires_at": "2000-01-01T00:00:00+00:00"}).encode(),
    )
    assert blocker.is_cleared(queue.backend) is False
    queue.register_worker("gpu", "worker-gpu", ttl_seconds=60)
    assert blocker.is_cleared(queue.backend) is True


def test_claim_respects_existing_live_lease_when_queue_entry_reappears(tmp_path):
    queue = _queue(tmp_path)
    job = _job()
    queue.submit(job, now=T0)
    queued_key = queue._queued_key("cpu", job.job_id)
    queued_payload = queue.backend.get_bytes(queued_key)
    first = queue.claim("cpu", "worker-a", ttl_seconds=60, now=T0)
    assert first is not None

    # Simulate a stale/eventually-consistent queue listing after deletion.
    queue.backend.put_bytes(queued_key, queued_payload)
    before = queue._get(queue._lease_key(job.job_id))
    second = queue.claim("cpu", "worker-b", ttl_seconds=60, now=T0 + timedelta(seconds=1))
    after = queue._get(queue._lease_key(job.job_id))

    assert second is None
    assert after == before
    assert after["owner_token"] == "worker-a"


def test_stale_queue_entry_cannot_resurrect_terminal_job(tmp_path):
    queue = _queue(tmp_path)
    job = _job()
    queue.submit(job, now=T0)
    queued_key = queue._queued_key("cpu", job.job_id)
    queued_payload = queue.backend.get_bytes(queued_key)
    claim = queue.claim("cpu", "worker-a", now=T0)
    assert claim is not None
    queue.complete(claim, result={"ok": True}, now=T0 + timedelta(seconds=1))
    queue.backend.put_bytes(queued_key, queued_payload)

    assert queue.claim("cpu", "worker-b", now=T0 + timedelta(seconds=2)) is None
    assert not queue.backend.exists(queued_key)
    assert queue.job_status(job.job_id)["status"] == "COMPLETED"


def test_completed_worker_removes_isolated_job_directory(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job())
    work = tmp_path / "work"

    def executor(command, workdir, env, timeout):
        del command, env, timeout
        (workdir / "outputs").mkdir(exist_ok=True)
        (workdir / "outputs" / "result.txt").write_text("done", encoding="utf-8")
        return 0, "ok"

    result = run_one(queue, resource_class="cpu", owner_token="worker-a", workdir=work, executor=executor)
    assert result is not None and result.completed
    assert not (work / "J-1").exists()
    assert queue.backend.exists("devautopilot/completed/J-1.json")


def test_failed_terminal_worker_removes_isolated_job_directory(tmp_path):
    queue = _queue(tmp_path)
    queue.submit(_job())
    work = tmp_path / "work"

    def executor(command, workdir, env, timeout):
        del command, env, timeout
        (workdir / "large.tmp").write_bytes(b"x" * 1024)
        return 2, "failed"

    result = run_one(queue, resource_class="cpu", owner_token="worker-a", workdir=work, executor=executor)
    assert result is not None and not result.completed
    assert not (work / "J-1").exists()
    assert queue.backend.exists("devautopilot/failed/J-1.json")
