from __future__ import annotations

import json

import pytest

from dev_autopilot.config import (
    export_job_specification_schema,
    load_job_configuration_text,
)
from dev_autopilot.errors import ConfigurationError


def test_yaml_load_and_schema_export(repository, tmp_path) -> None:
    job = load_job_configuration_text(
        f"""
name: demo
objective: do work
repository: {repository}
allowed_paths:
  - kind: tree
    path: src
test_commands:
  baseline: pytest -q
  fast: pytest -q
  final: pytest -q
"""
    )
    assert job.allows_path("src/a.py")
    target = tmp_path / "schema.json"
    export_job_specification_schema(target)
    schema = json.loads(target.read_text())
    assert schema["title"] == "JobSpecification"


@pytest.mark.parametrize("text", ["[1, 2]", "name: [", "name: x\nunknown: true"])
def test_invalid_configuration_is_actionable(text: str) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_job_configuration_text(text)
    assert str(error.value)
