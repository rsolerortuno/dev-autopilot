# Validation record

Executed offline on Python 3.13.5:

- `pytest -q`: 43 passed;
- existing `main` contract, configuration and path-rule behavior included;
- `python -m compileall -q src tests`: passed;
- editable install with `--no-build-isolation`: passed;
- CLI smoke flow `init -> doctor -> start --fake -> approve -> archive`: passed;
- terminal state from the smoke run: `READY_FOR_HUMAN_REVIEW`.

`ruff`, `mypy` and `python -m build` were not available in the isolated execution
environment. CI is configured to run all three after dependencies are installed.
