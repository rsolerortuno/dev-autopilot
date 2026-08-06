# M05 final release — v0.5.0

## Release decision

Dev Autopilot v0.5.0 closes the audited M00-M05 scope and is ready for a human
release decision. No known defect remains in the locally executable and simulated
Drive scope. Publication still requires the repository CI and one credentialed
Drive/Colab environment smoke run.

## Closed audit findings

| Previous finding | Resolution in v0.5.0 |
|---|---|
| Invalidated P1 could permit acceptance | Invalidated or stale P0/P1 findings block until final-diff re-review |
| HTML could say READY despite failed evidence | One validated acceptance object controls milestone and report readiness |
| `report.html` was mutable | HTML checksum and folded bundle digest are verified |
| Drive queue could not list jobs | Recursive, read-only Drive prefix listing implemented and contract-tested |
| Long Colab jobs lost leases | Background heartbeat covers staging, execution and upload |
| Finding resolution bypassed validation | Updates reconstruct strict Pydantic models |
| Worker path traversal | Safe job IDs, relative input paths and storage keys enforced |
| Secret allowlist unused | Worker builds a minimal explicit environment |
| Checkpoints did not resume | Durable prior checkpoint and resume sequence are passed to the entrypoint |
| M02/M03 detached from orchestration | Baseline, reviews, acceptance and bundle are required workflow phases |
| Duplicate/stale output publication | Attempt-specific fencing namespace and owner-checked terminal commit |
| Relative `PYTHONPATH` failed in worker cwd | Entries are resolved before entering the isolated job directory |
| Duplicate job ID across resource queues | Global queue-state duplicate detection added |
| Eventually consistent queue entry could restart a live job | Live lease is checked before claim; fencing remains the publication barrier |
| Worker scratch accumulated across jobs | Terminal and fenced job directories are removed after durable state publication |
| Unsigned bundle could be over-trusted | Threat model now states checksums are integrity checks, not origin signatures |
| Undeclared official-looking bundle files were ignored | Verification rejects extra files, directories and symlinks |
| Drive transient failures and token expiry | Stable-key retries use backoff/jitter and credentials refresh during long sessions |

## M00-M05 acceptance

- M00: project charter, definition of done, non-goals, autonomy policy and schemas.
- M01: dependency-ordered continuous milestones, no conversational questions,
  assumptions and file-based blockers.
- M02: persistent, diff-bound, point-by-point findings and verified resolutions.
- M03: exact baseline, fail-closed acceptance and fully integrity-protected bundle.
- M04: bounded-memory split/reassembly plus simulated Google Drive contract.
- M05: fenced workers, heartbeats, checkpoints, retries, preflight and Colab notebook.

## Executed evidence

- 155 tests passed with `ResourceWarning` promoted to an error.
- 84.08% total coverage; configured threshold is 80%.
- Source and tests compile.
- Git whitespace validation passes.
- Local project smoke reaches `READY_FOR_HUMAN_RELEASE`.
- Generated milestone bundle verifies.
- Local worker smoke publishes a fenced output and reaches `COMPLETED`.
- Local split/reassembly returns the exact source SHA-256.
- Simulated Drive queue covers submit, list, claim, checkpoint and complete.
- Wheel and source distribution build; the source distribution rebuilds the wheel.
- The final wheel imports from an isolated target directory with all schemas.

See `VALIDATION.md` for the exact commands and the external-provider boundary.
