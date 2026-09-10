"""Run a bounded offline soak against local durable queue and budget stores."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dev_autopilot.budget import BudgetConfig, BudgetExceeded, BudgetStore
from dev_autopilot.storage.backend import LocalStorageBackend
from dev_autopilot.worker.coordination import SQLiteCoordinator
from dev_autopilot.worker.job import JobStatus, ResourceClass, WorkerJob
from dev_autopilot.worker.queue import DriveQueue, LostLeaseError


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _job(job_id: str) -> WorkerJob:
    return WorkerJob(
        job_id=job_id,
        project_id="offline-soak",
        milestone_id="M16",
        resource_class=ResourceClass.CPU,
        entrypoint=("python", "-c", "print('offline soak')"),
        output_prefix=f"outputs/{job_id}",
    )


def _normal_wait_observed(delta: float, interval: float) -> float:
    """Count ordinary wait time, excluding a likely host suspend/resume gap."""
    if delta < 0 or delta > max(2 * interval, 60.0):
        return 0.0
    return min(delta, interval)


def _queue_cycle(root: Path, index: int) -> dict[str, Any]:
    backend = LocalStorageBackend(root / "objects")
    coordinator_path = root / "queue-coordination.sqlite3"
    queue = DriveQueue(backend, coordinator=SQLiteCoordinator(coordinator_path))
    now = datetime.now(UTC)
    job_id = f"soak-{index:06d}"
    queue.submit(_job(job_id), now=now)
    old = queue.claim("cpu", "owner-stale", ttl_seconds=1, now=now)
    if old is None:
        raise AssertionError("initial claim did not win")
    if queue.reclaim_expired(now=now + timedelta(seconds=2)) != (job_id,):
        raise AssertionError("expired lease was not reclaimed")
    new = queue.claim("cpu", "owner-current", now=now + timedelta(seconds=2))
    if new is None:
        raise AssertionError("replacement claim did not win")
    try:
        queue.complete(old, now=now + timedelta(seconds=2))
    except LostLeaseError:
        stale_owner_denied = True
    else:
        stale_owner_denied = False
    queue.complete(new, now=now + timedelta(seconds=3))
    reopened = DriveQueue(backend, coordinator=SQLiteCoordinator(coordinator_path))
    status = reopened.job_status(job_id)
    if status is None or status.get("status") != JobStatus.COMPLETED.value:
        raise AssertionError("completed job was not durable after coordinator reopen")

    class CrashAfterPublish(LocalStorageBackend):
        def delete(self, key: str) -> None:
            running = f"devautopilot/running/crash-{index:06d}.json"
            completed = f"devautopilot/completed/crash-{index:06d}.json"
            if key == running and self.exists(completed):
                raise OSError("injected crash after terminal publication")
            super().delete(key)

    crash_backend = CrashAfterPublish(root / "crash-objects")
    crash_queue = DriveQueue(crash_backend, coordinator=SQLiteCoordinator(root / "crash-coordination.sqlite3"))
    crash_id = f"crash-{index:06d}"
    crash_queue.submit(_job(crash_id), now=now)
    crash_claim = crash_queue.claim("cpu", "crash-owner", now=now)
    if crash_claim is None:
        raise AssertionError("crash claim did not win")
    try:
        crash_queue.complete(crash_claim, now=now + timedelta(seconds=1))
    except OSError as error:
        crash_injected = "after terminal publication" in str(error)
    else:
        crash_injected = False
    crash_status = crash_queue.job_status(crash_id)
    crash_recovered = crash_status is not None and crash_status.get("status") == JobStatus.COMPLETED.value
    return {
        "lease_expiration_recovered": True,
        "stale_owner_denied": stale_owner_denied,
        "reopened_completed": True,
        "crash_after_publish_injected": crash_injected,
        "crash_terminal_survived": crash_recovered,
    }


def _budget_cycle(root: Path, index: int) -> dict[str, Any]:
    config = BudgetConfig("offline-soak", f"M{index + 16:02d}", max_calls=1)
    path = root / "budget.sqlite3"
    store = BudgetStore(path)
    call_id = f"budget-{index:06d}"
    store.reserve(config, call_id=call_id)
    reopened = BudgetStore(path)
    try:
        reopened.reserve(config, call_id=f"denied-{index:06d}")
    except BudgetExceeded:
        denied = True
    else:
        denied = False
    return {"reservation_persisted": bool(reopened.reservations("offline-soak", config.milestone_id)), "budget_denied": denied}


def run(output: Path, *, duration_seconds: float = 28_800.0, interval_seconds: float = 5.0) -> dict[str, Any]:
    if duration_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("duration and interval must be positive")
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"output must be a new empty directory; refusing existing durable state: {output}")
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / "soak-checkpoint.json"
    started = time.monotonic()
    active_seconds = 0.0
    observed_seconds = 0.0
    cycles = 0
    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    status = "incomplete"
    while observed_seconds < duration_seconds:
        cycle_started = time.monotonic()
        try:
            result = {"queue": _queue_cycle(output, cycles), "budget": _budget_cycle(output, cycles)}
            if not all(result["queue"].values()) or not all(result["budget"].values()):
                raise AssertionError("injected soak invariant failed")
            results.append(result)
            cycles += 1
        except Exception as error:  # checkpoint the failure before returning
            failures.append({"cycle": cycles, "error": f"{type(error).__name__}: {error}"})
            status = "failed"
            operation_seconds = max(0.0, time.monotonic() - cycle_started)
            active_seconds += operation_seconds
            observed_seconds += operation_seconds
            _atomic_json(
                checkpoint,
                _report(status, started, active_seconds, observed_seconds, duration_seconds, cycles, failures, results),
            )
            break
        operation_seconds = max(0.0, time.monotonic() - cycle_started)
        active_seconds += operation_seconds
        observed_seconds += operation_seconds
        _atomic_json(
            checkpoint, _report(status, started, active_seconds, observed_seconds, duration_seconds, cycles, failures, results)
        )
        remaining = duration_seconds - observed_seconds
        if remaining > 0:
            before_wait = time.monotonic()
            time.sleep(min(interval_seconds, remaining))
            observed_seconds += _normal_wait_observed(time.monotonic() - before_wait, interval_seconds)
            _atomic_json(
                checkpoint,
                _report(status, started, active_seconds, observed_seconds, duration_seconds, cycles, failures, results),
            )
    if not failures and observed_seconds >= duration_seconds:
        status = "success" if cycles else "incomplete"
    report = _report(status, started, active_seconds, observed_seconds, duration_seconds, cycles, failures, results)
    _atomic_json(checkpoint, report)
    _atomic_json(output / "soak-report.json", report)
    return report


def _report(
    status: str,
    started: float,
    active: float,
    observed: float,
    target: float,
    cycles: int,
    failures: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "mode": "offline soak with injected failures",
        "provider_evaluation": False,
        "colab_claim": False,
        "status": status,
        "cycles": cycles,
        "failures": failures,
        "metrics": {
            "active_seconds": active,
            "observed_seconds": observed,
            "target_seconds": target,
            "elapsed_seconds": max(0.0, time.monotonic() - started),
        },
        "checkpoint_semantics": "atomic JSON replacement; sleep time is excluded from active_seconds",
        "last_result": results[-1] if results else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=28_800.0)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    args = parser.parse_args()
    report = run(args.output, duration_seconds=args.duration_seconds, interval_seconds=args.interval_seconds)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
