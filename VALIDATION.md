# Validation record — M05 / v0.5.0

Validation date: 2026-08-06
Local interpreter: CPython 3.13.5

## Executed locally

```text
pytest -q -W error::ResourceWarning
155 passed

pytest --cov=dev_autopilot --cov-report=term-missing --cov-fail-under=80 -q
155 passed; total coverage 84.08%

python -m compileall -q src tests
passed

git diff --check
passed

python -m pip wheel . --no-build-isolation --no-deps
passed

setuptools.build_meta.build_wheel / build_sdist
passed

python -m pip wheel dist/dev_autopilot-0.5.0.tar.gz --no-build-isolation --no-deps
passed
```

Additional executed smoke paths:

- fake-agent orchestration reaches `READY_FOR_HUMAN_REVIEW` only after a
  verified acceptance decision and verified bundle;
- a multi-milestone project reaches `READY_FOR_HUMAN_RELEASE` and emits one
  verified bundle per milestone;
- local split/resume/reassembly reproduces the exact source SHA-256;
- simulated Drive submit -> list -> claim -> heartbeat -> checkpoint -> complete
  succeeds;
- worker fencing prevents a stale owner from checkpointing or publishing;
- a reappearing eventually-consistent queue entry cannot replace a live lease;
- terminal and fenced worker scratch directories are removed after durable publication;
- undeclared bundle files, directories and symlinks fail verification;
- transient Drive listings retry with bounded backoff and jitter;
- a lost create response is retried by stable logical key without duplicate objects;
- expired Drive credentials refresh before long-session operations;
- heartbeat remains active during slow staging and execution;
- report and payload tampering fail bundle verification;
- invalidated P0/P1 findings block acceptance;
- project, worker-job and job-specification schemas parse as JSON;
- the Colab notebook parses as valid notebook JSON;
- the built wheel imports with all packaged schemas and CLI help available.
- the final clean source ZIP was extracted into a new directory and all 155 tests passed;
- the timeout-plus-Git-mutation security regression passed ten consecutive runs.

## CI-owned gates

The repository CI runs the following on Python 3.11, 3.12 and 3.13:

```text
pytest with coverage >= 80%
ruff check .
ruff format --check .
strict mypy
python -m build
fresh-wheel installation and import
pip check
schema and notebook validation
```

Ruff and strict mypy remain mandatory publication gates and are executed by the
Linux release script before tests, commit, push or tag creation. The isolated
artifact-building runtime used for this report did not contain those two tools,
so it does not claim to have executed them locally. The wheel and source
distribution were built locally through the same setuptools backend.

## External-provider boundary

No authenticated live Google Drive or managed Google Colab session was available
inside the build environment. Drive behavior is validated through a contract-level
simulated API, and the Colab notebook and recovery protocols are validated
offline. A final live credentialed smoke run remains an environment acceptance
check rather than an unverified release claim.
