import json
from importlib.resources import files
from pathlib import Path

import pytest

from dev_autopilot.config import (
    export_job_specification_schema,
    job_specification_schema,
    load_job_configuration,
    load_job_configuration_text,
)
from dev_autopilot.errors import ConfigurationError

VALID_YAML = """
name: docs
objective: Improve documentation
repository: /work/project
allowed_paths:
  - kind: file
    path: README.md
  - kind: tree
    path: examples/html_reports
  - kind: glob
    pattern: tests/test_html_*.py
test_commands:
  baseline: pytest tests/unit
  fast: pytest tests/unit/test_html.py
  final: pytest
"""


def test_valid_yaml_loads_from_text_and_file(tmp_path: Path) -> None:
    path = tmp_path / "job.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")

    from_text = load_job_configuration_text(VALID_YAML)
    from_file = load_job_configuration(path)

    assert from_file == from_text
    assert from_file.name == "docs"


def test_malformed_yaml_is_actionable() -> None:
    with pytest.raises(ConfigurationError, match=r"malformed YAML.*line"):
        load_job_configuration_text("name: [broken", source="job.yaml")


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (VALID_YAML + "\nunknown: true\n", "unknown"),
        ("- not\n- a\n- mapping\n", "expected a YAML mapping"),
        ("", "expected a YAML mapping"),
    ],
)
def test_invalid_document_fails_closed(yaml_text: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        load_job_configuration_text(yaml_text)


@pytest.mark.parametrize("phase", ["baseline", "fast", "final"])
def test_each_test_command_is_required_and_non_empty(phase: str) -> None:
    command_lines = {
        "baseline": "  baseline: pytest tests/unit",
        "fast": "  fast: pytest tests/unit/test_html.py",
        "final": "  final: pytest",
    }
    invalid = VALID_YAML.replace(command_lines[phase], f"  {phase}: '   '")

    with pytest.raises(ConfigurationError, match=phase):
        load_job_configuration_text(invalid)


def test_configuration_identity_ignores_yaml_key_order() -> None:
    reordered = """
test_commands:
  final: pytest
  fast: pytest tests/unit/test_html.py
  baseline: pytest tests/unit
allowed_paths:
  - path: README.md
    kind: file
  - path: examples/html_reports
    kind: tree
  - pattern: tests/test_html_*.py
    kind: glob
repository: /work/project
objective: Improve documentation
name: docs
"""
    first = load_job_configuration_text(VALID_YAML)
    second = load_job_configuration_text(reordered)

    assert first.configuration_id == second.configuration_id


def test_generated_schema_can_be_exported(tmp_path: Path) -> None:
    schema = job_specification_schema()
    destination = tmp_path / "job.schema.json"

    export_job_specification_schema(destination)

    assert schema["title"] == "JobSpecification"
    assert '"additionalProperties": false' in destination.read_text(encoding="utf-8")


def test_packaged_schema_matches_generated_schema() -> None:
    schema_resource = files("dev_autopilot").joinpath("job-specification.schema.json")

    assert json.loads(schema_resource.read_text(encoding="utf-8")) == (
        job_specification_schema()
    )
