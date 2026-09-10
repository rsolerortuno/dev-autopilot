Prepare Dev Autopilot 0.9.0 as a public CLI preview for acceptance testing before 1.0.0. The owner explicitly authorized this preview's publication.

This adds durable provider budgets and queue recovery, optional Docker isolation, scoped approval grants and MCP inspection, immutable Git retrieval and offline evaluation fixtures. The README retains its structure with diagrams explaining the workflow, quota/restart and worker leases, plus wheel installation steps.

Package and Colab notebook versions are aligned to 0.9.0. Drive transfers are SHA-256 verified. Tag publication runs mandatory CI, validates metadata, builds an SBOM and checksums, and publishes only the authorized v0.9.0 preview.

Validation: preceding Linux CI passed 418 tests (one opt-in Docker skip), lint, format, mypy, dependency audit and fresh installation on Python 3.11–3.13. Docker enforcement/timeout cleanup and CodeQL passed separately. The 0.9.0 local wheel installs and completes the offline pause/restart demo. Final candidate CI must pass.

The saved Colab notebook proves mount/install/queue startup, then eight idle cycles and manual interruption, with no completed job. The eight-hour offline soak is incomplete. Real-provider comparison/billing, Colab job recovery, the full soak and owner acceptance remain 1.0.0 gates.
