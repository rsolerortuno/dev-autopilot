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


def test_code_dataset_is_immutable_shape_and_broken_candidate_fails() -> None:
    tasks = load_tasks(dataset="tasks-code-v1")
    assert len(tasks) == 30
    assert {task.category for task in tasks} == {"numerical", "data", "software"}
    assert {task.split for task in tasks} == {"train", "holdout"}
    result = run_task(tasks[0], [sys.executable, "-c", "pass"])
    assert result.success is False


def test_code_dataset_known_solution_passes_hidden_cases(tmp_path) -> None:
    task = load_tasks(dataset="tasks-code-v1")[0]
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('solution.py').write_text('def solve(inputs):\\n    return sum(inputs)/len(inputs)\\n')",
    ]
    result = run_task(task, command)
    assert result.success is True


def test_code_oracle_timeout_is_failure() -> None:
    task = load_tasks(dataset="tasks-code-v1")[0]
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('solution.py').write_text('def solve(inputs):\\n    while True: pass\\n')",
    ]
    result = run_task(task, command)
    assert result.success is False


def test_candidate_output_flood_and_missing_executable_are_failures() -> None:
    task = load_tasks()[0]
    flood = run_task(task, [sys.executable, "-c", "import os; os.write(1, b'x' * 2097152)"], timeout=5)
    assert not flood.success and flood.error == "candidate output limit exceeded"
    missing = run_task(task, ["dev-autopilot-no-such-executable"])
    assert not missing.success and missing.return_code is None
