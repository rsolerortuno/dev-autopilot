"""Integration tests for the drive/colab/bundle CLI subcommands."""

from __future__ import annotations

import os

from dev_autopilot.cli import main
from dev_autopilot.worker.job import WorkerJob


def test_drive_split_then_reassemble_via_cli(tmp_path, capsys):
    source = tmp_path / "big.bin"
    source.write_bytes(os.urandom(8192))
    store = tmp_path / "store"

    rc = main(["drive", "split", str(source), "proj/big", "--store", str(store), "--part-size-bytes", "1000"])
    assert rc == 0

    out = tmp_path / "restored.bin"
    rc = main(["drive", "reassemble", "proj/big", str(out), "--store", str(store)])
    assert rc == 0
    assert out.read_bytes() == source.read_bytes()


def test_colab_submit_and_status_via_cli(tmp_path, capsys):
    store = tmp_path / "store"
    job = WorkerJob.from_dict(
        {
            "job_id": "J-100",
            "project_id": "targetintel",
            "resource_class": "cpu",
            "entrypoint": ["python", "-c", "print(1)"],
            "output_prefix": "projects/targetintel/out/J-100",
        }
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(job.to_json(), encoding="utf-8")

    rc = main(["colab", "submit", str(job_path), "--store", str(store)])
    assert rc == 0

    capsys.readouterr()
    rc = main(["colab", "status", "cpu", "--store", str(store)])
    assert rc == 0
    assert "J-100" in capsys.readouterr().out


def test_bundle_verify_via_cli(tmp_path, capsys):
    from dev_autopilot.bundle import BundleInputs, write_bundle

    inputs = BundleInputs(
        project_yaml="project:\n  title: demo\n",
        baseline={"tree_sha256": "0" * 64},
        final_patch="diff\n",
        milestones=[],
        findings=[],
        tests={},
        scientific_gates=[],
    )
    write_bundle(inputs, tmp_path / "bundle")
    rc = main(["bundle", "verify", str(tmp_path / "bundle")])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_drive_split_key_and_verify_via_cli(tmp_path, capsys):
    from dev_autopilot.storage.backend import LocalStorageBackend

    store = tmp_path / "store"
    backend = LocalStorageBackend(store)
    backend.put_bytes("raw/input.bin", b"remote-data" * 300)
    rc = main(
        [
            "drive",
            "split-key",
            "raw/input.bin",
            "parts/input",
            "--store",
            str(store),
            "--part-size-bytes",
            "257",
        ]
    )
    assert rc == 0
    capsys.readouterr()
    rc = main(["drive", "verify", "parts/input", "--store", str(store)])
    assert rc == 0
    assert '"valid": true' in capsys.readouterr().out


def test_colab_status_reports_terminal_states(tmp_path, capsys):
    store = tmp_path / "store"
    job = WorkerJob.from_dict(
        {
            "job_id": "J-200",
            "project_id": "targetintel",
            "resource_class": "cpu",
            "entrypoint": ["python", "-c", "print(1)"],
            "output_prefix": "projects/targetintel/out/J-200",
        }
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(job.to_json(), encoding="utf-8")
    assert main(["colab", "submit", str(job_path), "--store", str(store)]) == 0
    capsys.readouterr()
    assert main(["colab", "status", "--job-id", "J-200", "--store", str(store)]) == 0
    assert '"status": "QUEUED"' in capsys.readouterr().out
