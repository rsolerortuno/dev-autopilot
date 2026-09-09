# Compatibility policy

Dev Autopilot follows [Semantic Versioning 2.0.0](https://semver.org/).

The public Python contracts, JSON schemas, serialized job records, and SQLite
database format are compatibility surfaces:

- Patch releases fix behavior or documentation without changing accepted
  contract data.
- Minor releases may add optional fields, models, or schema migrations while
  preserving existing 0.6 data and APIs.
- Major releases may remove or change fields, states, APIs, or migrations and
  require an explicit migration plan.

SQLite databases store their migration level in `schema_migrations`. A release
refuses to open a database whose highest recorded level is newer than it
supports; this check runs before initialization so an unsupported future
database is not modified. `SQLiteStore.backup()` creates a consistent snapshot,
and `SQLiteStore.restore()` verifies integrity before atomically installing a
backup. Existing restore destinations require `overwrite=True`.

Packaged JSON schemas are generated from the Pydantic models with:

```text
python scripts/generate_schemas.py
```

The schema synchronization test must pass before a release. Contract changes
must update the model first, regenerate schemas, and include a compatibility
note in `CHANGELOG.md`.
