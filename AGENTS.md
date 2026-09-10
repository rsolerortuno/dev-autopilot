# Dev Autopilot implementation instructions

## Mission and acceptance

Implement the 1.0.0 plan in `docs/PLAN-1.0.0.md`. Track work in
`docs/execution-state.json`; a planned feature is not an achieved milestone.
Keep external-provider tests, offline tests, and unverified claims separate.
Do not publish a release or tag until the owner reviews the final candidate.

## Collaboration

Use one coordinator and at most three gpt-5.6-luna subagents. Each agent works in
an isolated worktree. The owner's coordinator preference is gpt-6-astra with low
reasoning effort (configured by the host model selector). Assign each worker a
Git branch. One owner at a time edits shared contracts,
schemas, DB or CLI. Commit bounded changes, provide test evidence, and have a
different agent review before integration. Never weaken a gate to claim success.
Linux/WSL2 is the supported process runtime; offline storage should be portable.

## Durable storage and Colab

User-authorized Drive parent: `1o5gfkBSUw32YOoqc555yLUE3YCQYV8F_`.
Project folder: `1PAkI23Ajzu5QcmcI8quw_Xfd6-4UhROO`.
Folder IDs and notebook sync metadata live in `docs/drive-workspace.json`.
Use only this project folder for new artifacts; preserve other projects.
Store large artifacts, checkpoints, evidence and notebooks in the corresponding
subfolders. Use resumable streaming or verified parts, bounded memory, and
SHA-256 manifests. Verify uploads/downloads; never label an unverified transfer
complete. Do not store credentials or tokens in Drive or evidence.

Keep `notebooks/dev_autopilot_colab_worker.ipynb` synchronized to the notebook
folder after changes, preserving the remote file ID when available. It must
install an exact wheel and verify its SHA-256 before executing package code.
Colab sessions are ephemeral and require user authentication/runtime allocation;
preserve checkpoints and document any external blocker rather than inventing a
successful run. Until atomic coordination is validated, use one Drive worker.

## Resume protocol

Read execution-state, Git status/log and latest evidence. Resume incomplete tasks
without repeating completed mutations. Record task IDs, branches, commits,
review status, checks and external blockers. Continue independent work when an
external service is unavailable.
