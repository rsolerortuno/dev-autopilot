# Intended PR slices #5–#15

The implementation is structured so it can be published as the following
independent, sequential pull requests once GitHub write access is available.

## #5 Architecture spike

- `docs/architecture.md`
- `docs/adr/0001-persistent-state-machine.md`

Freezes component boundaries, authority, persistence, recovery, idempotency and
security decisions.

## #6 SQLite persistence

- `src/dev_autopilot/db.py`
- `src/dev_autopilot/events.py`
- persistence tests

Adds migrations, runs, append-only events, phase results, retry state, locks,
artifacts and audit cache.

## #7 Transition engine

- `src/dev_autopilot/engine.py`
- transition tests

Adds the legal transition graph, atomic compare-and-set transitions, pause,
resume, cancel and classified terminal failures.

## #8 CLI

- `src/dev_autopilot/cli.py`
- `src/dev_autopilot/__main__.py`
- console entry points

Adds doctor, init, start, status, events, watch, pause, resume, cancel, approve
and archive.

## #9 Fake agents and adapters

- `src/dev_autopilot/adapters/`
- adapter tests

Adds protocols, deterministic scripted agents, fake commands, local commands and
file-based executable agents.

## #10 Gates and policies

- `src/dev_autopilot/gates.py`
- expanded configuration policies
- scope and git-metadata safety tests

Adds explicit network opt-in, no-git-write enforcement, allowed-path gates,
changed-file limits and bounded context.

## #11 Retries and recovery

- `src/dev_autopilot/retries.py`
- retry persistence and restart integration tests

Adds independent counters, deterministic jitter, explicit quota reset handling
and automatic due-time resume.

## #12 Complete review loop

- `src/dev_autopilot/review.py`
- `src/dev_autopilot/orchestrator.py`
- full offline integration tests

Adds implementation, scope, fast tests, mandatory AGY, independent Claude
review, correction cycles, final tests and human-review terminal state.

## #13 Legacy migration

- `src/dev_autopilot/legacy.py`
- `docs/legacy-migration.md`
- migration tests

Imports active, paused, failed and cancelled Bash checkpoints plus persistent
retry state without modifying legacy files.

## #14 Real TargetIntel-IO run

- `src/dev_autopilot/bridge.py`
- `examples/targetintel-io.job.yaml`
- `scripts/run_targetintel_issue.sh`

Adds a generic subscription-CLI bridge and a concrete profile for
`/home/rso12/projects/TargetIntel-IO`. The actual live run requires that checkout
and the user's local Codex, AGY and Claude commands.

## #15 v0.1.0 release

- `README.md`
- `CHANGELOG.md`
- `LICENSE`
- `.github/workflows/ci.yml`
- generated JSON Schema
- package version and public exports

Declares the 0.1.0 interface and validates Python 3.11–3.13.
