import json
from importlib.resources import files

from dev_autopilot.config import job_specification_schema
from dev_autopilot.project import ProjectCharter
from dev_autopilot.worker.job import WorkerJob


def _packaged(name: str) -> dict:
    return json.loads(files("dev_autopilot").joinpath(name).read_text(encoding="utf-8"))


def test_packaged_schemas_match_contract_models() -> None:
    expected = {
        "job-specification.schema.json": job_specification_schema(),
        "project-charter.schema.json": ProjectCharter.model_json_schema(mode="validation", ref_template="#/$defs/{model}"),
        "worker-job.schema.json": WorkerJob.model_json_schema(mode="validation", ref_template="#/$defs/{model}"),
    }
    for filename, schema in expected.items():
        assert _packaged(filename) == schema
