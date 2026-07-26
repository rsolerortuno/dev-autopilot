# Dev Autopilot architecture

## Decision summary

Dev Autopilot is a Python state machine whose durable source of truth is a
versioned SQLite database. Agent processes are replaceable adapters. They do not
own state transitions, retry counters, final approval or merge authority.

The package is divided into six dependency layers:

1. `models`, `states`, `errors`: immutable contracts and stable serialized values.
2. `db`, `events`: transactions, migrations, append-only history, locks and caches.
3. `engine`, `retries`: legal transitions and persistent recovery decisions.
4. `adapters`, `gates`, `review`: external execution and fail-closed validation.
5. `orchestrator`: idempotent phase handlers and the complete review loop.
6. `cli`, `legacy`: operator interface and compatibility boundary.

Lower layers never import higher layers.

## Persistence

Schema version 1 contains:

- `runs`: exactly one current state, optional resume state, failure and approval data;
- `events`: ordered append-only journal protected by update/delete triggers;
- `phase_results`: successful idempotency records keyed by phase and input SHA-256;
- `retries`: independent persistent counters and deadlines per adapter owner;
- `run_locks`: expiring exclusive run ownership;
- `artifacts`: immutable artifact manifests;
- `audit_cache`: AGY reports keyed by the reviewed diff SHA-256;
- `schema_migrations`: applied schema versions.

Mutating operations use `BEGIN IMMEDIATE`. State writes compare the expected
current state inside the same transaction, so stale supervisors fail closed.

## Authority

- Codex may change only configured product paths. It cannot review or approve.
- AGY is read-only and adversarial. Its report is mandatory by default.
- Claude reviewer is read-only and owns the implementation review decision.
- Claude supervisor is optional and operational only; it cannot edit product code.
- The human owns initial scientific scope, final approval and any later Git merge.

## Recovery

Retryable quota, timeout and agent failures create a durable `RetryState`. The
backoff sequence is configurable, survives restarts, uses deterministic jitter
and honors a later explicit quota reset. `watch` resumes a due run without
resetting its attempt count.

Every pause stores `resume_state`. Every terminal failure stores a non-empty
reason and stable `ErrorClass`. A run lock prevents concurrent supervisors.
Leases are atomically renewed before each phase and must still be live and owned
by the same token after external execution before its result is persisted. By
default, a lease is the largest configured agent timeout or gate timeout, plus
60 seconds for bounded process cleanup (with a 300-second minimum). This lets a
configured long-running agent finish without the supervisor losing ownership
mid-call. Callers may explicitly provide a positive TTL for controlled
deployments. An expired lease is never resurrected: a crashed supervisor can be
taken over after expiry, while a supervisor that loses ownership stops
fail-closed without writing a transition.

## Idempotency and audit cache

A deterministic phase input hash combines configuration identity, workflow
state, command and repository diff identity. Successful deterministic phases are
reused only while that hash is unchanged. AGY reports are cached only for the
exact diff SHA-256; any source change invalidates the cached audit.

## Security defaults

- Network-backed real agents require explicit `gates.allow_network: true`.
- Git writes default to false. Agent execution snapshots HEAD, the complete ref
  listing, and the actual index returned by `git rev-parse --git-path index`, so
  linked worktrees are protected too; mutation is a security violation.
- Agent and local command processes run in a dedicated session. On timeout the
  whole process group receives bounded TERM then KILL cleanup; Git metadata is
  checked after cleanup, and a security mutation takes precedence over timeout.
- Test commands use argv execution by default. Shell evaluation requires the
  persisted `test_commands.allow_shell: true` opt-in.
- Scope validation rejects every changed path not matched by an explicit file,
  tree or root-anchored glob rule.
- The supervisor never stages, commits, pushes, tags or merges.
- Context is passed through bounded temporary files and compacted when necessary.
- Tests use fake agents and require no network or subscription.
