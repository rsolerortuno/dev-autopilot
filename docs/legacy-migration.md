# Legacy Bash migration

`dev-autopilot migrate-legacy JOB CHECKPOINT` imports either a JSON object or a
simple `KEY=value` checkpoint. At minimum it recognizes `state` or
`workflow_state` and an optional `stop_reason`.

The import creates a new run, replays the canonical legal transitions and adds a
`LEGACY_MIGRATED` journal event containing the source values. The legacy files
remain untouched. Keep the old launcher available until one real project run has
reached `READY_FOR_HUMAN_REVIEW` and its event history has been inspected.
