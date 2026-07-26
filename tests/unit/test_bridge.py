from __future__ import annotations

import pytest

from dev_autopilot.bridge import _extract_json


def test_bridge_extracts_plain_and_fenced_json() -> None:
    assert _extract_json('{"passed": true, "summary": "ok", "findings": []}')["passed"] is True
    assert _extract_json('text\n```json\n{"decision":"APPROVE","summary":"ok","findings":[]}\n```')["decision"] == "APPROVE"


def test_bridge_rejects_missing_json() -> None:
    with pytest.raises(ValueError):
        _extract_json("not json")
