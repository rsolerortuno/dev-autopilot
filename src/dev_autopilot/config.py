"""YAML loading and JSON Schema export for job specifications."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from dev_autopilot.errors import ConfigurationError
from dev_autopilot.models import JobSpecification


def load_job_configuration(path: str | Path) -> JobSpecification:
    """Load and validate a UTF-8 YAML job configuration file."""

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        message = f"cannot read job configuration {source}: {exc}"
        raise ConfigurationError(message) from exc
    return load_job_configuration_text(text, source=str(source))


def load_job_configuration_text(
    text: str,
    *,
    source: str = "<configuration>",
) -> JobSpecification:
    """Parse and validate a YAML job configuration document."""

    try:
        value: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        location = ""
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            location = f" at line {mark.line + 1}, column {mark.column + 1}"
        message = f"malformed YAML in {source}{location}: {exc}"
        raise ConfigurationError(message) from exc

    if not isinstance(value, dict):
        raise ConfigurationError(
            f"invalid job configuration in {source}: expected a YAML mapping"
        )
    try:
        return JobSpecification.model_validate(value, strict=True)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False)
        )
        message = f"invalid job configuration in {source}: {details}"
        raise ConfigurationError(message) from exc


def job_specification_schema() -> dict[str, Any]:
    """Return the generated JSON Schema for the YAML job contract."""

    return JobSpecification.model_json_schema(
        mode="validation",
        ref_template="#/$defs/{model}",
    )


def export_job_specification_schema(path: str | Path) -> None:
    """Write the generated JSON Schema with deterministic formatting."""

    Path(path).write_text(
        json.dumps(job_specification_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
