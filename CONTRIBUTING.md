# Contributing

Use Python 3.11 or newer and work in an isolated branch or worktree.

```bash
python -m pip install -e '.[dev,drive]'
pytest --cov=dev_autopilot --cov-fail-under=80 -q
ruff check .
ruff format --check .
mypy
python -m build --no-isolation
```

Changes to a public contract must update its Pydantic model, generated JSON
schema, tests, README, and changelog. Safety changes need a regression test that
fails under the previous behavior. Drive and Colab tests must be deterministic
and must not require live credentials.

Do not commit caches, credentials, generated datasets, private run databases, or
large scientific artifacts.
