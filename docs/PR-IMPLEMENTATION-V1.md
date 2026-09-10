Dev Autopilot previously lacked enforceable shared provider budgets, robust local
queue recovery, scoped MCP inspection and reproducible evaluation evidence. This
branch adds durable reservation/replay controls, Windows storage race fixes,
optional Docker isolation and approval grants, immutable Git retrieval, and
versioned code-repair evaluation fixtures.

The current development version is `0.7.0.dev0`; this is a draft toward 1.0.0.
It includes an offline restart demo, package checks, deployment/runbook material
and a checksum-pinned Colab notebook synchronized to the owner's Drive workspace.

Validation before the PR: 414 tests passed on Windows/Python 3.12 with 83.25%
coverage; one Linux process-group test was skipped. Ruff, formatting and strict
mypy passed, and a fresh wheel installation produced a verified demo bundle.
Twenty development retrieval queries improved from Recall@5=0.40 to 0.90; these
are not held-out model-quality results. CI results on this branch are separate
from that local evidence.

Linux CI (Python 3.11/3.12/3.13), CodeQL and a live Docker enforcement/timeout
smoke passed at bfeb515. The branch also includes an offline eight-hour durability
runner with incremental checkpoints and suspension-gap exclusion. A local run is
in progress; it is not yet evidence of eight hours completed.

Remaining acceptance gates include real-provider matched
evaluations and billing, Colab interruption/recovery, an eight-hour soak and the
owner's portfolio presentation/final review. No 1.0.0 tag or release is published.
