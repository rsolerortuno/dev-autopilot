"""Chunk manifests and storage profiles (M04).

A manifest is the contract that ties a split to its reassembly.  It records the
whole-file SHA-256, the ordered parts with per-part offset, size and SHA-256,
and the profile used.  Reassembly is defined entirely by the manifest: given
the parts and the manifest, the original file can be reconstructed and verified
byte-for-byte, and a resumed split can skip any part whose stored SHA-256
already matches.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from dev_autopilot.models import ContractModel, NonEmptyString

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

# Split profiles from the product charter.  Part size is the only knob that
# matters for correctness; everything else is derived.  There is deliberately
# no 24 GB ceiling -- whole-file size is unbounded and limited only by storage.
STORAGE_PROFILES: dict[str, int] = {
    # Sized just under a common 100 MB upload boundary.
    "chatgpt_upload": 95_000_000,
    # 1 GiB parts for in-Colab processing.
    "colab_processing": 1_073_741_824,
    # 256 MiB parts to survive flaky connections with cheap resume.
    "unstable_network": 268_435_456,
}
DEFAULT_PROFILE = "colab_processing"


class FormatStrategy(StrEnum):
    """How a file is divided.  ``byte_range`` is always safe; the others are
    format-aware hints a caller may choose for record-aligned splitting."""

    BYTE_RANGE = "byte_range"
    ARCHIVE_SHARD = "archive_shard"
    PARQUET_ROW_GROUP = "parquet_row_group"
    ZARR_CHUNK = "zarr_chunk"
    HDF5_REASSEMBLY = "hdf5_reassembly"
    RECORD_SHARD = "record_shard"


def resolve_part_size(*, profile: str | None = None, part_size_bytes: int | None = None) -> int:
    """Resolve an effective part size from an explicit value or a named profile."""
    if part_size_bytes is not None:
        if part_size_bytes <= 0:
            raise ValueError("part_size_bytes must be positive")
        return part_size_bytes
    name = profile or DEFAULT_PROFILE
    if name not in STORAGE_PROFILES:
        raise ValueError(f"unknown storage profile {name!r}; known: {sorted(STORAGE_PROFILES)}")
    return STORAGE_PROFILES[name]


class ChunkEntry(ContractModel):
    """One contiguous part of the source file."""

    index: Annotated[int, Field(ge=0)]
    name: NonEmptyString
    offset: Annotated[int, Field(ge=0)]
    size: Annotated[int, Field(ge=0)]
    sha256: Sha256

    @property
    def end(self) -> int:
        return self.offset + self.size


class ChunkManifest(ContractModel):
    """The full, verifiable description of a split file."""

    source_name: NonEmptyString
    total_size: Annotated[int, Field(ge=0)]
    whole_sha256: Sha256
    part_size_bytes: Annotated[int, Field(gt=0)]
    strategy: FormatStrategy = FormatStrategy.BYTE_RANGE
    parts: tuple[ChunkEntry, ...] = Field(default_factory=tuple)

    @field_validator("parts", mode="before")
    @classmethod
    def freeze_parts(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def check_contiguity(self) -> ChunkManifest:
        expected_offset = 0
        for position, part in enumerate(self.parts):
            if part.index != position:
                raise ValueError(f"part index {part.index} out of order at position {position}")
            if part.offset != expected_offset:
                raise ValueError(f"part {part.index} offset {part.offset} is not contiguous (expected {expected_offset})")
            expected_offset += part.size
        if self.parts and expected_offset != self.total_size:
            raise ValueError(f"parts cover {expected_offset} bytes but total_size is {self.total_size}")
        return self

    @property
    def is_complete(self) -> bool:
        return bool(self.parts) and self.parts[-1].end == self.total_size

    def part_by_index(self, index: int) -> ChunkEntry:
        for part in self.parts:
            if part.index == index:
                return part
        raise KeyError(f"no part with index {index}")
