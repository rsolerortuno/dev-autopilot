# Offline soak runner

`scripts/soak_offline.py` exercises the local durable queue and budget stores
without providers, credentials, payments, Colab, or external services. Each
cycle verifies lease expiration and takeover, stale-owner fencing, terminal
publication followed by an injected cleanup crash, coordinator/database
reopen, and a denied second budget reservation.

The default command is an eight-hour run with a five-second interval:

```sh
python scripts/soak_offline.py --output ./outputs/soak
```

Use a short run for smoke validation:

```sh
python scripts/soak_offline.py --output ./outputs/soak-smoke --duration-seconds 1 --interval-seconds 0.1
```

`soak-checkpoint.json` is rewritten atomically after every completed cycle and
on failure. `soak-report.json` records `success`, `failed`, or `incomplete`,
cycle outcomes, failures, and monotonic elapsed/active timings. Active time is
measured only around work cycles; interval sleep is excluded, so suspension or
waiting is not reported as work. The eight-hour default is not run by the test
suite and has no live-provider or Colab interpretation.
