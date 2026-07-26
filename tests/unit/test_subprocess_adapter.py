from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dev_autopilot.adapters.subprocess import ExecutableAgentAdapter
from dev_autopilot.models import AgentCommand, ResultStatus


def test_agent_git_metadata_write_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    (repository / "base.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
    code = (
        "import json,os,subprocess,pathlib;"
        "p=pathlib.Path('changed.txt');p.write_text('x');"
        "subprocess.run(['git','add','changed.txt'],check=True);"
        "pathlib.Path(os.environ['DEV_AUTOPILOT_OUTPUT_FILE']).write_text("
        "json.dumps({'summary':'done'}))"
    )
    adapter = ExecutableAgentAdapter("codex", AgentCommand(command=(sys.executable, "-c", code), timeout_seconds=30))
    result = adapter.execute(
        task="work",
        repository=repository,
        context={"gates": {"allow_git_writes": False}},
        output_contract="write JSON",
    )
    assert result.status is ResultStatus.SECURITY
    assert "git metadata" in result.summary
