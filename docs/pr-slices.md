# Delivery slices through M05

- **M00:** immutable project charter and schemas.
- **M01:** milestone DAG, no-questions runner, assumptions, blockers, and resume.
- **M02:** strict review reports, stable findings, diff invalidation, resolution,
  and acceptance score.
- **M03:** exact baseline, evidence store, tamper-evident JSON/HTML bundle, and
  verified transition to human review.
- **M04:** bounded-memory storage protocol, local and Drive backends, split,
  resume, verification, and reassembly.
- **M05:** worker job schema, Drive-compatible queue, leases, fencing,
  heartbeats, cooperative checkpoints, watchdog recovery, output publication,
  Colab notebook, and resource routing.

Every slice requires deterministic regression tests. No slice changes human
commit, release, or merge authority.
