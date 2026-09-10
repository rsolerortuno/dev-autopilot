"""Validate that a release tag, project metadata, and built wheel agree."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path


def _metadata_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        for line in archive.read(metadata).decode("utf-8").splitlines():
            if line.startswith("Version: "):
                return line.removeprefix("Version: ").strip()
    raise ValueError(f"wheel has no Version metadata: {wheel}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag, for example v0.6.0")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    match = re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", args.tag)
    if match is None:
        print(f"release tag must be SemVer with a v prefix: {args.tag}", file=sys.stderr)
        return 1
    import tomllib

    project_version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    tag_version = args.tag[1:]
    wheels = sorted(args.dist.glob("*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one wheel in {args.dist}, found {len(wheels)}", file=sys.stderr)
        return 1
    wheel_version = _metadata_version(wheels[0])
    versions = {"tag": tag_version, "pyproject": project_version, "wheel": wheel_version}
    if len(set(versions.values())) != 1:
        print(f"release versions do not agree: {versions}", file=sys.stderr)
        return 1
    print(f"release metadata aligned: v{tag_version} ({wheels[0].name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
