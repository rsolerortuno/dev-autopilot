"""YAML loading and JSON Schema export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from dev_autopilot.errors import ConfigurationError
from dev_autopilot.models import JobSpecification


def load_job_configuration(path: str | Path) -> JobSpecification:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"cannot read job configuration {source}: {exc}") from exc
    return load_job_configuration_text(text, source=str(source))


def load_job_configuration_text(text: str, *, source: str = "<configuration>") -> JobSpecification:
    try:
        value: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = "" if mark is None else f" at line {mark.line + 1}, column {mark.column + 1}"
        raise ConfigurationError(f"malformed YAML in {source}{location}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"invalid job configuration in {source}: expected a YAML mapping")
    try:
        return JobSpecification.model_validate(value, strict=True)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors(include_url=False)
        )
        raise ConfigurationError(f"invalid job configuration in {source}: {details}") from exc


def job_specification_schema() -> dict[str, Any]:
    return JobSpecification.model_json_schema(mode="validation", ref_template="#/$defs/{model}")


def export_job_specification_schema(path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(job_specification_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
