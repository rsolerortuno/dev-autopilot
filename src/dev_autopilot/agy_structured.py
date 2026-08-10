#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "summary": {"type": "string", "minLength": 1},
        "findings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["passed", "summary", "findings"],
    "additionalProperties": False,
}


def extract_embedded_json(text: str) -> Any:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()

    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value

    return None


def find_audit(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if {"passed", "summary", "findings"}.issubset(value):
            passed = value["passed"]
            summary = value["summary"]
            findings = value["findings"]

            if (
                isinstance(passed, bool)
                and isinstance(summary, str)
                and summary.strip()
                and isinstance(findings, list)
                and all(isinstance(item, str) for item in findings)
            ):
                return {
                    "passed": passed,
                    "summary": summary,
                    "findings": findings,
                }

        for child in value.values():
            found = find_audit(child)
            if found is not None:
                return found

    elif isinstance(value, list):
        for child in value:
            found = find_audit(child)
            if found is not None:
                return found

    elif isinstance(value, str):
        embedded = extract_embedded_json(value)
        if embedded is not None and embedded != value:
            return find_audit(embedded)

    return None


def describe_event(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or event.get("event") or event.get("step_type") or "progress")

    step_type = event.get("step_type")

    tool_info = event.get("tool_info")
    if isinstance(tool_info, dict):
        tool_name = tool_info.get("name") or tool_info.get("tool_name") or tool_info.get("canonical_name")
        if tool_name:
            return f"{event_type} | tool={tool_name}"

    if step_type:
        return f"{event_type} | step={step_type}"

    return event_type


def _agy_command(prompt: str) -> list[str]:
    """Build the isolated structured AGY audit command."""
    return [
        "agy",
        "--new-project",
        "--print",
        prompt,
        "--output-format",
        "stream-json",
        "--json-schema",
        json.dumps(AUDIT_SCHEMA),
        "--disable-slash-commands",
        "--mode",
        "plan",
        "--print-timeout",
        "30m",
    ]


def main() -> int:
    prompt = sys.stdin.read()

    repository = Path.cwd()

    prompt = (
        f"""
DEV AUTOPILOT AGY AUDIT BOUNDARY

You are a READ-ONLY reviewer operating inside exactly this repository:

{repository}

STRICT TOOL RULES:
- Do NOT use run_command.
- Do NOT execute shell commands.
- Do NOT inspect parent directories or /home.
- Do NOT search outside the current repository.
- Do NOT modify files.
- Do NOT use write_to_file, replace_file_content,
  multi_replace_file_content, notebook_edit, or other write tools.
- Use only read-only repository inspection tools such as:
  list_dir, view_file, grep_search, sed_file.
- The current working directory is already the repository root.
- Repository paths in the supplied context are authoritative.
- Finish by returning the exact structured JSON required by the JSON schema.

ORIGINAL DEV AUTOPILOT ENVELOPE:

"""
        + prompt
    )

    print(
        f"[AGY] starting structured audit ({len(prompt):,} prompt chars)",
        file=sys.stderr,
        flush=True,
    )

    process = subprocess.Popen(
        _agy_command(prompt),
        text=True,
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=1,
    )

    if process.stdout is None:
        print("[AGY] stdout unavailable", file=sys.stderr)
        return 3

    audit: dict[str, Any] | None = None

    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(
                f"[AGY] non-JSON stream output: {line[:300]}",
                file=sys.stderr,
                flush=True,
            )
            continue

        if isinstance(event, dict):
            print(
                f"[AGY] {describe_event(event)}",
                file=sys.stderr,
                flush=True,
            )

        found = find_audit(event)
        if found is not None:
            audit = found

    return_code = process.wait()

    if return_code != 0:
        print(
            f"[AGY] process exited with {return_code}",
            file=sys.stderr,
        )
        return return_code

    if audit is None:
        print(
            "[AGY] stream completed without a valid AuditReport",
            file=sys.stderr,
        )
        return 3

    print(
        "[AGY] audit completed successfully",
        file=sys.stderr,
        flush=True,
    )

    # stdout remains exactly the JSON expected by dev_autopilot.bridge.
    print(json.dumps(audit, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
