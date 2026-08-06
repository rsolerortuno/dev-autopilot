# M05 threat model

## Assets

- product source code and Git metadata;
- scientific input data;
- model and analysis outputs;
- agent credentials and cloud tokens;
- milestone findings and human review evidence;
- Drive queue ownership and final release bundle.

## Primary threats and controls

| Threat | M05 control |
|---|---|
| Agent edits unauthorized paths | explicit path rules plus post-run scope gate |
| Agent stages or changes refs | HEAD/ref/index snapshot and security failure |
| Shell injection in test commands | argv default; explicit shell opt-in |
| Worker path traversal | strict IDs and resolved relative-path containment |
| Secret leakage to worker | minimal environment plus explicit allowlist |
| Colab dies during a long job | durable queue, heartbeat, checkpoint, watchdog |
| Two workers execute one job | live-lease check before claim, plus fencing and owner validation at publication |
| Stale worker overwrites output | attempt-and-fence output namespace |
| Source changes during split | object identity checked before and after |
| Corrupt or missing part | per-part SHA-256 and whole-file digest |
| Misleading human report | report included in checksums and folded digest |
| Review no longer matches code | diff-keyed invalidation and acceptance blocker |
| Drive read creates unwanted folders | read-only resolution path |

## Residual risks

- Local agent execution has host permissions and is not container-isolated.
- Drive coordination is at-least-once, not a database transaction. Live leases reduce duplicate starts, while fencing remains the final publication barrier.
- A network partition can waste computation before fencing rejection.
- Live cloud quotas and provider behavior are external dependencies.
- Generic processes require application cooperation for semantic resume.
- User authentication and selection of a Colab accelerator remain manual.
- Bundle checksums detect corruption and inconsistent modification, but they are not signatures. A party able to rewrite the complete bundle can recompute every unsigned digest; origin authentication requires an external signature or provenance attestation.
- Bundle verification rejects undeclared files, directories and symlinks, but authenticity still depends on the trusted distribution channel.

These residual risks must be visible in the release report rather than hidden by
a successful local test suite.
