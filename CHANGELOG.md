# Changelog

## 0.9.0 - 2026-09-11

Public preview for user acceptance testing before 1.0.0.

- Durable provider budgets, restart recovery, scoped approvals and MCP inspection.
- Optional Docker sandbox with live Linux enforcement and timeout cleanup checks.
- Immutable Git retrieval and reproducible offline evaluation fixtures.
- Verified wheel, Drive checkpoint storage and checksum-pinned Colab worker.
- Linux CI across Python 3.11–3.13; offline restart demo and durability soak runner.

The Colab worker has been started by the owner. This is not yet evidence of a
completed real-provider workload or interruption/recovery test. The eight-hour
soak and matched-provider quality/billing evaluations remain 1.0.0 gates.

See `docs/RELEASE-0.9.0.md` for installation and acceptance testing.

## 0.6.0 - 2026-08-10

### Real-project execution hardening

- Added built-in default commands for Codex, AGY, and Claude so normal project
  execution no longer requires `DEV_AUTOPILOT_*_COMMAND` environment variables.
  Environment variables remain available as explicit overrides.
- Added structured AGY integration with repository-bounded, read-only audit
  instructions and schema-constrained `AuditReport` output. AGY audits start in
  an isolated project automatically, so repository reads do not require manual
  per-repository `read_file(...)` permission configuration.
- Added deterministic-gate autocorrection instead of terminal failure when an
  implementation can be repaired.
- Added continuous project retry/resume behavior for retryable agent failures.
- Changed default transient retry delays to short linear backoff
  (`1, 2, 3, 4, 5, 6, 7, 8` seconds), while explicit provider reset times still
  take precedence.
- Improved subprocess classification so malformed bridge output is not
  incorrectly classified as provider quota exhaustion.
- Added live verbose project progress reporting.
- Hardened changed-file validation.
- Fixed Google Drive `appProperties` failures for long logical storage keys by
  storing a deterministic SHA-256 identifier rather than the full logical key.
- Added regression tests covering gate autocorrection, project autoresume,
  built-in bridge commands, and Drive long-key handling.
- Validated the hardened workflow against a real TargetIntel-IO M00 project,
  including Codex correction, deterministic gates, AGY audit, independent
  review, evidence packaging, and `READY_FOR_HUMAN_RELEASE`.

## 0.5.0 - 2026-08-06

Final M00-M05 release candidate: continuous milestone execution, point-by-point
review evidence, integrity-protected release bundles, bounded-memory storage, a
Drive-backed queue and recoverable Colab workers.

### Continuous project execution (M00-M01)

- Added immutable project charters with mission, definition of done, non-goals,
  autonomy policy and a dependency-checked milestone DAG.
- Added `project init`, `project plan`, `project start`, `project resume` and
  `project status` commands.
- Added continuous no-questions execution using safe, reversible assumptions.
- Added durable assumption records and machine-readable blockers with one
  required action and an explicit resume condition.
- Added per-milestone bundles and a final `READY_FOR_HUMAN_RELEASE` state.

### Review ledger and bundles (M02-M03)

- Persisted implementation, adversarial-audit and independent-review evidence.
- Added stable point identifiers, severity, blocking status, diff binding,
  resolution evidence and independent verification metadata.
- Invalidated P0/P1 findings now block acceptance until re-reviewed on the final
  diff.
- Finding resolution and invalidation updates are revalidated through Pydantic.
- Added exact tracked and untracked baseline hashes, Git state and dependency and
  environment fingerprints.
- Made one validated acceptance decision the source of truth for milestone and
  HTML report readiness.
- Added checksums for `report.html`, verification of the folded bundle digest and
  fail-closed tamper detection.
- Integrated baseline capture, review evidence, acceptance and verified bundle
  generation into the normal orchestration path.

### Large files and Google Drive (M04)

- Added bounded-memory range streaming for split, reassembly and hashing.
- Added source identity checks before and after a split and atomic local output
  publication.
- Added direct remote-object splitting without downloading the complete source
  file to the laptop.
- Implemented recursive Drive prefix listing, side-effect-free reads, object
  identity, range reads, file uploads and resumable Google API uploads.
- Added a simulated Drive contract suite covering folder creation, upload,
  update, listing, queue submission, claim, checkpoint and completion.

### Colab workers (M05)

- Added strict safe IDs and relative paths for jobs and staged inputs.
- Added leases, heartbeats, fencing tokens, owner-verified checkpoints and
  terminal publication, monotonic checkpoint sequences and retry attempts.
- Heartbeats now cover staging, execution and output upload, preventing long
  jobs from being reclaimed while still active.
- Added process-group timeout termination, minimal environment construction,
  explicit environment allowlists and resource preflight checks.
- Added cooperative resume helpers and checkpoint state through
  `DEV_AUTOPILOT_RESUME_SEQUENCE` and `DEV_AUTOPILOT_CHECKPOINT_FILE`.
- Isolated outputs by attempt/fencing token so stale workers cannot overwrite a
  recovered job.
- Added automatic blocker requeue support and worker registration.
- Replaced the fixed notebook with a reusable worker that installs an exact wheel
  from Drive, detects resources and contains no hardcoded project folder.

### Final pre-release hardening

- Refused claims while a live lease exists, preventing duplicate GPU/Colab starts caused by eventually consistent queue listings.
- Removed terminal and fenced worker scratch directories after durable publication to prevent `/content` exhaustion across sequential jobs.
- Rejected undeclared bundle files, directories and symlinks during verification.
- Documented that checksums provide integrity rather than origin authentication.
- Added logical-operation Drive retries with exponential backoff and jitter, stable-key re-resolution to avoid duplicate creates, and automatic credential refresh for long sessions.

### Quality and release engineering

- Added project-charter and worker-job JSON Schemas to the wheel.
- Corrected project metadata so runtime dependencies are packaged correctly.
- Added CI coverage enforcement, Drive-extra tests, schema/notebook validation,
  fresh-wheel smoke testing, dependency audit, CodeQL, Dependabot, release
  checksums and SBOM generation.
- Added a threat model, Drive/Colab operations guide, security policy,
  contribution guide and validated examples.

## 0.2.0 - 2026-08-06

Initial M02-M05 prototype. It introduced the first findings ledger, bundle,
storage and worker primitives. The 0.5.0 release replaces its incomplete Drive,
checkpoint, fencing and acceptance behavior.

## 0.1.1 - 2026-07-26

- Hardened linked-worktree Git metadata protection.
- Added bounded process-group termination on timeout.
- Added durable lock renewal and lost-lease rejection.
- Made shell execution an explicit trust decision.
- Expanded branch-focused safety and review tests.

## 0.1.0 - 2026-07-26

- Added immutable job, state, failure, retry, audit and review contracts.
- Added versioned SQLite persistence, append-only events and expiring run locks.
- Added fail-closed transition engine, idempotent phase results and durable retries.
- Added CLI commands for doctor, init, start, status, events, watch, pause, resume,
  cancel, approve, archive and legacy migration.
- Added fake and subprocess-backed agent/command adapters.
- Added scope, command, context-size and Git-metadata safety gates.
- Added AGY audit caching and independent Claude review/correction loops.
- Added a fully offline integration suite.
