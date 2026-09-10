# Resume the development checkpoint

Read `AGENTS.md`, `execution-state.json`, `PLAN-1.0.0.md` and recent Git history.
The coordinator preference is Astra with low reasoning effort; delegate at most
three concurrent workers to `gpt-5.6-luna`. The host model selector controls the
coordinator setting. Each worker owns a separate worktree and bounded task;
another agent or the coordinator reviews before integration.

## Recover from Drive

Drive project folder: `1PAkI23Ajzu5QcmcI8quw_Xfd6-4UhROO`.
Download `checkpoints/dev-autopilot-checkpoint.bundle` and `artefactos/manifest.json`.
Verify the bundle SHA-256 against the manifest, then run:

```sh
git clone dev-autopilot-checkpoint.bundle dev-autopilot
cd dev-autopilot
git switch implementation/v1
git bundle verify ../dev-autopilot-checkpoint.bundle
python -m venv .venv
# Activate the environment for your shell, then:
python -m pip install -e '.[dev,drive,mcp,telemetry]'
pytest --cov=dev_autopilot --cov-fail-under=80 -q
ruff check .
ruff format --check .
mypy src
```

The bundle preserves local history and worktree branch references. Recreate
worktrees with `git worktree add` only after checking `git worktree list`.
Git push now works. The active draft is PR #13 on `implementation/v1`.
CI and CodeQL passed for e1ecfc1; verify later commits separately.
Do not print or copy credentials into evidence.

## Colab

Open `notebooks/dev_autopilot_colab_worker.ipynb` from the project Drive folder.
Allocate the runtime and authenticate Drive interactively. Run configuration,
mount, checksum verification, installation and authorization cells in order.
The notebook points to the development wheel and verifies its exact SHA-256.
Use only one Drive worker: local SQLite coordination does not coordinate separate
Colab hosts. Keep required credentials in Colab Secrets, not Drive.

## Remaining release gates

Local offline soak started on 2026-09-10, PID 26992. Inspect
`outputs/soak-8h-20260910/soak-checkpoint.json` in the parent workspace and the
matching stdout/stderr logs before starting another run. This process needs the
computer to remain available; suspension gaps do not count toward the target.
If interrupted, preserve its evidence and start a fresh run in a new directory.
See `OWNER-HANDOFF.md` for the account actions still needed.

- CI on Linux/Python 3.11–3.13 for the final code SHA and live Docker enforcement.
- Matched-model single-agent/pipeline/ablation evaluations, three repetitions,
  measured provider usage, and the plan's success threshold.
- Retrieval/context usefulness on representative real tasks, beyond fixture tests.
- Authenticated Drive/Colab interruption/recovery and eight-hour soak.
- Owner video, explanation of tradeoffs and final release review.

Approval of a local run does not publish a release. Do not create a 1.0.0 tag or
publish until the owner reviews the final candidate and evidence.
