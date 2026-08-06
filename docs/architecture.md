# Dev Autopilot M05 architecture

## Decision summary

Dev Autopilot 0.5.0 has two cooperating planes:

1. a persistent control plane for project milestones, repository work, review,
   acceptance, and evidence;
2. ephemeral workers for storage-heavy, high-RAM, GPU, or TPU jobs.

SQLite is the local source of truth for control-plane state. A `StorageBackend`
provides local or Google Drive object storage for large inputs, worker queues,
checkpoints, and outputs.

## Control-plane layers

1. **Contracts** — immutable Pydantic models and generated JSON schemas.
2. **Persistence** — SQLite runs, events, retries, locks, evidence, findings,
   project milestones, and assumptions.
3. **State engine** — legal fail-closed phase transitions.
4. **Execution adapters** — deterministic commands and replaceable agents.
5. **Review and gates** — scope, tests, strict output parsing, diff-bound audit,
   and independent review.
6. **Acceptance and bundle** — milestone score, blocker evaluation, baseline,
   manifests, report, and complete integrity verification.
7. **Project runner** — topological milestone execution under an immutable
   no-questions charter.

Lower layers do not own approval authority.

## M00/M01 project execution

A `ProjectCharter` contains one immutable mission, definition of done, non-goals,
a fixed autonomy policy, and a DAG of milestones. `ask_questions` is a literal
false value, not a runtime preference.

The continuous runner selects the next milestone whose dependencies are
accepted. A stopped run creates a persisted blocker and `ACTION_REQUIRED.md`.
The original project is never silently rewritten. A later resume continues the
same milestone run.

## M02 findings

Audit and review reports are persisted with:

- milestone and reviewer role;
- exact diff SHA-256;
- decision and full point list;
- severity and blocking status;
- evidence and required resolution;
- resolution evidence and verifier identities.

P0/P1 findings that are open, invalidated, or attached to a superseded diff block
acceptance. Model reconstruction validates every state update.

## M03 baseline and bundle

Baseline capture records:

- current commit and branch;
- stable tracked and untracked content hashes;
- status and dependency/environment digests;
- timestamp and repository path.

One validated acceptance decision drives both the machine result and HTML
verdict. The bundle manifest includes the HTML report. Verification recomputes
all file digests and the folded bundle digest, so the human-facing report cannot
be changed independently.

## M04 storage

`StorageBackend` supports bounded range reads, streaming file upload, strict
append, listing, identity, checksums, and independent clients for heartbeat
threads.

The splitter:

- never modifies the source;
- validates source identity before and after processing;
- writes a bounded temporary part;
- uploads and verifies each part;
- reuses only verified parts after restart;
- records per-part and whole-file SHA-256;
- fails on a zero-progress read or identity change.

Reassembly validates every part while writing to a temporary destination and
atomically replaces the final path only after whole-file verification.

The Drive backend separates read resolution from folder creation, recursively
lists keys, uses range reads for hashing, and uses resumable Google media upload
sessions for files.

## M05 queue and worker

Queue records live under:

```text
devautopilot/
├── queues/<resource>/
├── running/
├── checkpoints/
├── completed/
├── failed/
└── blocked/
```

A claim includes an owner token, random fencing token, attempt number, and lease
expiry. The owner must prove the current fencing token before heartbeat,
checkpoint, failure, blocking, or completion.

Workers heartbeat for the complete job lifetime, including input staging and
output upload. Checkpoint sequences are monotonic. The watchdog requeues expired
jobs until `max_attempts` is reached. Every output is written below an
attempt-and-fence namespace; a stale worker may finish computing but cannot
publish a terminal result or overwrite a newer attempt.

Drive provides last-write-wins objects rather than database transactions. The
system therefore uses at-least-once semantics plus fencing and idempotent output
publication, not an unsupported exactly-once claim.

## Security boundaries

- no autonomous Git stage, commit, push, tag, release, or merge;
- Git HEAD, refs, and actual worktree index protected when writes are disabled;
- argv execution by default; shell execution requires explicit trust;
- process groups terminated on timeout;
- repository paths and worker local paths reject traversal;
- workers receive an environment allowlist rather than the complete host env;
- source identities and artifact checksums are verified;
- report and bundle integrity is fail-closed;
- secrets are not stored in Drive.

The local process runtime is not a full sandbox. Container isolation is outside
M05 and is documented as a remaining limitation.
