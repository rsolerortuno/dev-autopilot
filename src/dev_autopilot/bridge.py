"""Bridge arbitrary subscription CLIs to the file-based agent adapter protocol."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _extract_json(text: str) -> dict[str, object]:
    stripped = text.strip()
    candidates = [stripped]
    candidates.extend(match.group(1) for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("agent stdout did not contain a JSON object")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev-autopilot-bridge")
    parser.add_argument("--env", help="environment variable containing the agent command")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if args.env:
        value = os.environ.get(args.env, "").strip()
        if not value:
            print(f"missing environment variable: {args.env}", file=sys.stderr)
            return 2
        command = shlex.split(value)
    if not command:
        print("no agent command configured", file=sys.stderr)
        return 2
    context_path = Path(os.environ["DEV_AUTOPILOT_CONTEXT_FILE"])
    output_path = Path(os.environ["DEV_AUTOPILOT_OUTPUT_FILE"])
    prompt = context_path.read_text(encoding="utf-8")
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        return completed.returncode
    try:
        output = _extract_json(completed.stdout)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    output_path.write_text(json.dumps(output, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
