import json

import pytest
from scripts.soak_offline import _normal_wait_observed, run


def test_short_soak_exercises_all_injected_invariants(tmp_path):
    report = run(tmp_path / "soak", duration_seconds=0.1, interval_seconds=0.01)
    assert report["status"] == "success"
    assert report["cycles"] >= 1
    assert report["failures"] == []
    assert report["metrics"]["active_seconds"] > 0
    assert report["last_result"]["queue"] == {
        "lease_expiration_recovered": True,
        "stale_owner_denied": True,
        "reopened_completed": True,
        "crash_after_publish_injected": True,
        "crash_terminal_survived": True,
    }
    assert report["last_result"]["budget"] == {"reservation_persisted": True, "budget_denied": True}
    checkpoint = json.loads((tmp_path / "soak" / "soak-checkpoint.json").read_text())
    assert checkpoint["cycles"] == report["cycles"]
    assert checkpoint["status"] == "success"
    assert checkpoint["metrics"]["observed_seconds"] >= checkpoint["metrics"]["target_seconds"]


def test_suspend_gap_is_not_counted_as_observed_work():
    assert _normal_wait_observed(0.2, 0.1) == 0.1
    assert _normal_wait_observed(61.0, 5.0) == 0.0


def test_soak_refuses_existing_output_state(tmp_path):
    output = tmp_path / "soak"
    output.mkdir()
    (output / "soak-checkpoint.json").write_text("{}")
    with pytest.raises(ValueError, match="new empty directory"):
        run(output, duration_seconds=0.1, interval_seconds=0.01)
