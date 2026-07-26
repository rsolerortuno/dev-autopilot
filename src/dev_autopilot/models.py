"""Immutable, serializable domain models for Dev Autopilot."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from dev_autopilot.errors import ErrorClass
from dev_autopilot.states import WorkflowState

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ModelT = TypeVar("ModelT", bound="ContractModel")
_GLOB_META = frozenset("*?[")


class ContractModel(BaseModel):
    """Shared strict, immutable contract with JSON round-trip helpers."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""

        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """Serialize using deterministic JSON formatting."""

        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls: type[ModelT], value: dict[str, Any]) -> ModelT:
        """Deserialize a JSON-compatible mapping."""

        return cls.model_validate_json(json.dumps(value))

    @classmethod
    def from_json(cls: type[ModelT], value: str | bytes) -> ModelT:
        """Deserialize a JSON document."""

        return cls.model_validate_json(value)


def _normalize_repository_path(value: str) -> str:
    """Validate and normalize one repository-relative POSIX path."""

    if not value.strip():
        raise ValueError("path must not be empty")
    if value != value.strip():
        raise ValueError("path must not have leading or trailing whitespace")
    if "\\" in value:
        raise ValueError("path must use '/' as its separator")
    if value.startswith("/") or PureWindowsPath(value).is_absolute():
        raise ValueError("absolute paths are not allowed")

    parts = value.split("/")
    if ".." in parts:
        raise ValueError("'..' path traversal is not allowed")
    if value.endswith("/"):
        raise ValueError(
            "trailing '/' is ambiguous; use kind='tree' for a complete directory tree"
        )

    normalized = PurePosixPath(value).as_posix()
    if normalized in {"", "."}:
        raise ValueError("path must identify a repository entry")
    return normalized


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a documented repository-root glob without fnmatch quirks."""

    index = 0
    result = ["^"]
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 1
                if index + 1 < len(pattern) and pattern[index + 1] == "/":
                    index += 1
                    result.append("(?:.*/)?")
                else:
                    result.append(".*")
            else:
                result.append("[^/]*")
        elif char == "?":
            result.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                raise ValueError("glob contains an unterminated character range")
            content = pattern[index + 1 : end]
            if not content:
                raise ValueError("glob contains an empty character range")
            if content[0] == "!":
                content = "^" + content[1:]
            escaped = content.replace("\\", r"\\").replace("]", r"\]")
            result.append("[" + escaped + "]")
            index = end
        else:
            result.append(re.escape(char))
        index += 1
    result.append("$")
    return re.compile("".join(result))


class FilePathRule(ContractModel):
    """Allow exactly one repository-relative file path."""

    kind: Literal["file"]
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = _normalize_repository_path(value)
        if any(character in normalized for character in _GLOB_META):
            raise ValueError("file rules cannot contain glob syntax")
        return normalized

    def matches(self, candidate: str) -> bool:
        """Return whether *candidate* is this exact file."""

        return _normalize_repository_path(candidate) == self.path


class TreePathRule(ContractModel):
    """Allow one directory and every path below it."""

    kind: Literal["tree"]
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = _normalize_repository_path(value)
        if any(character in normalized for character in _GLOB_META):
            raise ValueError("tree rules cannot contain glob syntax")
        return normalized

    def matches(self, candidate: str) -> bool:
        """Return whether *candidate* is the directory or one of its descendants."""

        normalized = _normalize_repository_path(candidate)
        return normalized == self.path or normalized.startswith(f"{self.path}/")


class GlobPathRule(ContractModel):
    """Allow paths matched by an explicit repository-root glob.

    ``*`` and ``?`` never cross ``/``; ``**`` does. Matching is case-sensitive
    and is always against the complete repository-relative path.
    """

    kind: Literal["glob"]
    pattern: str

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str) -> str:
        normalized = _normalize_repository_path(value)
        if not any(character in normalized for character in _GLOB_META):
            raise ValueError(
                "glob rules must contain explicit glob syntax; use kind='file' "
                "or kind='tree' otherwise"
            )
        _glob_to_regex(normalized)
        return normalized

    def matches(self, candidate: str) -> bool:
        """Return whether *candidate* matches this complete-path glob."""

        normalized = _normalize_repository_path(candidate)
        return _glob_to_regex(self.pattern).fullmatch(normalized) is not None


PathRule = Annotated[
    FilePathRule | TreePathRule | GlobPathRule,
    Field(discriminator="kind"),
]
_PATH_RULE_ADAPTER: TypeAdapter[PathRule] = TypeAdapter(PathRule)


class TestCommands(ContractModel):
    """Separate commands for baseline, fast-feedback, and final validation."""

    baseline: NonEmptyString
    fast: NonEmptyString
    final: NonEmptyString


class JobSpecification(ContractModel):
    """Validated configuration for one general-purpose development job."""

    name: NonEmptyString
    objective: NonEmptyString
    repository: NonEmptyString
    allowed_paths: tuple[PathRule, ...] = Field(min_length=1)
    test_commands: TestCommands

    @field_validator("allowed_paths", mode="before")
    @classmethod
    def freeze_allowed_paths(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("repository must not contain a NUL byte")
        return value

    @model_validator(mode="after")
    def reject_duplicate_rules(self) -> JobSpecification:
        seen: set[tuple[str, str]] = set()
        for rule in self.allowed_paths:
            value = rule.pattern if isinstance(rule, GlobPathRule) else rule.path
            key = (rule.kind, value)
            if key in seen:
                message = f"duplicate normalized allowed-path rule: {rule.kind}:{value}"
                raise ValueError(message)
            seen.add(key)
        return self

    @property
    def configuration_id(self) -> str:
        """SHA-256 identity of the canonical validated configuration."""

        canonical = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def allows_path(self, candidate: str) -> bool:
        """Return whether any configured rule allows *candidate*."""

        return any(rule.matches(candidate) for rule in self.allowed_paths)


class RunIdentity(ContractModel):
    """Stable identity of a run and the configuration that created it."""

    run_id: UUID
    job_name: NonEmptyString
    configuration_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class TransitionEvent(ContractModel):
    """A recorded state transition; sequencing is supplied by future persistence."""

    sequence: Annotated[int, Field(ge=0)]
    run_id: UUID
    from_state: WorkflowState | None
    to_state: WorkflowState
    occurred_at: datetime
    reason: NonEmptyString | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)


class RetryState(ContractModel):
    """Retry count owned by one named agent or deterministic phase."""

    owner: NonEmptyString
    count: Annotated[int, Field(ge=0)]
    error_class: ErrorClass


class FailureRecord(ContractModel):
    """A classified failure with a mandatory human-readable reason."""

    run_id: UUID
    state: WorkflowState
    error_class: ErrorClass
    reason: NonEmptyString
    occurred_at: datetime
    owner: NonEmptyString | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)


def validate_path_rule(
    value: dict[str, Any],
) -> FilePathRule | TreePathRule | GlobPathRule:
    """Validate a standalone path rule using the same discriminated contract."""

    return _PATH_RULE_ADAPTER.validate_python(value, strict=True)
