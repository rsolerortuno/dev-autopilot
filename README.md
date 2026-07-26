# Dev Autopilot

Dev Autopilot is a persistent, auditable development orchestrator for scientific
software. It coordinates an implementation agent, an adversarial AGY audit and
an independent Claude review, while SQLite preserves every state transition,
retry and decision across process restarts.

Version 0.1.0 stops at `READY_FOR_HUMAN_REVIEW`. It never stages, commits,
pushes, tags or merges product code.

## What works

- validated YAML configuration with explicit file, tree and glob permissions;
- versioned SQLite state, append-only events and exclusive expiring run locks;
- legal fail-closed transitions and persistent pause/resume state;
- deterministic idempotency hashes and diff-keyed AGY audit caching;
- independent retry counters for Codex, AGY and Claude;
- deterministic jitter, explicit quota-reset support and restart recovery;
- fake adapters for a complete offline workflow;
- generic real-agent bridge using bounded context/output files;
- scope, final-test, context-size and git-metadata safety gates;
- review/correction cycles ending at human review;
- import of legacy Bash checkpoints.

## Install

```bash
python -m pip install -e '.[dev]'
dev-autopilot doctor
```

Python 3.11 or newer is required.

## Offline smoke run

```bash
mkdir -p /tmp/autopilot-demo/repo
dev-autopilot init /tmp/autopilot-demo/job.yaml \
  --repository /tmp/autopilot-demo/repo
dev-autopilot --db /tmp/autopilot-demo/state.sqlite3 \
  start /tmp/autopilot-demo/job.yaml --fake
```

The fake run executes the same persistence, state, gate, audit and review code as
a real run and reaches `READY_FOR_HUMAN_REVIEW` without network access.

## CLI

```text
dev-autopilot doctor [--job JOB]
dev-autopilot init PATH [--repository REPOSITORY]
dev-autopilot start JOB [--fake]
dev-autopilot status RUN_ID [--json]
dev-autopilot events RUN_ID
dev-autopilot watch RUN_ID JOB [--fake]
dev-autopilot pause RUN_ID
dev-autopilot resume RUN_ID JOB [--fake]
dev-autopilot cancel RUN_ID
dev-autopilot approve RUN_ID
dev-autopilot archive RUN_ID
dev-autopilot migrate-legacy JOB CHECKPOINT
```

`watch` automatically resumes quota-paused runs after their durable retry
deadline. Human-decision pauses remain blocked until explicitly resumed.

## Real agent protocol

A configured agent command receives:

- `DEV_AUTOPILOT_CONTEXT_FILE`: bounded JSON task and review context;
- `DEV_AUTOPILOT_OUTPUT_FILE`: path where the process must write JSON;
- `DEV_AUTOPILOT_REPOSITORY`: product repository path.

The command must exit zero and create the output file. AGY and Claude output is
strictly validated. `python -m dev_autopilot.bridge --env VARIABLE` can adapt a
subscription CLI that accepts a prompt on stdin and emits a JSON object on
stdout.

Real execution is disabled unless the job explicitly sets
`gates.allow_network: true`. Git metadata changes are rejected while
`gates.allow_git_writes` is false.

## TargetIntel-IO

`examples/targetintel-io.job.yaml` is prepared for the existing checkout at
`/home/rso12/projects/TargetIntel-IO`. Set the exact local subscription commands:

```bash
export DEV_AUTOPILOT_CODEX_COMMAND='YOUR_CODEX_COMMAND'
export DEV_AUTOPILOT_AGY_COMMAND='YOUR_AGY_COMMAND'
export DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND='YOUR_CLAUDE_COMMAND'
export DEV_AUTOPILOT_ROOT="$HOME/projects/dev-autopilot"
./scripts/run_targetintel_issue.sh
```

The three commands should return only the JSON requested in their supplied
context. The profile authorizes common TargetIntel source, test, configuration,
documentation and script paths; narrow them further for each issue.

## Workflow

```text
CREATED -> PLAN_VALIDATION -> BASELINE_VALIDATION -> IMPLEMENTATION
        -> SCOPE_VALIDATION -> FAST_TESTS -> AGY_AUDIT -> CLAUDE_REVIEW
        -> [CORRECTION -> SCOPE_VALIDATION ...] -> FINAL_TESTS
        -> READY_FOR_HUMAN_REVIEW
```

Retryable agent failures enter `PAUSED_QUOTA` with the exact previous phase in
`resume_state`. Scientific ambiguity enters `PAUSED_HUMAN_DECISION`. All failures
store a stable class and a non-empty stop reason.

See `docs/architecture.md` for the component and persistence design.

## Validation

```bash
pytest -q
ruff check .
mypy
python -m build --no-isolation
```

The integration suite uses no network and no live agent subscription.
