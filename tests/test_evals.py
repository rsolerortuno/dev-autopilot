from __future__ import annotations

import sys

from dev_autopilot.evals.harness import EvalResult, _render, load_tasks, run_task


def test_dataset_has_thirty_tasks_and_three_categories() -> None:
    tasks = load_tasks()
    assert len(tasks) == 30
    assert {task.category for task in tasks} == {"scientific", "software", "data"}
    assert {task.split for task in tasks} == {"train", "holdout"}


def test_harness_accepts_solution_and_isolates_repo() -> None:
    task = load_tasks()[0]
    command = [sys.executable, "-c", "from pathlib import Path; Path('answer.txt').write_text('2')"]
    result = run_task(task, command)
    assert result.success is True
    assert result.cost is None and result.cost_status == "unknown"


def test_harness_reports_timeout_and_escapes_html() -> None:
    task = load_tasks()[0]
    command = [sys.executable, "-c", "import time; time.sleep(1)"]
    result = run_task(task, command, timeout=0.01)
    assert result.timed_out is True and result.success is False
    rendered = _render([EvalResult("<x>", "data", "holdout", False, 0.1, None, "unknown", False, 1, "<bad>")])
    assert "&lt;x&gt;" in rendered and "&lt;bad&gt;" in rendered


def test_harness_records_missing_output_and_nonzero() -> None:
    task = load_tasks()[0]
    missing = run_task(task, [sys.executable, "-c", "pass"])
    failed = run_task(task, [sys.executable, "-c", "raise SystemExit(2)"])
    assert missing.success is False and missing.timed_out is False
    assert failed.success is False and failed.return_code == 2
