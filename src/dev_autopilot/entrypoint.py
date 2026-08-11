"""Top-level command router preserving the v0.6 CLI and adding eval authoring."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["eval"]:
        from dev_autopilot.eval_cli import main as eval_main

        return eval_main(args[1:])

    from dev_autopilot.cli import main as legacy_main

    return legacy_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
