# Provider budgets and recovery

Real CLI runs wrap every configured agent with a durable budget and metadata-only
JSONL tracing. `budget.max_calls` defaults to 100 per milestone and
`budget.project_max_calls` to 1000 across the project. Standalone jobs use their
configuration ID; project runs share the charter project ID across restarts and
new executions. An explicit `budget.project_id` overrides this identity.
All participating processes must share the database directory. Separate databases
do not share limits. Existing limits are immutable; changing them requires an
explicit operational migration, not silently reopening a larger budget.

Optional monetary caps use integer micro-US dollars. Each agent then requires a
positive `estimated_micro_usd`. The current executable adapter reports no trusted
billing: the estimate remains charged and actual usage is recorded as unknown.
This is an admission limit on estimates, not a guarantee of the provider invoice.
Obtain billing evidence from the provider before making cost comparisons.

Reservations are written before execution. A stable logical invocation can claim
execution only once, including after a process restart. An exception after claim
leaves an ambiguous reservation charged and blocks automatic replay. Operators
must inspect the provider and repository state before authorizing a new attempt;
never delete the budget database or reset a claimed reservation to bypass this
check. No automatic reconciliation or exactly-once external effects are claimed.

`provider-traces.jsonl` contains provider/model, project, milestone, run, phase,
latency, outcome and known usage. It excludes task text, response text and process
environment. Tracing failures do not retry external calls. Fake offline runs do
not call providers and are not billing or latency benchmarks.

Agent settings accept `runtime: docker`, a digest-pinned `sandbox_image`, and
`sandbox_network` (default `none`). Local execution and test commands are not
sandboxed. Docker enforcement still requires the Linux integration gate; an
isolated subprocess alone is not a security boundary.
