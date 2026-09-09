# Deployment and recovery runbook

The `Dockerfile` builds a wheel in a builder stage, installs it in a runtime
stage, and runs as the unprivileged `autopilot` user. Builds require an
explicit immutable base-image digest. Obtain the digest from the registry
policy used by your deployment and pass `PYTHON_IMAGE_DIGEST=sha256:...`.
Docker runtime verification is unavailable in the offline test environment.

Persist the SQLite database and queue storage under `/var/lib/dev-autopilot`
using a durable volume. Run the read-only probe against an existing database:

```sh
python -m dev_autopilot.health --db /var/lib/dev-autopilot/autopilot.sqlite3
```

The probe opens SQLite with `mode=ro`, checks schema version and integrity, and
returns non-zero for a missing or incompatible database. It never creates a
database or parent directory.

For a consistent backup, stop writers or use the application's SQLite backup
operation, then retain the resulting file and checksum outside the live
volume. A plain file copy is suitable only while no writer can modify the
database. Restore by placing the verified backup at the configured path,
running the health probe, and starting the service. Keep the original until
the restored probe passes.

Generate offline evidence with `python -m dev_autopilot.demo --output
./offline-demo`. It writes JSON and HTML reports, fixture SQLite state, and an
integrity-verified review bundle. Fake agents and gates are synthetic and are
not a provider quality benchmark.
