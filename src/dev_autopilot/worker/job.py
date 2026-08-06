"""Validated worker job, claim, blocker and checkpoint contracts (M05)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator

from dev_autopilot.models import ContractModel, NonEmptyString
from dev_autopilot.storage.backend import validate_storage_key

SafeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


def validate_relative_local_path(value: str) -> str:
    if not value or value != value.strip() or "\x00" in value or "\\" in value:
        raise ValueError("local path must be a clean POSIX relative path")
    if value.startswith("/") or PureWindowsPath(value).is_absolute():
        raise ValueError("local path must be relative")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("local path may not contain traversal or ambiguous components")
    return path.as_posix()


class ResourceClass(StrEnum):
    CPU = "cpu"
    HIGH_RAM = "high_ram"
    GPU = "gpu"
    TPU = "tpu"
    STORAGE = "storage"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class MountMode(StrEnum):
    REASSEMBLE = "reassemble"
    STREAM = "stream"
    DIRECT = "direct"


class JobInput(ContractModel):
    key_prefix: NonEmptyString
    mount_mode: MountMode = MountMode.REASSEMBLE
    local_name: NonEmptyString

    @field_validator("key_prefix")
    @classmethod
    def validate_key_prefix(cls, value: str) -> str:
        return validate_storage_key(value.rstrip("/"))

    @field_validator("local_name")
    @classmethod
    def validate_local_name(cls, value: str) -> str:
        return validate_relative_local_path(value)


class WorkerJob(ContractModel):
    job_id: SafeId
    project_id: SafeId
    milestone_id: Annotated[str, StringConstraints(pattern=r"^M\d{2,}$")] | None = None
    resource_class: ResourceClass = ResourceClass.CPU
    entrypoint: tuple[NonEmptyString, ...] = Field(min_length=1)
    inputs: tuple[JobInput, ...] = ()
    output_prefix: NonEmptyString
    checkpoint_interval_seconds: Annotated[int, Field(gt=0)] = 300
    timeout_seconds: Annotated[int, Field(gt=0)] = 36_000
    max_attempts: Annotated[int, Field(gt=0)] = 4
    env_allowlist: tuple[EnvName, ...] = ()
    output_split_threshold_bytes: Annotated[int, Field(gt=0)] = 1_073_741_824
    output_part_size_bytes: Annotated[int, Field(gt=0)] = 95_000_000
    minimum_free_disk_bytes: Annotated[int, Field(ge=0)] = 0
    minimum_ram_bytes: Annotated[int, Field(ge=0)] = 0
    minimum_gpu_memory_bytes: Annotated[int, Field(ge=0)] = 0

    @field_validator("entrypoint", "inputs", "env_allowlist", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("output_prefix")
    @classmethod
    def validate_output_prefix(cls, value: str) -> str:
        return validate_storage_key(value.rstrip("/"))


class Blocker(ContractModel):
    blocker_id: SafeId
    milestone: Annotated[str, StringConstraints(pattern=r"^M\d{2,}$")] | None = None
    reason: NonEmptyString
    single_required_action: NonEmptyString
    resume_when_key_exists: NonEmptyString

    @field_validator("resume_when_key_exists")
    @classmethod
    def validate_resume_key(cls, value: str) -> str:
        return validate_storage_key(value)

    def is_cleared(self, backend: Any) -> bool:
        if not backend.exists(self.resume_when_key_exists):
            return False
        try:
            import json

            raw = json.loads(backend.get_bytes(self.resume_when_key_exists).decode("utf-8"))
            expires_at = raw.get("expires_at") if isinstance(raw, dict) else None
            if expires_at:
                return datetime.fromisoformat(str(expires_at)).astimezone(UTC) > datetime.now(UTC)
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            return False
        return True


class Checkpoint(ContractModel):
    job_id: SafeId
    sequence: Annotated[int, Field(ge=0)]
    progress: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class JobClaim(ContractModel):
    job: WorkerJob
    owner_token: NonEmptyString
    fencing_token: NonEmptyString
    attempt: Annotated[int, Field(ge=0)]
    lease_expires_at: datetime

    @property
    def job_id(self) -> str:
        return self.job.job_id

    @property
    def resource_class(self) -> ResourceClass:
        return self.job.resource_class
