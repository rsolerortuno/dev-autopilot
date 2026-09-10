# Local operator approval grants

The CLI can issue an expiring grant for a run in `READY_FOR_HUMAN_REVIEW`:

```sh
dev-autopilot --db state.sqlite3 approval issue RUN_ID --actor reviewer --ttl-seconds 900
dev-autopilot --db state.sqlite3 approve RUN_ID --actor reviewer --grant-id GRANT_ID
```

The grant binds the run ID, actor, action and current Git diff digest. A changed
diff, expired grant, different run/actor or replay is rejected. Issuance and
consumption use a local `authorization.sqlite3` alongside the run database.
The actor is an operator-supplied audit label, not an authenticated user identity.
Protect both databases and repository access with host permissions.

Without `--grant-id`, `approve` retains the existing local operator authority.
Grants are an opt-in workflow safeguard, not a mandatory multi-user security
boundary. Consuming a grant and updating the run are separate transactions:
a crash between them fails closed and may require a newly reviewed grant.
Repository mutation by another host process remains outside this authority
model. This command approves the local run; it never publishes a release.
