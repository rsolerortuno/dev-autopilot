# Dev Autopilot 0.9.0

0.9.0 is a public preview for hands-on acceptance testing before 1.0.0.
Publication of this preview was explicitly authorized by the owner on 2026-09-11.

## Install and try it

Download the wheel and SHA256SUMS.txt from the GitHub v0.9.0 release. Use Linux
or WSL2 for actual agent subprocess execution, with Python 3.11–3.13.

```sh
sha256sum --check SHA256SUMS.txt --ignore-missing
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./dev_autopilot-0.9.0-py3-none-any.whl
dev-autopilot --help
python -m dev_autopilot.demo --output ./autopilot-demo
```

The demo uses deterministic fake agents, needs no model credentials and should
report `successful_bundle: true`. It exercises pause, restart, resume and bundle
verification. For a real project follow README.md, configure the provider CLIs
and budgets, and first use a disposable checkout.

Record the version, OS, command, expected result and observed result for each
issue. Remove credentials and private source data before sharing logs.

## Evidence and remaining 1.0.0 criteria

Linux CI, CodeQL, dependency audit and a real Docker enforcement/cleanup test
passed for the preceding implementation. The release tag has its own mandatory
CI and package metadata validation before artifacts are published.

The saved Colab notebook confirms Drive mounting, package installation and queue
startup. It waited for eight idle cycles without a job and was manually stopped.
That does not prove job completion or recovery. Matched-provider benchmarks, measured billing, Colab job
interruption/recovery, eight observed hours of durability testing and final
owner acceptance remain open. No 1.0.0 readiness claim is made.

Windows supports the offline demo/storage checks; Linux/WSL2 is the supported
process runtime. This release is a Python CLI package, not a Windows GUI installer.
