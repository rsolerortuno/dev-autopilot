import json

from dev_autopilot.demo import run
from dev_autopilot.health import check_database


def test_offline_demo_writes_verified_json_and_html(tmp_path):
    report = run(tmp_path / "demo")
    assert report["successful_bundle"] is True
    assert report["fake_agents"] is True
    assert report["provider_evaluation"] is False
    assert report["denied_path_example"]["allowed"] is False
    assert json.loads((tmp_path / "demo" / "demo-report.json").read_text())["successful_bundle"] is True
    assert "synthetic" in (tmp_path / "demo" / "demo-report.html").read_text()


def test_health_is_read_only_for_missing_database(tmp_path):
    database = tmp_path / "missing.sqlite3"
    healthy, detail = check_database(database)
    assert healthy is False
    assert "does not exist" in detail
    assert not database.exists()


def test_health_accepts_demo_database(tmp_path):
    run(tmp_path / "demo")
    healthy, detail = check_database(tmp_path / "demo" / "demo.sqlite3")
    assert healthy is True
    assert "schema_version=1" in detail
