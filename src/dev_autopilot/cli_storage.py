"""CLI handlers for M04 storage, M05 workers and verified review bundles."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path
from typing import Any
from uuid import uuid4

from dev_autopilot.bundle import verify_bundle
from dev_autopilot.storage.backend import LocalStorageBackend
from dev_autopilot.storage.reassembler import load_manifest, reassemble_from_prefix
from dev_autopilot.storage.splitter import split_file, split_object, verify_parts
from dev_autopilot.worker.coordination import SQLiteCoordinator
from dev_autopilot.worker.job import WorkerJob
from dev_autopilot.worker.queue import DriveQueue
from dev_autopilot.worker.runner import poll_loop


def _backend(args: argparse.Namespace) -> Any:
    if getattr(args, "drive_folder", None):
        from google.auth import default

        from dev_autopilot.storage.drive_backend import DriveStorageBackend

        credentials, _ = default()
        return DriveStorageBackend(args.drive_folder, credentials)
    return LocalStorageBackend(args.store)


def _add_backend_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store", type=Path, default=Path(".dev-autopilot/store"))
    parser.add_argument("--drive-folder", default=None, help="Drive root folder ID; uses ambient Google credentials")
    parser.add_argument("--queue-coordinator", type=Path, help="shared single-host SQLite coordination DB for all queue writers")
    parser.add_argument("--single-writer", action="store_true", help="acknowledge only one host writes the remote queue")


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    drive = sub.add_parser("drive", help="stream, split, reassemble and verify large files")
    drive_sub = drive.add_subparsers(dest="drive_command", required=True)

    split = drive_sub.add_parser("split", help="split a local file into a local or Drive backend")
    split.add_argument("source", type=Path)
    split.add_argument("key_prefix")
    _add_backend_options(split)
    split.add_argument("--profile", default=None)
    split.add_argument("--part-size-bytes", type=int, default=None)

    split_key = drive_sub.add_parser("split-key", help="range-stream and split an object already present in the selected backend")
    split_key.add_argument("source_key")
    split_key.add_argument("key_prefix")
    _add_backend_options(split_key)
    split_key.add_argument("--profile", default=None)
    split_key.add_argument("--part-size-bytes", type=int, default=None)

    reassemble = drive_sub.add_parser("reassemble")
    reassemble.add_argument("key_prefix")
    reassemble.add_argument("destination", type=Path)
    _add_backend_options(reassemble)

    verify_parts_parser = drive_sub.add_parser("verify")
    verify_parts_parser.add_argument("key_prefix")
    _add_backend_options(verify_parts_parser)

    colab = sub.add_parser("colab", help="submit and execute fenced jobs through a Drive-compatible queue")
    colab_sub = colab.add_subparsers(dest="colab_command", required=True)

    submit = colab_sub.add_parser("submit")
    submit.add_argument("job", type=Path, help="path to a WorkerJob JSON file")
    _add_backend_options(submit)

    run = colab_sub.add_parser("run")
    run.add_argument("resource_class")
    run.add_argument("owner_token", nargs="?", default=None)
    _add_backend_options(run)
    run.add_argument("--workdir", type=Path, default=Path(".dev-autopilot/work"))
    run.add_argument("--max-jobs", type=int, default=1)

    status = colab_sub.add_parser("status")
    status.add_argument("resource_class", nargs="?", default=None)
    status.add_argument("--job-id", default=None)
    _add_backend_options(status)

    watchdog = colab_sub.add_parser("watchdog")
    _add_backend_options(watchdog)

    ready = colab_sub.add_parser("register-worker")
    ready.add_argument("resource_class")
    ready.add_argument("owner_token", nargs="?", default=None)
    ready.add_argument("--ttl-seconds", type=int, default=300)
    _add_backend_options(ready)

    bundle = sub.add_parser("bundle", help="verify a review bundle's complete integrity manifest")
    bundle_sub = bundle.add_subparsers(dest="bundle_command", required=True)
    verify = bundle_sub.add_parser("verify")
    verify.add_argument("bundle_dir", type=Path)


def _owner_token(value: str | None) -> str:
    return value or f"{socket.gethostname()}-{uuid4().hex[:12]}"


def _split_payload(report: Any, source: str) -> dict[str, object]:
    return {
        "source": source,
        "parts": len(report.manifest.parts),
        "written": report.parts_written,
        "reused": report.parts_reused,
        "whole_sha256": report.manifest.whole_sha256,
        "total_size": report.manifest.total_size,
    }


def handle(args: argparse.Namespace) -> int | None:
    """Handle a storage/worker/bundle command, or return ``None``."""
    if args.command == "drive":
        backend = _backend(args)
        if args.drive_command == "split":
            report = split_file(
                args.source,
                backend,
                key_prefix=args.key_prefix,
                profile=args.profile,
                part_size_bytes=args.part_size_bytes,
            )
            print(json.dumps(_split_payload(report, str(args.source)), indent=2))
            return 0
        if args.drive_command == "split-key":
            report = split_object(
                backend,
                args.source_key,
                backend,
                key_prefix=args.key_prefix,
                profile=args.profile,
                part_size_bytes=args.part_size_bytes,
            )
            print(json.dumps(_split_payload(report, args.source_key), indent=2))
            return 0
        if args.drive_command == "reassemble":
            digest = reassemble_from_prefix(backend, key_prefix=args.key_prefix, destination=args.destination)
            print(json.dumps({"destination": str(args.destination), "whole_sha256": digest}, indent=2))
            return 0
        if args.drive_command == "verify":
            manifest = load_manifest(backend, key_prefix=args.key_prefix)
            problems = verify_parts(backend, manifest, key_prefix=args.key_prefix)
            print(
                json.dumps(
                    {
                        "key_prefix": args.key_prefix,
                        "valid": not problems,
                        "problems": problems,
                        "whole_sha256": manifest.whole_sha256,
                    },
                    indent=2,
                )
            )
            return 0 if not problems else 1

    if args.command == "colab":
        backend = _backend(args)
        queue = DriveQueue(
            backend,
            coordinator=SQLiteCoordinator(args.queue_coordinator) if args.queue_coordinator else None,
            single_writer=args.single_writer,
        )
        if args.colab_command == "submit":
            job = WorkerJob.from_json(Path(args.job).read_text(encoding="utf-8"))
            queue.submit(job)
            print(json.dumps({"submitted": job.job_id, "resource_class": job.resource_class.value}, indent=2))
            return 0
        if args.colab_command == "run":
            owner = _owner_token(args.owner_token)
            results = poll_loop(
                queue,
                resource_class=args.resource_class,
                owner_token=owner,
                workdir=args.workdir,
                max_jobs=args.max_jobs,
            )
            print(
                json.dumps(
                    {
                        "owner_token": owner,
                        "results": [
                            {
                                "job_id": result.job_id,
                                "completed": result.completed,
                                "exit_code": result.exit_code,
                                "summary": result.summary,
                            }
                            for result in results
                        ],
                    },
                    indent=2,
                )
            )
            return 0 if all(result.completed for result in results) else 1
        if args.colab_command == "status":
            if args.job_id:
                payload = queue.job_status(args.job_id)
                print(json.dumps({"job_id": args.job_id, "record": payload}, indent=2, sort_keys=True))
                return 0 if payload is not None else 1
            resources = [args.resource_class] if args.resource_class else ["cpu", "high_ram", "gpu", "tpu", "storage"]
            payload = {
                "queued": {resource: [Path(key).stem for key in queue.list_queued(resource)] for resource in resources},
                "running": [Path(key).stem for key in queue.list_state("running")],
                "completed": [Path(key).stem for key in queue.list_state("completed")],
                "failed": [Path(key).stem for key in queue.list_state("failed")],
                "blocked": [Path(key).stem for key in queue.list_state("blocked")],
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.colab_command == "watchdog":
            resumed = queue.resume_cleared_blockers()
            reclaimed = queue.reclaim_expired()
            print(json.dumps({"reclaimed": reclaimed, "resumed": resumed}, indent=2))
            return 0
        if args.colab_command == "register-worker":
            owner = _owner_token(args.owner_token)
            queue.register_worker(args.resource_class, owner, ttl_seconds=args.ttl_seconds)
            print(json.dumps({"owner_token": owner, "resource_class": args.resource_class}, indent=2))
            return 0

    if args.command == "bundle" and args.bundle_command == "verify":
        problems = verify_bundle(args.bundle_dir)
        if problems:
            for problem in problems:
                print(f"FAIL {problem}")
            return 1
        print("OK bundle manifest, report and folded digest verified")
        return 0

    return None
