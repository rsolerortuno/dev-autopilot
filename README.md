# Dev Autopilot

Dev Autopilot is a persistent, auditable development orchestrator for scientific
software. It coordinates an implementation agent, an adversarial AGY audit and
an independent Claude review, while SQLite preserves every state transition,
retry and decision across process restarts.

Version 0.1.1 stops at `READY_FOR_HUMAN_REVIEW`. It never stages, commits,
pushes, tags or merges product code.

In plain terms, Dev Autopilot gives independent roles a shared, durable workflow:
agents can advise, implement, and review, but deterministic gates decide whether
work may advance and a person decides scientific scope and whether to merge.

## Authority and review order

```text
AGY planning advice
    -> Codex implementation
    -> deterministic gates
    -> AGY adversarial audit
    -> independent Claude review
    -> bounded correction/re-review rounds
    -> READY_FOR_HUMAN_REVIEW
    -> human commit/PR/merge decision
```

AGY planning advice is input, not approval. Codex implements but never approves
its own work. AGY audits the produced diff adversarially and Claude independently
reviews that exact diff and may request changes. No agent can override mechanical
gates. The human retains scientific-scope and merge authority.

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

## Tutorial: a real, narrow issue

Install in the `dev-autopilot` environment, then verify the installation and the
offline workflow:

```bash
python -m pip install -e '.[dev]'
dev-autopilot doctor
mkdir -p /tmp/autopilot-demo/repo
dev-autopilot init /tmp/autopilot-demo/job.yaml --repository /tmp/autopilot-demo/repo
dev-autopilot --db /tmp/autopilot-demo/state.sqlite3 start /tmp/autopilot-demo/job.yaml --fake
```

For a real issue, first create a separate worktree outside Autopilot, then copy
and narrow a job (especially `allowed_paths`) to that one issue:

```bash
git -C /path/to/product worktree add ../product-issue -b issue-work
cp examples/targetintel-io.job.yaml /tmp/issue.job.yaml
# Edit /tmp/issue.job.yaml: repository, objective, and allowed_paths.
dev-autopilot doctor --job /tmp/issue.job.yaml
```

Use argv arrays for agents. The supplied TargetIntel profile uses the bridge to
read subscription commands from the environment:

```bash
export DEV_AUTOPILOT_CODEX_COMMAND='codex YOUR_ARGUMENTS'
export DEV_AUTOPILOT_AGY_COMMAND='agy YOUR_ARGUMENTS'
export DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND='claude YOUR_ARGUMENTS'
# Save AGY planning advice as part of the issue objective/context before start.
dev-autopilot --db /tmp/issue.sqlite3 start /tmp/issue.job.yaml
dev-autopilot --db /tmp/issue.sqlite3 status RUN_ID
dev-autopilot --db /tmp/issue.sqlite3 watch RUN_ID /tmp/issue.job.yaml
dev-autopilot --db /tmp/issue.sqlite3 pause RUN_ID
dev-autopilot --db /tmp/issue.sqlite3 resume RUN_ID /tmp/issue.job.yaml
```

`PAUSED_QUOTA` can be resumed once its persisted retry deadline is due.
`PAUSED_HUMAN_DECISION` means a reviewer needs your decision. `FAILED` records
a fail-closed reason; inspect events before changing anything. At
`READY_FOR_HUMAN_REVIEW`, inspect the diff and test results yourself, then make
the commit, PR, and merge outside Autopilot. The TargetIntel example is a starting
point only: narrow its broad path list for every issue.

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
`gates.allow_git_writes` is false. Test command strings are argv-parsed by
default; shell syntax (`&&`, pipes, redirects) runs only with the explicit
`test_commands.allow_shell: true` opt-in, because shell evaluation expands the
trust boundary.

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
