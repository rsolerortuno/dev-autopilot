# Dev Autopilot

Dev Autopilot is a future persistent development orchestrator. This first
package slice defines only immutable domain contracts and validated job
configuration; it does not run agents, access a network, or persist state.

## Job configuration

The YAML contract requires a name, objective, repository identifier, explicit
allowed-path rules, and separate test commands:

```yaml
name: documentation-refresh
objective: Refresh the project documentation
repository: /workspace/example
allowed_paths:
  - kind: file
    path: README.md
  - kind: tree
    path: docs
  - kind: glob
    pattern: tests/test_docs_*.py
test_commands:
  baseline: pytest tests/unit
  fast: pytest tests/unit/test_docs.py
  final: pytest
```

A `file` rule matches exactly one path. A `tree` rule matches the named
directory and all descendants. A `glob` is anchored at the repository root:
`*` and `?` do not cross `/`, while `**` does. Paths always use `/`, must be
relative, and cannot contain `..`. Unknown configuration fields are rejected.

Load a configuration and obtain its deterministic identity:

```python
from dev_autopilot import load_job_configuration

job = load_job_configuration("job.yaml")
print(job.configuration_id)
```

All models support `to_dict`, `to_json`, `from_dict`, and `from_json`.
`dev_autopilot.config.job_specification_schema()` returns the JSON Schema, and
`export_job_specification_schema(path)` writes it deterministically. The
distribution also contains the generated `job-specification.schema.json`.

## Development

The supported Python versions are 3.11 and newer. Verification is entirely
offline once the development dependencies are present:

```console
pytest
ruff check .
mypy
python -m build --no-isolation
```
