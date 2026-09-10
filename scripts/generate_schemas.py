"""Regenerate packaged JSON schemas from their Pydantic contract models."""

from __future__ import annotations

import json
from pathlib import Path

from dev_autopilot.config import job_specification_schema
from dev_autopilot.project import ProjectCharter
from dev_autopilot.worker.job import WorkerJob

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    schemas = {
        "src/dev_autopilot/job-specification.schema.json": job_specification_schema(),
        "src/dev_autopilot/project-charter.schema.json": ProjectCharter.model_json_schema(
            mode="validation", ref_template="#/$defs/{model}"
        ),
        "src/dev_autopilot/worker-job.schema.json": WorkerJob.model_json_schema(
            mode="validation", ref_template="#/$defs/{model}"
        ),
    }
    for relative, schema in schemas.items():
        (ROOT / relative).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
