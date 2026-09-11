# Dev Autopilot 0.9.1

Patch preview fixing intermittent approval CLI failures. Random grant IDs could
start with a hyphen, which argparse interpreted as an option when supplied as
`--grant-id VALUE`. Newly issued IDs have a safe alphabetic prefix without
reducing their random entropy. Existing grants remain valid; pass historical
hyphen-leading IDs as `--grant-id=VALUE`.

A deterministic regression forces a hyphen-leading random value and exercises
the issue/approve path. The release requires Linux CI on Python 3.11–3.13,
package metadata checks, fresh-wheel installation, SBOM and checksums.

Download the wheel and SHA256SUMS.txt from this release. With Python 3.11–3.13:

```sh
sha256sum --check SHA256SUMS.txt --ignore-missing
python -m pip install ./dev_autopilot-0.9.1-py3-none-any.whl
python -m dev_autopilot.demo --output ./autopilot-demo-091
```

Use Linux/WSL2 for real agent subprocess execution. This remains a CLI preview;
the outstanding live-provider, Colab recovery, eight-hour soak and final owner
acceptance criteria for 1.0.0 are unchanged.
