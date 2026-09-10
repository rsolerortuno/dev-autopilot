"""Small, deterministic helpers for subprocess environment and log hygiene."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_NAME = re.compile(r"(^|[._-])(secret|credentials?|token|password|passwd|private[-_]?key|api[-_]?key)([._-]|$)", re.I)
BASE_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SystemRoot", "SYSTEMROOT", "PATHEXT")


def is_sensitive_name(name: str) -> bool:
    return bool(SENSITIVE_NAME.search(name))


def redact(value: Any, secrets: tuple[str, ...]) -> Any:
    """Recursively redact exact secret values in JSON-like process results."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, secrets) for item in value)
    return value


def secret_values(environment: dict[str, str]) -> tuple[str, ...]:
    values = {value for name, value in environment.items() if is_sensitive_name(name) and value}
    return tuple(sorted(values, key=len, reverse=True))


def sensitive_values(value: Any) -> tuple[str, ...]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if is_sensitive_name(str(key)) and isinstance(item, str) and item:
                found.add(item)
            found.update(sensitive_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(sensitive_values(item))
    return tuple(sorted(found, key=len, reverse=True))
