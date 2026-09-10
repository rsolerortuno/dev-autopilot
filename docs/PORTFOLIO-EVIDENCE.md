# Agentic engineering evidence

This is a development checkpoint, not a completed 1.0.0 portfolio. Use the
following evidence when explaining implementation decisions; retain the gaps
alongside the results.

| Capability | Reviewable implementation | Remaining demonstration |
|---|---|---|
| Durable orchestration | SQLite state machine, retries, persistent project runner; restart demo | Linux and eight-hour soak |
| Multi-agent review | Separate implement/audit/review roles, exact-diff gates, findings ledger | Same-model comparison and audit ablation |
| Tool permissions | Minimal environment, scoped MCP tools, optional pinned Docker adapter | Live container enforcement and full authorization flow |
| Cost control | Atomic reservations, shared project caps, replay prevention | Trusted provider billing and ambiguous-call reconciliation |
| Observability | Metadata JSONL and optional OpenTelemetry sink | Real provider trace and cost evidence |
| Evaluation | 30 code tasks, stratified holdout, 90 verified reference cases, JSON/HTML reports | Three model repetitions, target success rate and failure analysis |
| Retrieval | Commit-pinned Git blobs, source provenance, bounded untrusted context | Twenty-query retrieval metrics and utility ablation |
| Storage and recovery | Chunked SHA-256 transfer, checkpoints, SQLite coordination | Authenticated Colab interruption/resume smoke |
| Delivery | Versioned schemas, CI matrix, package build, demo and health commands | CI on the final SHA and owner release review |

## Demonstration outline

1. Show the charter, scope and deterministic gates. Explain why explicit state
   transitions were chosen for recoverability.
2. Run `python -m dev_autopilot.demo --output /tmp/autopilot-demo` in a fresh
   installed environment. Open the generated HTML report and verify its bundle.
   Identify synthetic agents and gates explicitly.
3. Inspect the quota pause, reopened SQLite state and successful continuation.
   Explain what happens if the process dies after an external call but before
   saving the result; show the fail-closed budget policy.
4. Show an out-of-scope path being denied and explain the local/Docker boundary.
5. Present real benchmark, billing and retrieval reports once collected. Until
   then show their pending status, without substituting fixture tests for them.

The owner still needs to record the 5–8 minute presentation and explain the
tradeoffs, failures and measurements in their own words.
