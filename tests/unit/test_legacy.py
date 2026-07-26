from __future__ import annotations

from datetime import UTC, datetime

from dev_autopilot.db import SQLiteStore
from dev_autopilot.errors import ErrorClass
from dev_autopilot.legacy import migrate_legacy_checkpoint, read_legacy_checkpoint
from dev_autopilot.states import WorkflowState


def test_read_shell_checkpoint(tmp_path) -> None:
    path = tmp_path / "checkpoint.env"
    path.write_text("STATE=PAUSED_QUOTA\nRETRY_COUNT=2\n", encoding="utf-8")
    assert read_legacy_checkpoint(path)["state"] == "PAUSED_QUOTA"


def test_migrate_paused_retry(tmp_path, job) -> None:
    path = tmp_path / "checkpoint.json"
    next_attempt = datetime(2026, 1, 2, tzinfo=UTC)
    path.write_text(
        "{"
        + '"state":"PAUSED_QUOTA",'
        + '"resume_state":"IMPLEMENTATION",'
        + '"retry_owner":"codex",'
        + '"retry_count":2,'
        + '"error_class":"RETRYABLE_QUOTA",'
        + f'"next_attempt_at":"{next_attempt.isoformat()}",'
        + '"stop_reason":"quota"'
        + "}",
        encoding="utf-8",
    )
    store = SQLiteStore(tmp_path / "state.sqlite3")
    run = migrate_legacy_checkpoint(store, job, path)
    assert run.state is WorkflowState.PAUSED_QUOTA
    assert run.resume_state is WorkflowState.IMPLEMENTATION
    retry = store.get_retry(run.run_id, "codex")
    assert retry is not None
    assert retry.count == 2
    assert retry.error_class is ErrorClass.RETRYABLE_QUOTA


def test_migrate_classified_failure(tmp_path, job) -> None:
    path = tmp_path / "checkpoint.env"
    path.write_text(
        "STATE=FAILED\nFAILURE_STATE=FAST_TESTS\nERROR_CLASS=TEST_FAILURE\nSTOP_REASON=legacy tests failed\n",
        encoding="utf-8",
    )
    run = migrate_legacy_checkpoint(SQLiteStore(tmp_path / "state.sqlite3"), job, path)
    assert run.state is WorkflowState.FAILED
    assert run.failure_class is ErrorClass.TEST_FAILURE
    assert run.stop_reason == "legacy tests failed"
