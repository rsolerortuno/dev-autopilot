# Validation

## Local, offline validation

Run from a development environment with `.[dev]` installed:

```bash
pytest -q
ruff check .
ruff format --check .
mypy
python -m build --no-isolation
python -m venv /tmp/dev-autopilot-wheel
/tmp/dev-autopilot-wheel/bin/pip install dist/*.whl
/tmp/dev-autopilot-wheel/bin/python -c 'import dev_autopilot'
git diff --check
```

The test suite uses fake adapters and does not call live Codex, AGY, or Claude.

## CI validation

The CI matrix runs the same pytest, Ruff check, Ruff format check, strict mypy,
package build, and fresh-wheel import smoke test on Python 3.11, 3.12, and 3.13.
