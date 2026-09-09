from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client

from dev_autopilot.db import SQLiteStore
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
