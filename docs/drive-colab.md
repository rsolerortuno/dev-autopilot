# Drive and Colab operating guide

## Durable root

Create one Drive folder for Dev Autopilot and copy its folder ID. The backend
creates only folders needed by write operations. Reads never create paths.

Recommended logical layout:

```text
DevAutopilot/
├── devautopilot/queues/
├── devautopilot/running/
├── devautopilot/checkpoints/
├── devautopilot/completed/
├── projects/<project>/inputs/
└── projects/<project>/outputs/
```

## Install the exact wheel

Build `dev_autopilot-0.5.0-py3-none-any.whl`, upload it to Drive, then set its
path in `notebooks/dev_autopilot_colab_worker.ipynb`. The notebook deliberately
does not install an unpinned GitHub branch.

## Authentication

Authenticate Drive from the active Colab runtime. Do not place OAuth tokens or
CLI credentials in Drive. Use Colab Secrets or the local runtime's protected
credential store.

## Large source already in Drive

Use `drive split-key`. It reads bounded byte ranges and writes verified parts
back to Drive. No complete source copy is required on the laptop.

## Recovery model

Colab runtimes are ephemeral. Recovery relies on:

- verified input parts;
- a durable queue record;
- lease and fencing tokens;
- periodic runner checkpoints;
- application progress written through `save_progress()`;
- attempt-specific output namespaces.

A replacement worker loads the previous checkpoint. The scientific entrypoint is
responsible for translating that progress into an application-specific resume
position.

## Operational checks

Before a large run:

1. verify the source identity and parts;
2. set realistic RAM, disk, GPU-memory, and timeout requirements;
3. register the appropriate worker class;
4. confirm `colab status` shows the job queued;
5. keep the watchdog running from the notebook or another process;
6. verify the final review bundle and output checksums.

## Multi-worker and long-session safeguards

- `claim()` refuses to replace a live lease even if an eventually consistent Drive listing temporarily re-exposes the queued object.
- Fencing tokens remain the final protection against stale publication after takeover.
- Completed, failed, or fenced worker scratch directories are removed from `/content` after durable terminal publication so sequential jobs do not exhaust Colab disk.
- Drive metadata and logical object operations use bounded exponential backoff with jitter. Upload retries re-resolve the stable storage key before creating, preventing response-loss retries from creating duplicate queue records.
- Expired OAuth credentials are refreshed before Drive operations and resumable upload chunks.
