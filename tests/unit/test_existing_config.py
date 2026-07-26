import json
from importlib.resources import files

from dev_autopilot.config import job_specification_schema, load_job_configuration_text

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


def test_existing_yaml_and_packaged_schema() -> None:
    first = load_job_configuration_text(VALID_YAML)
    second = load_job_configuration_text(
        """
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
    )
    assert first.configuration_id == second.configuration_id
    resource = files("dev_autopilot").joinpath("job-specification.schema.json")
    assert json.loads(resource.read_text(encoding="utf-8")) == job_specification_schema()
