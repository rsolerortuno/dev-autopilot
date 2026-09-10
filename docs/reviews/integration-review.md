# Integration review

Reviewed `work/dev-autopilot` at commit `b84643b` with the pending working-tree changes after `17696de`. The review covered provider trace context, configured budget wiring, and the isolated evaluation oracle.

## Findings

No new release-blocking defect was confirmed in the reviewed changes.

The project budget identity correction now uses `charter.project_id`, so project caps can aggregate across milestone runs. `BudgetStore.configure()` checks existing project-level limits, and the provider wrapper derives a stable call ID from the invocation context. Trace records use the explicit wrapper values first and fall back to run/state context.

The evaluation oracle now runs candidate functions with `python -I`, passes only a minimal environment, keeps expected answers in the parent evaluator, and polls a temporary output stream with a two-second and one-megabyte bound. Candidate output remains untrusted and is compared to evaluator-owned expected values.

Two boundedness gaps remain in the harness. The main candidate process writes stdout and stderr to temporary files without a live size limit; the one-megabyte limit is applied only when reading the tail after process exit, so a hostile candidate can consume unbounded temporary disk space. Also, both timeout paths call `wait()` after killing the process without a second deadline. A stuck descendant or platform-specific process teardown can therefore hold the evaluator indefinitely. These should be fixed before treating hostile-candidate isolation as complete.

## Residual verification limits

The oracle is process-isolated for the supported evaluator path, but live sandbox enforcement was not verified: Docker is unavailable on the Windows host. The Windows branch uses `process.kill()` rather than a process-group termination, so descendant cleanup remains a live-platform concern for hostile candidates. The oracle also checks output paths before reading them; a hostile concurrent path swap would require a no-follow file-descriptor implementation if that threat model is in scope.

These are verification or hardening limits, not failures demonstrated by the current tests.
