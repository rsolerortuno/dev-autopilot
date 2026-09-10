from __future__ import annotations

import hashlib
import html
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

MAX_CAPTURE_BYTES = 1_048_576


@dataclass(frozen=True)
class Task:
    id: str
    category: str
    split: str
    prompt: str
    files: dict[str, str]
    oracle: dict[str, Any]


@dataclass(frozen=True)
class EvalResult:
    task_id: str
    category: str
    split: str
    success: bool
    latency_seconds: float
    cost: float | None
    cost_status: str
    timed_out: bool
    return_code: int | None
    error: str | None
    repetition_id: int = 0


def load_tasks(path: Path | None = None, dataset: str = "tasks-v1") -> list[Task]:
    source = path or Path(__file__).with_name("data").joinpath(f"{dataset}.json")
    raw = json.loads(source.read_text(encoding="utf-8"))
    return [Task(**item) for item in raw["tasks"]]


def _oracle(task: Task, task_dir: Path) -> bool:
    kind = task.oracle["kind"]
    output = task_dir / task.oracle.get("path", "answer.txt")
    if output.is_symlink() or not output.resolve().is_relative_to(task_dir):
        return False
    if kind == "text":
        return bool(output.read_text(encoding="utf-8").strip() == task.oracle["value"])
    if kind == "json":
        return bool(json.loads(output.read_text(encoding="utf-8")) == task.oracle["value"])
    if kind == "python_function":
        module = task_dir / task.oracle["module"]
        if module.is_symlink() or not module.resolve().is_relative_to(task_dir):
            return False
        payload = json.dumps([case[0] for case in task.oracle["cases"]], separators=(",", ":"))
        code = (
            "import importlib.util,json,sys; "
            "s=importlib.util.spec_from_file_location('candidate',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "f=getattr(m,sys.argv[2]); cases=json.loads(sys.stdin.read()); "
            "print(json.dumps([f(x) for x in cases]))"
        )
        with tempfile.TemporaryFile() as inputs, tempfile.TemporaryFile() as outputs:
            inputs.write(payload.encode())
            inputs.seek(0)
            check = subprocess.Popen(
                [sys.executable, "-I", "-c", code, str(module), task.oracle["function"]],
                cwd=task_dir,
                stdin=inputs,
                stdout=outputs,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env={key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}},
            )
            deadline = time.monotonic() + 2
            while check.poll() is None:
                if time.monotonic() >= deadline or os.fstat(outputs.fileno()).st_size > MAX_CAPTURE_BYTES:
                    if os.name == "posix":
                        os.killpg(check.pid, getattr(signal, "SIGKILL", 9))  # type: ignore[attr-defined]
                    else:
                        check.kill()
                    check.wait()
                    return False
                time.sleep(0.01)
            if os.fstat(outputs.fileno()).st_size > MAX_CAPTURE_BYTES:
                return False
            outputs.seek(0)
            actual = json.loads(outputs.read(MAX_CAPTURE_BYTES))
            return check.returncode == 0 and actual == [case[1] for case in task.oracle["cases"]]
    raise ValueError(f"unsupported oracle kind: {kind}")


def run_task(task: Task, command: list[str], timeout: float = 10.0) -> EvalResult:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"dev-autopilot-eval-{task.id}-") as raw_dir:
        task_dir = Path(raw_dir).resolve()
        for relative, content in task.files.items():
            relative_path = Path(relative)
            target = task_dir / relative_path
            if relative_path.is_absolute() or not target.resolve().is_relative_to(task_dir):
                return EvalResult(
                    task.id, task.category, task.split, False, 0.0, None, "unknown", False, None, "unsafe fixture path"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (task_dir / "TASK.md").write_text(task.prompt, encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "DEV_AUTOPILOT_TASK_ID": task.id}
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    command, cwd=task_dir, env=env, stdout=stdout_file, stderr=stderr_file, start_new_session=True
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        os.killpg(process.pid, getattr(signal, "SIGKILL", 9))  # type: ignore[attr-defined]
                    else:
                        process.kill()
                    process.wait()
                    raise

                def _tail(stream: Any) -> str:
                    stream.seek(0, 2)
                    end = stream.tell()
                    stream.seek(max(0, end - MAX_CAPTURE_BYTES))
                    return cast(bytes, stream.read(MAX_CAPTURE_BYTES)).decode("utf-8", errors="replace")

                stderr = _tail(stderr_file)
                try:
                    oracle_ok = _oracle(task, task_dir)
                except Exception:
                    oracle_ok = False
                success = process.returncode == 0 and oracle_ok
                error = None if success else (stderr[-500:] or "oracle rejected candidate")
                return_code: int | None = process.returncode
                timed_out = False
        except subprocess.TimeoutExpired:
            success, error, return_code, timed_out = False, "candidate timed out", None, True
    return EvalResult(
        task.id,
        task.category,
        task.split,
        success,
        time.perf_counter() - started,
        None,
        "unknown",
        timed_out,
        return_code,
        error,
    )


def _render(results: list[EvalResult]) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(r.task_id)}</td><td>{html.escape(r.category)}</td><td>{r.success}</td>"
        f"<td>{r.latency_seconds:.3f}</td><td>{html.escape(r.cost_status)}</td><td>{html.escape(r.error or '')}</td></tr>"
        for r in results
    )
    header = "<tr><th>Task</th><th>Category</th><th>Success</th><th>Latency (s)</th><th>Cost</th><th>Error</th></tr>"
    return f"<html><body><h1>Evaluation report</h1><table>{header}{rows}</table></body></html>"


def run_evaluation(
    command: list[str],
    *,
    split: str = "holdout",
    repetitions: int = 1,
    timeout: float = 10.0,
    output: Path | None = None,
    dataset: str = "tasks-v1",
    config_label: str = "default",
) -> list[EvalResult]:
    if repetitions < 1 or timeout <= 0 or split not in {"train", "holdout", "all"}:
        raise ValueError("positive repetitions/timeout and a valid split are required")
    tasks = [t for t in load_tasks(dataset=dataset) if split == "all" or t.split == split]
    results = [
        replace(run_task(task, command, timeout), repetition_id=repetition)
        for repetition in range(repetitions)
        for task in tasks
    ]
    if output:
        output.mkdir(parents=True, exist_ok=True)
        payload = {
            "dataset": dataset,
            "dataset_sha256": hashlib.sha256(
                Path(__file__).with_name("data").joinpath(f"{dataset}.json").read_bytes()
            ).hexdigest(),
            "config_label": config_label,
            "split": split,
            "repetitions": repetitions,
            "repetition_ids": list(range(repetitions)),
            "summary": {"successful": sum(r.success for r in results), "total": len(results)},
            "cost": {"status": "unknown", "reason": "candidate protocol reports no token billing"},
            "results": [r.__dict__ for r in results],
        }
        (output / "report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (output / "report.html").write_text(_render(results), encoding="utf-8")
    return results
