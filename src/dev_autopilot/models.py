"""Immutable serializable contracts for configuration and runtime records."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls: type[ModelT], value: dict[str, Any]) -> ModelT:
        return cls.model_validate_json(json.dumps(value))

    @classmethod
    def from_json(cls: type[ModelT], value: str | bytes) -> ModelT:
        return cls.model_validate_json(value)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_repository_path(value: str) -> str:
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
        raise ValueError("trailing '/' is ambiguous; use kind='tree' for a directory tree")
    normalized = PurePosixPath(value).as_posix()
    if normalized in {"", "."}:
        raise ValueError("path must identify a repository entry")
    return normalized


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
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
            result.append("[" + content.replace("\\", r"\\") + "]")
            index = end
        else:
            result.append(re.escape(char))
        index += 1
    result.append("$")
    return re.compile("".join(result))


class FilePathRule(ContractModel):
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
        return _normalize_repository_path(candidate) == self.path


class TreePathRule(ContractModel):
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
        normalized = _normalize_repository_path(candidate)
        return normalized == self.path or normalized.startswith(f"{self.path}/")


class GlobPathRule(ContractModel):
    kind: Literal["glob"]
    pattern: str

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str) -> str:
        normalized = _normalize_repository_path(value)
        if not any(character in normalized for character in _GLOB_META):
            raise ValueError("glob rules must contain explicit glob syntax")
        _glob_to_regex(normalized)
        return normalized

    def matches(self, candidate: str) -> bool:
        normalized = _normalize_repository_path(candidate)
        return _glob_to_regex(self.pattern).fullmatch(normalized) is not None


PathRule = Annotated[FilePathRule | TreePathRule | GlobPathRule, Field(discriminator="kind")]
_PATH_RULE_ADAPTER: TypeAdapter[PathRule] = TypeAdapter(PathRule)


class TestCommands(ContractModel):
    baseline: NonEmptyString
    fast: NonEmptyString
    final: NonEmptyString


class AgentCommand(ContractModel):
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: Annotated[int, Field(gt=0)] = 3600

    @field_validator("command", mode="before")
    @classmethod
    def freeze_command(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class AgentSettings(ContractModel):
    codex: AgentCommand | None = None
    agy: AgentCommand | None = None
    claude_reviewer: AgentCommand | None = None
    claude_supervisor: AgentCommand | None = None


class RetryPolicySpec(ContractModel):
    delays_seconds: tuple[int, ...] = (60, 300, 900, 1800, 3600)
    max_attempts: Annotated[int, Field(gt=0)] = 8
    jitter_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.1

    @field_validator("delays_seconds", mode="before")
    @classmethod
    def freeze_delays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("delays_seconds")
    @classmethod
    def validate_delays(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(delay < 0 for delay in value):
            raise ValueError("retry delays must be a non-empty list of non-negative values")
        return value


class GatePolicy(ContractModel):
    allow_network: bool = False
    allow_git_writes: bool = False
    require_clean_baseline: bool = False
    max_changed_files: Annotated[int, Field(gt=0)] = 200
    max_context_bytes: Annotated[int, Field(gt=0)] = 2_000_000


class ReviewPolicy(ContractModel):
    max_correction_rounds: Annotated[int, Field(ge=0)] = 3
    repair_malformed_output_once: bool = True
    require_agy: bool = True


class JobSpecification(ContractModel):
    name: NonEmptyString
    objective: NonEmptyString
    repository: NonEmptyString
    allowed_paths: tuple[PathRule, ...] = Field(min_length=1)
    test_commands: TestCommands
    agents: AgentSettings = AgentSettings()
    retry_policy: RetryPolicySpec = RetryPolicySpec()
    gates: GatePolicy = GatePolicy()
    review: ReviewPolicy = ReviewPolicy()
    scientific_invariants: tuple[str, ...] = ()

    @field_validator("allowed_paths", "scientific_invariants", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

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
                raise ValueError(f"duplicate normalized allowed-path rule: {key}")
            seen.add(key)
        return self

    @property
    def configuration_id(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def allows_path(self, candidate: str) -> bool:
        return any(rule.matches(candidate) for rule in self.allowed_paths)


class RunIdentity(ContractModel):
    run_id: UUID
    job_name: NonEmptyString
    configuration_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class TransitionEvent(ContractModel):
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
    owner: NonEmptyString
    count: Annotated[int, Field(ge=0)]
    error_class: ErrorClass
    next_attempt_at: datetime | None = None
    last_reason: NonEmptyString | None = None

    @field_validator("next_attempt_at")
    @classmethod
    def normalize_next_attempt(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("next_attempt_at must include a timezone")
        return value.astimezone(UTC)


class FailureRecord(ContractModel):
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


class RunRecord(ContractModel):
    run_id: UUID
    job: JobSpecification
    state: WorkflowState
    resume_state: WorkflowState | None = None
    stop_reason: str | None = None
    failure_class: ErrorClass | None = None
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool = False
    approved_at: datetime | None = None
    archived_at: datetime | None = None
    correction_rounds: Annotated[int, Field(ge=0)] = 0


class ResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    QUOTA = "QUOTA"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    MALFORMED = "MALFORMED"
    SECURITY = "SECURITY"


class ExecutionResult(ContractModel):
    status: ResultStatus
    summary: NonEmptyString
    output: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    quota_reset_at: datetime | None = None


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    HUMAN_DECISION = "HUMAN_DECISION"


class ReviewReport(ContractModel):
    decision: ReviewDecision
    summary: NonEmptyString
    findings: tuple[str, ...] = ()

    @field_validator("findings", mode="before")
    @classmethod
    def freeze_findings(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class AuditReport(ContractModel):
    passed: bool
    summary: NonEmptyString
    findings: tuple[str, ...] = ()

    @field_validator("findings", mode="before")
    @classmethod
    def freeze_findings(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


def validate_path_rule(value: dict[str, Any]) -> FilePathRule | TreePathRule | GlobPathRule:
    return _PATH_RULE_ADAPTER.validate_python(value, strict=True)
