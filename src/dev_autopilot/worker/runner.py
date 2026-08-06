"""M05 worker runner with fencing, heartbeats, timeout and checkpoint resume."""

from __future__ import annotations

import inspect
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dev_autopilot.storage.reassembler import reassemble_from_prefix
from dev_autopilot.storage.splitter import split_file
from dev_autopilot.worker.job import (
    Blocker,
    Checkpoint,
    JobClaim,
    JobInput,
    MountMode,
    ResourceClass,
    WorkerJob,
)
from dev_autopilot.worker.queue import DriveQueue, LostLeaseError

CommandExecutor = Callable[..., tuple[int, str]]
_READ_BLOCK = 4 * 1024 * 1024
_MIN_ENV = ("PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=3)


def subprocess_executor(
    command: tuple[str, ...],
    workdir: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[int, str]:  # pragma: no cover - subprocess branches tested separately
    process = subprocess.Popen(
        list(command),
        cwd=workdir,
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return 124, f"entrypoint timed out after {timeout_seconds}s"
    summary = (stdout or stderr or "").strip()[:4000] or "no output"
    return process.returncode, summary


@dataclass(frozen=True)
class WorkerResult:
    job_id: str
    completed: bool
    exit_code: int | None
    summary: str
    resumed_from_sequence: int | None = None


def _safe_destination(root: Path, relative: str) -> Path:
    destination = (root / relative).resolve()
    root_resolved = root.resolve()
    if root_resolved not in destination.parents:
        raise ValueError(f"path escapes worker directory: {relative!r}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _copy_remote_file(queue: DriveQueue, key: str, destination: Path) -> None:
    total = queue.backend.size(key)
    offset = 0
    tmp = destination.with_name(destination.name + ".partial")
    with tmp.open("wb") as handle:
        while offset < total:
            block = queue.backend.get_range(key, offset=offset, length=min(_READ_BLOCK, total - offset))
            if not block:
                raise OSError(f"remote input {key!r} ended at {offset}, expected {total}")
            handle.write(block)
            offset += len(block)
    os.replace(tmp, destination)


def stage_input(queue: DriveQueue, job_input: JobInput, workdir: Path) -> Path:
    destination = _safe_destination(workdir, job_input.local_name)
    if job_input.mount_mode is MountMode.REASSEMBLE:
        reassemble_from_prefix(queue.backend, key_prefix=job_input.key_prefix, destination=destination)
    elif job_input.mount_mode is MountMode.DIRECT:
        _copy_remote_file(queue, job_input.key_prefix, destination)
    else:
        descriptor = {
            "storage_backend": type(queue.backend).__name__,
            "key_prefix": job_input.key_prefix,
            "mode": "range_stream",
        }
        destination.write_text(json.dumps(descriptor, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return destination


def _worker_environment(job: WorkerJob, job_dir: Path, checkpoint_path: Path, progress_path: Path) -> dict[str, str]:
    allowed = set(_MIN_ENV) | set(job.env_allowlist)
    env = {name: value for name in allowed if (value := os.environ.get(name)) is not None}
    pythonpath = env.get("PYTHONPATH")
    if pythonpath:
        # A worker changes cwd to its isolated job directory. Preserve source-tree
        # and virtual-environment imports by resolving relative entries before
        # that cwd change rather than silently breaking the configured runtime.
        env["PYTHONPATH"] = os.pathsep.join(
            str(Path(entry).expanduser().resolve()) if entry else str(Path.cwd()) for entry in pythonpath.split(os.pathsep)
        )
    env.update(
        {
            "DEV_AUTOPILOT_JOB_ID": job.job_id,
            "DEV_AUTOPILOT_PROJECT_ID": job.project_id,
            "DEV_AUTOPILOT_WORKDIR": str(job_dir),
            "DEV_AUTOPILOT_CHECKPOINT_FILE": str(checkpoint_path),
            "DEV_AUTOPILOT_PROGRESS_FILE": str(progress_path),
            "DEV_AUTOPILOT_RESUME_SEQUENCE": "-1",
        }
    )
    return env


def _call_executor(
    executor: CommandExecutor,
    command: tuple[str, ...],
    workdir: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[int, str]:
    # Compatibility with existing two-argument fake executors while the public
    # M05 executor contract uses four arguments.
    try:
        parameters = len(inspect.signature(executor).parameters)
    except (TypeError, ValueError):
        parameters = 4
    if parameters <= 2:
        return executor(command, workdir)
    return executor(command, workdir, env, timeout_seconds)


def _read_progress(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"progress_file_error": "invalid JSON"}
    return value if isinstance(value, dict) else {"progress": value}


def _available_ram_bytes() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return 0


def _gpu_memory_bytes() -> int:
    if shutil.which("nvidia-smi") is None:
        return 0
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        values = [int(line.strip()) * 1024 * 1024 for line in result.stdout.splitlines() if line.strip().isdigit()]
        return max(values, default=0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _resource_blocker(job: WorkerJob, workdir: Path) -> Blocker | None:
    free_disk = shutil.disk_usage(workdir).free
    if free_disk < job.minimum_free_disk_bytes:
        return Blocker(
            blocker_id=f"B-{job.job_id}-DISK",
            milestone=job.milestone_id,
            reason=f"free disk {free_disk} is below required {job.minimum_free_disk_bytes}",
            single_required_action="Start a Colab worker with sufficient temporary disk",
            resume_when_key_exists=f"workers/{job.resource_class.value}/READY.json",
        )
    available_ram = _available_ram_bytes()
    if available_ram and available_ram < job.minimum_ram_bytes:
        return Blocker(
            blocker_id=f"B-{job.job_id}-RAM",
            milestone=job.milestone_id,
            reason=f"available RAM {available_ram} is below required {job.minimum_ram_bytes}",
            single_required_action="Start a Colab high-RAM worker",
            resume_when_key_exists="workers/high_ram/READY.json",
        )
    if job.resource_class is ResourceClass.GPU and shutil.which("nvidia-smi") is None:
        return Blocker(
            blocker_id=f"B-{job.job_id}-GPU",
            milestone=job.milestone_id,
            reason="GPU runtime is not available",
            single_required_action="Start a Colab GPU worker",
            resume_when_key_exists="workers/gpu/READY.json",
        )
    if job.resource_class is ResourceClass.GPU and _gpu_memory_bytes() < job.minimum_gpu_memory_bytes:
        return Blocker(
            blocker_id=f"B-{job.job_id}-GPU-MEM",
            milestone=job.milestone_id,
            reason="GPU memory is below the job minimum",
            single_required_action="Start a Colab GPU worker with sufficient GPU memory",
            resume_when_key_exists="workers/gpu/READY.json",
        )
    if job.resource_class is ResourceClass.TPU and not (os.environ.get("COLAB_TPU_ADDR") or os.environ.get("TPU_NAME")):
        return Blocker(
            blocker_id=f"B-{job.job_id}-TPU",
            milestone=job.milestone_id,
            reason="TPU runtime is not available",
            single_required_action="Start a Colab TPU worker",
            resume_when_key_exists="workers/tpu/READY.json",
        )
    return None


def _upload_outputs(queue: DriveQueue, claim: JobClaim, outputs_dir: Path) -> list[dict[str, Any]]:
    uploaded: list[dict[str, Any]] = []
    if not outputs_dir.is_dir():
        return uploaded
    # Every attempt writes to a fencing-token namespace. A stale worker may
    # upload bytes, but can neither overwrite the current attempt nor publish
    # them as terminal outputs.
    attempt_prefix = f"{claim.job.output_prefix}/attempt-{claim.attempt:03d}-{claim.fencing_token}"
    for produced in sorted(outputs_dir.rglob("*")):
        if not produced.is_file():
            continue
        rel = produced.relative_to(outputs_dir).as_posix()
        if produced.stat().st_size >= claim.job.output_split_threshold_bytes:
            prefix = f"{attempt_prefix}/{rel}.parts"
            report = split_file(
                produced,
                queue.backend,
                key_prefix=prefix,
                part_size_bytes=claim.job.output_part_size_bytes,
            )
            uploaded.append(
                {
                    "path": rel,
                    "split_prefix": prefix,
                    "size": report.manifest.total_size,
                    "sha256": report.manifest.whole_sha256,
                }
            )
        else:
            key = f"{attempt_prefix}/{rel}"
            digest = queue.backend.put_file(key, produced)
            uploaded.append({"path": rel, "key": key, "size": produced.stat().st_size, "sha256": digest})
    return uploaded


def run_one(
    queue: DriveQueue,
    *,
    resource_class: str,
    owner_token: str,
    workdir: Path | str,
    executor: CommandExecutor = subprocess_executor,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
    lease_ttl_seconds: int = 900,
    heartbeat_interval_seconds: float | None = None,
) -> WorkerResult | None:
    work = Path(workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    claim = queue.claim(resource_class, owner_token, ttl_seconds=lease_ttl_seconds, now=now_factory())
    if claim is None:
        return None
    job = claim.job
    blocker = _resource_blocker(job, work)
    if blocker is not None:
        queue.block(claim, blocker, now=now_factory())
        return WorkerResult(job.job_id, False, None, blocker.reason)

    interval = heartbeat_interval_seconds or max(
        0.1,
        min(60.0, lease_ttl_seconds / 3, float(job.checkpoint_interval_seconds)),
    )
    queue.register_worker(
        job.resource_class.value,
        owner_token,
        ttl_seconds=max(lease_ttl_seconds, 60),
        metadata={"job_id": job.job_id},
        now=now_factory(),
    )
    heartbeat_stop = threading.Event()
    heartbeat_failures: list[Exception] = []
    heartbeat_queue = queue.fork()

    def heartbeat_loop() -> None:
        while not heartbeat_stop.wait(interval):
            try:
                heartbeat_queue.heartbeat(claim, ttl_seconds=lease_ttl_seconds, now=now_factory())
                heartbeat_queue.register_worker(
                    job.resource_class.value,
                    owner_token,
                    ttl_seconds=max(lease_ttl_seconds, 60),
                    metadata={"job_id": job.job_id},
                    now=now_factory(),
                )
            except Exception as exc:  # recorded and re-raised in the owner thread
                heartbeat_failures.append(exc)
                heartbeat_stop.set()
                return

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name=f"dev-autopilot-heartbeat-{job.job_id}",
        daemon=True,
    )
    heartbeat_thread.start()

    def stop_heartbeat() -> None:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=max(1.0, min(5.0, interval * 2)))

    def ensure_ownership() -> None:
        if heartbeat_failures:
            failure = heartbeat_failures[0]
            if isinstance(failure, LostLeaseError):
                raise failure
            raise LostLeaseError(f"heartbeat failed for {job.job_id}: {failure}")
        queue.assert_ownership(claim, now=now_factory())

    previous = queue.load_checkpoint(job.job_id)
    sequence = -1 if previous is None else previous.sequence
    resumed = None if previous is None else previous.sequence
    job_dir: Path | None = None
    cleanup_job_dir = False
    try:
        job_dir = _safe_destination(work, job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = job_dir / ".dev-autopilot-checkpoint.json"
        progress_path = job_dir / ".dev-autopilot-progress.json"
        if previous is not None:
            checkpoint_path.write_text(previous.to_json() + "\n", encoding="utf-8")
        else:
            checkpoint_path.write_text("{}\n", encoding="utf-8")

        for job_input in job.inputs:
            ensure_ownership()
            stage_input(queue, job_input, job_dir)
        ensure_ownership()
        sequence += 1
        queue.checkpoint(
            claim,
            Checkpoint(
                job_id=job.job_id,
                sequence=sequence,
                progress={"inputs_staged": True, "resumed_from_sequence": resumed},
                note="inputs staged",
            ),
            now=now_factory(),
        )
        env = _worker_environment(job, job_dir, checkpoint_path, progress_path)
        env["DEV_AUTOPILOT_RESUME_SEQUENCE"] = str(-1 if previous is None else previous.sequence)
        started = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"worker-{job.job_id}")
        future = pool.submit(_call_executor, executor, job.entrypoint, job_dir, env, job.timeout_seconds)
        try:
            while True:
                remaining = job.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    future.cancel()
                    exit_code, summary = 124, f"entrypoint timed out after {job.timeout_seconds}s"
                    break
                try:
                    exit_code, summary = future.result(timeout=min(interval, remaining))
                    break
                except FutureTimeoutError:
                    ensure_ownership()
                    sequence += 1
                    queue.checkpoint(
                        claim,
                        Checkpoint(
                            job_id=job.job_id,
                            sequence=sequence,
                            progress={
                                "elapsed_seconds": round(time.monotonic() - started, 3),
                                **_read_progress(progress_path),
                            },
                            note="periodic worker checkpoint",
                        ),
                        now=now_factory(),
                    )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        ensure_ownership()
        sequence += 1
        queue.checkpoint(
            claim,
            Checkpoint(
                job_id=job.job_id,
                sequence=sequence,
                progress={"exit_code": exit_code, **_read_progress(progress_path)},
                note="entrypoint finished",
            ),
            now=now_factory(),
        )
        if exit_code != 0:
            stop_heartbeat()
            ensure_ownership()
            queue.fail(claim, reason=f"entrypoint exited {exit_code}: {summary}", now=now_factory())
            cleanup_job_dir = True
            return WorkerResult(job.job_id, False, exit_code, summary, resumed)

        ensure_ownership()
        outputs = _upload_outputs(queue, claim, job_dir / "outputs")
        ensure_ownership()
        stop_heartbeat()
        ensure_ownership()
        queue.complete(
            claim,
            result={"summary": summary, "outputs": outputs, "checkpoint_sequence": sequence},
            now=now_factory(),
        )
        cleanup_job_dir = True
        return WorkerResult(job.job_id, True, exit_code, summary, resumed)
    except LostLeaseError as exc:
        # A stale worker must never publish failure or terminal outputs after takeover.
        # It also cannot safely resume locally, so remove its isolated scratch
        # directory and let the current owner re-stage from durable storage.
        cleanup_job_dir = True
        return WorkerResult(job.job_id, False, None, str(exc), resumed)
    except Exception as exc:
        stop_heartbeat()
        try:
            queue.fail(claim, reason=f"worker error: {exc}", now=now_factory())
            cleanup_job_dir = True
        except Exception:
            # Preserve local scratch when the terminal state could not be
            # persisted; an operator may need it to diagnose or recover.
            pass
        return WorkerResult(job.job_id, False, None, str(exc), resumed)
    finally:
        stop_heartbeat()
        if cleanup_job_dir and job_dir is not None:
            shutil.rmtree(job_dir, ignore_errors=True)


def poll_loop(
    queue: DriveQueue,
    *,
    resource_class: str,
    owner_token: str,
    workdir: Path | str,
    max_jobs: int,
    executor: CommandExecutor = subprocess_executor,
) -> list[WorkerResult]:
    results: list[WorkerResult] = []
    for _ in range(max_jobs):
        queue.resume_cleared_blockers()
        queue.reclaim_expired()
        result = run_one(
            queue,
            resource_class=resource_class,
            owner_token=owner_token,
            workdir=workdir,
            executor=executor,
        )
        if result is None:
            break
        results.append(result)
    return results


def describe_worker_job(job: WorkerJob) -> str:
    return f"{job.job_id} [{job.resource_class.value}] -> {' '.join(job.entrypoint)}"
