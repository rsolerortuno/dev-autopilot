# Changelog

## 0.1.0 - 2026-07-26

- Added immutable job, state, failure, retry, audit and review contracts.
- Added versioned SQLite persistence, append-only events and expiring run locks.
- Added fail-closed transition engine, idempotent phase results and durable retries.
- Added CLI commands for doctor, init, start, status, events, watch, pause, resume,
  cancel, approve, archive and legacy migration.
- Added fake and subprocess-backed agent/command adapters.
- Added scope, command, context-size and git-metadata safety gates.
- Added mandatory AGY audit cache and independent Claude review/correction loop.
- Added a TargetIntel-IO job profile and real-agent bridge protocol.
- Added a fully offline integration suite.
