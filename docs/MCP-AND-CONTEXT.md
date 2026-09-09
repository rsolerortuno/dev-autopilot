# Scoped MCP and repository context

Install the optional extra: `pip install '.[mcp]'`. Start the read-only stdio
server with an existing database and a configured repository:

```sh
python -m dev_autopilot.mcp_server --repository /path/to/repo --db /path/to/state.sqlite3 --scope repository:read --scope runs:read --scope evidence:read
```

The three tools are `search_repository`, `run_status`, and `evidence_summary`.
Paths are configured by the operator, not supplied by tool callers. Scopes are
explicit on the CLI. SQLite is opened read-only; status returns only state and
timestamps, evidence returns event counts without payloads. This local stdio
server relies on host process identity; it is not a multi-tenant HTTP service.

Search indexes tracked UTF-8 source/document files in a clean Git snapshot.
Hidden and credential-named files and recognizable private keys are excluded.
Results contain commit, path, line range and passage SHA-256. Dirty or changed
snapshots invalidate retrieval. Lexical matching is a transparent baseline,
not a claim of semantic retrieval performance or exhaustive secret detection.

Set `gates.retrieve_context: true` to attach a bounded untrusted-data envelope to
agent context. Changed working trees produce an explicit unavailable result.
Retrieval cannot grant tool permissions, approve changes, or override policy.

Real subprocesses inherit a minimal environment. Add required credential/CLI
configuration *names* under `agents.<role>.allowed_environment`; values stay in
the caller's environment. HOME/PYTHONPATH and bridge override variables require
explicit inclusion. This is intentionally stricter than 0.6.0.

SDK reference: https://py.sdk.modelcontextprotocol.io/get-started/first-steps/
MCP 2.2 is the tested optional SDK baseline. Runtime/provider and retrieval
benchmark validation remain separate from protocol unit/integration tests.
