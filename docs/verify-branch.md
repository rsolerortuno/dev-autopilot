# Verifying this branch before merging to `main`

Two independent things changed. Verify them separately — the first needs a real
Claude CLI, the second does not.

```bash
git fetch origin claude/reviewer-plan-mode-conflict-baqvkb
git checkout claude/reviewer-plan-mode-conflict-baqvkb
python -m pip install -e '.[dev]'
python -m pip install -e 'packages/dev-autopilot-evals[dev]'
```

## 1. Claude runs non-interactively again

The reviewer and supervisor used to default to `--permission-mode plan`, which
is read-only. Claude could not run the gates or write the decision JSON, so it
asked for human input, the bridge exited non-zero, and the retry budget drained:

```text
CLAUDE_REVIEW -> FAILED | retry budget exhausted for claude-reviewer: 5 attempts
```

Check the new default:

```bash
python -c "
from dev_autopilot.bridge import _default_agent_command
print(_default_agent_command('DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND'))
print(_default_agent_command('DEV_AUTOPILOT_CLAUDE_SUPERVISOR_COMMAND'))
"
# ['claude', '-p', '--permission-mode', 'auto']  (twice)
```

Confirm your Claude CLI accepts it:

```bash
claude --version
claude -p --permission-mode auto "Reply only with OK"
```

If your CLI is too old to know `auto`, override just the mode — no need to
rewrite the whole command:

```bash
export DEV_AUTOPILOT_CLAUDE_PERMISSION_MODE=acceptEdits
```

Overriding the entire command still works and still wins:

```bash
export DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND='claude -p --permission-mode auto'
export DEV_AUTOPILOT_CLAUDE_SUPERVISOR_COMMAND='claude -p --permission-mode auto'
```

Regression tests:

```bash
pytest tests/unit/test_bridge_defaults.py -q
```

## 2. Eval authoring, with the core untouched

Confirm first that the core really is untouched by the eval feature — this diff
adds no eval code, entry point or dependency to `dev_autopilot`:

```bash
git diff main -- pyproject.toml                      # no output
git diff --stat main -- src/dev_autopilot            # only bridge.py, orchestrator.py, review.py
grep -rn "eval" src/dev_autopilot --include=*.py     # no eval-authoring code
```

`dev-autopilot` still behaves exactly as before:

```bash
dev-autopilot --help
dev-autopilot doctor
```

### Wiring smoke test (no agent calls, no API spend)

```bash
cd "$(mktemp -d)"
printf 'Treated mean 42.0 vs control mean 60.0 across 3 replicates.\n' > paper.txt

dev-autopilot-eval prepare paper.txt --out ws --count 1 --json
dev-autopilot-eval rerun ws --fake --verbose --json
```

Expect the orchestrator to walk the real state machine
(`IMPLEMENTATION → SCOPE_VALIDATION → FAST_TESTS → AGY_AUDIT → CLAUDE_REVIEW →
FINAL_TESTS → READY_FOR_HUMAN_REVIEW`) and then the validator to report an empty
pack — the scripted offline adapters author nothing. `validation_passed: false`
with `autopilot_exit_code: 0` is the correct outcome here: it proves the charter,
the subprocess boundary and the validator are all connected.

### Real run

```bash
dev-autopilot-eval build paper.pdf \
  --data supplementary_table.csv \
  --out latch-eval-workspace \
  --count 3 \
  --verbose
```

Then gate the produced pack on its own:

```bash
dev-autopilot-eval gate latch-eval-workspace --expected-count 3
```

### Recovering the run that failed on `plan` mode

A run that exhausted its retry budget is terminal and cannot be resumed. Start a
fresh run over the existing workspace instead — the paper is not re-ingested and
nothing already authored is deleted, and the failed attempt stays in the same
recovery database:

```bash
dev-autopilot-eval rerun latch-eval-paper-supp123 --verbose
```

## 3. Everything else

```bash
pytest -q                                              # core
ruff check . && ruff format --check . && mypy          # core lint/types
cd packages/dev-autopilot-evals && pytest -q && mypy   # companion
```

## What is deliberately not here

The eval-specific reviewer wording that the prototype branch
(`agent/latch-eval-authoring`) added to the core orchestrator prompts — recipe
leakage, prompt cues, grader discrimination — is not in the core. It reaches the
reviewers through the charter's `objective` and `scientific_invariants`, which
`dev-autopilot-eval prepare` writes and the orchestrator already forwards in the
agent context. Only the domain-neutral half of those prompt changes was kept.
