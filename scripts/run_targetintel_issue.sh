#!/usr/bin/env bash
set -euo pipefail

ROOT="${DEV_AUTOPILOT_ROOT:-$HOME/projects/dev-autopilot}"
JOB="${DEV_AUTOPILOT_JOB:-$ROOT/examples/targetintel-io.job.yaml}"
DB="${DEV_AUTOPILOT_DB:-$HOME/projects/TargetIntel-IO/.dev-autopilot/autopilot.sqlite3}"

: "${DEV_AUTOPILOT_CODEX_COMMAND:?set the exact Codex subscription CLI command}"
: "${DEV_AUTOPILOT_AGY_COMMAND:?set the exact AGY CLI command}"
: "${DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND:?set the exact Claude reviewer CLI command}"

dev-autopilot --db "$DB" doctor --job "$JOB"
dev-autopilot --db "$DB" start "$JOB"
