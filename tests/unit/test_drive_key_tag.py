from __future__ import annotations

import hashlib

from dev_autopilot.storage.drive_backend import DriveStorageBackend


def test_drive_logical_key_tag_is_sha256_and_fixed_size() -> None:
    key = "projects/targetintel/" + ("very-long-logical-storage-component-" * 100) + "/artifact.json"

    tag = DriveStorageBackend._logical_key_tag(key)

    assert tag == hashlib.sha256(key.encode("utf-8")).hexdigest()
    assert len(tag) == 64
    assert tag.isascii()
    assert len(b"devAutopilotKey") + len(tag.encode()) < 124


def test_drive_logical_key_tag_is_deterministic_and_key_specific() -> None:
    first = "a/" + ("x" * 500)
    second = "b/" + ("x" * 500)

    assert DriveStorageBackend._logical_key_tag(first) == DriveStorageBackend._logical_key_tag(first)
    assert DriveStorageBackend._logical_key_tag(first) != DriveStorageBackend._logical_key_tag(second)
