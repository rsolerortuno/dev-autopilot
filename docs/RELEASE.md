# Release engineering

Release artifacts are built only after the reusable CI workflow passes on the
same commit. The workflow gates test coverage at 80%, Ruff lint and format,
strict mypy, distribution build, clean wheel installation, `pip check`, and
whitespace validation. The release job checks out the tag commit explicitly
and verifies that the tag, `pyproject.toml`, and wheel metadata contain the
same SemVer version.

The SBOM is generated after the wheel is installed into a fresh virtual
environment. That environment contains the installed product and its runtime
dependencies, so the SBOM is evidence for the shipped installation rather
than for the CI builder. The workflow also asserts that `dev-autopilot`,
`pydantic`, and `PyYAML` appear in the SBOM.

The workflow uploads an artifact bundle for review; it does not create or
publish a GitHub Release. A release decision must inspect the bundle and its
`SHA256SUMS.txt` after the workflow succeeds.

For local metadata validation after building, run:

```console
python scripts/check_release_metadata.py --tag v0.6.0 --dist dist
```
