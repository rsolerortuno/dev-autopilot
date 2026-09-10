from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client

from dev_autopilot.db import SQLiteStore
from dev_autopilot.events import EventType
from dev_autopilot.mcp_server import create_server


def test_mcp_protocol_tools_and_permission_denial(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "science.py").write_text("def normalize(): pass\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    database = tmp_path / "state.db"
    SQLiteStore(database)
    server = create_server(root, database, scopes=frozenset({"repository:read"}))

    async def exercise() -> None:
        async with Client(server) as client:
            result = await client.call_tool("search_repository", {"query": "normalize"})
            assert "science.py" in str(result)
            denied = await client.call_tool("run_status", {"run_id": "00000000-0000-0000-0000-000000000000"})
            assert denied.is_error

    asyncio.run(exercise())


def test_mcp_status_and_evidence_are_read_only_summaries(tmp_path: Path, job) -> None:
    root = tmp_path / "repo"
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "science.py").write_text("def normalize(): pass\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    database = tmp_path / "state.db"
    store = SQLiteStore(database)
    run = store.create_run(job.model_copy(update={"repository": str(root)}))
    store.append_event(
        run.run_id,
        EventType.PHASE_STARTED,
        actor="system",
        reason="phase started",
        payload={"prompt": "secret prompt must not escape", "token": "secret-token"},
    )
    before = store.list_events(run.run_id)
    server = create_server(root, database, scopes=frozenset({"runs:read", "evidence:read"}))

    async def exercise() -> None:
        async with Client(server) as client:
            status = await client.call_tool("run_status", {"run_id": str(run.run_id)})
            evidence = await client.call_tool("evidence_summary", {"run_id": str(run.run_id)})
            unknown = await client.call_tool("run_status", {"run_id": "00000000-0000-0000-0000-000000000000"})
            unknown_evidence = await client.call_tool(
                "evidence_summary", {"run_id": "00000000-0000-0000-0000-000000000000"}
            )
            rendered = f"{status}{evidence}"
            assert str(run.run_id) in rendered
            assert "PHASE_STARTED" in rendered
            assert "secret prompt" not in rendered
            assert "secret-token" not in rendered
            assert unknown.is_error and unknown_evidence.is_error

    asyncio.run(exercise())
    assert store.list_events(run.run_id) == before
