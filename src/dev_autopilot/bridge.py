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

DEFAULT_CLAUDE_PERMISSION_MODE = "auto"


def _claude_permission_mode() -> str:
    """Return the permission mode used by the built-in Claude bridges.

    Dev Autopilot drives Claude non-interactively and requires it to run the
    project's own test/gate commands and write a JSON decision file. ``plan``
    is deliberately read-only and makes Claude stop for human input instead of
    honouring the output contract, which burns the whole retry budget. ``auto``
    is the non-interactive mode: routine operations proceed and dangerous ones
    are still classified and blocked. The mode stays overridable for older
    Claude CLIs that do not implement ``auto``.
    """

    mode = os.environ.get("DEV_AUTOPILOT_CLAUDE_PERMISSION_MODE", "").strip()
    return mode or DEFAULT_CLAUDE_PERMISSION_MODE


def _default_agent_command(env_name: str) -> list[str] | None:
    """Return the built-in command for a supported agent bridge."""

    claude_permission_mode = _claude_permission_mode()
    defaults: dict[str, tuple[str, ...]] = {
        "DEV_AUTOPILOT_CODEX_COMMAND": (
            "codex",
            "exec",
            "--full-auto",
            "-",
        ),
        "DEV_AUTOPILOT_AGY_COMMAND": (
            sys.executable,
            "-m",
            "dev_autopilot.agy_structured",
        ),
        "DEV_AUTOPILOT_CLAUDE_REVIEW_COMMAND": (
            "claude",
            "-p",
            "--permission-mode",
            claude_permission_mode,
        ),
        "DEV_AUTOPILOT_CLAUDE_SUPERVISOR_COMMAND": (
            "claude",
            "-p",
            "--permission-mode",
            claude_permission_mode,
        ),
    }

    command = defaults.get(env_name)
    if command is None:
        return None

    return list(command)


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

        if value:
            command = shlex.split(value)
        else:
            default_command = _default_agent_command(args.env)

            if default_command is None:
                print(
                    f"missing environment variable and no built-in default: {args.env}",
                    file=sys.stderr,
                )
                return 2

            command = default_command
    if not command:
        print("no agent command configured", file=sys.stderr)
        return 2
    context_path = Path(os.environ["DEV_AUTOPILOT_CONTEXT_FILE"])
    output_path = Path(os.environ["DEV_AUTOPILOT_OUTPUT_FILE"])
    envelope = context_path.read_text(encoding="utf-8")

    prompt = (
        "You are executing one bounded Dev Autopilot agent task.\n"
        "Read and obey the execution envelope below. Perform the requested task "
        "within its stated constraints.\n\n"
        "CRITICAL OUTPUT REQUIREMENT:\n"
        "Your FINAL RESPONSE must contain exactly one JSON object satisfying "
        "the output_contract in the envelope. Do not finish with prose, Markdown, "
        "a summary outside the JSON object, or any other format.\n\n"
        "DEV AUTOPILOT EXECUTION ENVELOPE:\n" + envelope
    )

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
        if command[:1] == ["claude"] and "plan" in command:
            print(
                "hint: the Claude bridge is running with --permission-mode plan, which is "
                "read-only and cannot satisfy the output contract; use "
                "--permission-mode auto (see DEV_AUTOPILOT_CLAUDE_PERMISSION_MODE)",
                file=sys.stderr,
            )
        return 3
    output_path.write_text(json.dumps(output, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
