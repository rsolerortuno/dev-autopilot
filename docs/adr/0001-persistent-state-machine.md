# ADR 0001: persistent Python state machine

Status: accepted for v0.1.0

## Context

The previous autonomous workflow relied on shell control flow, transient process
state and manually interpreted outputs. Quota pauses, malformed reviews and
process restarts could lose the exact retry attempt or next phase.

## Decision

Use a Python package with SQLite as the authoritative state store. Bash is
limited to optional launchers. Each phase is explicit, idempotent and resumable.
External agents communicate through bounded context and output files. Legal
transitions are centralized and fail closed.

## Consequences

Runs can be inspected and resumed after crashes. Every transition is auditable.
Tests can exercise the complete loop with fake adapters. Schema migrations and
adapter protocols become public compatibility contracts and must evolve
carefully.
