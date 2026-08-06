from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from dev_autopilot.adapters.subprocess import ExecutableAgentAdapter, LocalCommandAdapter
from dev_autopilot.models import AgentCommand, ResultStatus

GIT = "/usr/bin/git"


def _git_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run([GIT, "init", "-q"], cwd=path, check=True)
    (path / "base.txt").write_text("base", encoding="utf-8")
    subprocess.run([GIT, "add", "base.txt"], cwd=path, check=True)
    environment = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    subprocess.run([GIT, "commit", "-qm", "base"], cwd=path, env=environment, check=True)
    return path


def test_agent_git_metadata_write_is_rejected(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path / "repo")
    code = (
        "import json,os,subprocess,pathlib;"
        "p=pathlib.Path('changed.txt');p.write_text('x');"
        "subprocess.run(['/usr/bin/git','add','changed.txt'],check=True);"
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


def test_git_metadata_uses_real_index_for_normal_and_linked_worktree(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path / "repo")
    normal = ExecutableAgentAdapter._git_metadata(repository)
    assert normal[0] and normal[2]
    worktree = tmp_path / "linked"
    subprocess.run([GIT, "worktree", "add", "-qb", "linked", str(worktree)], cwd=repository, check=True)
    assert (worktree / ".git").is_file()
    linked_before = ExecutableAgentAdapter._git_metadata(worktree)
    (worktree / "linked.txt").write_text("x", encoding="utf-8")
    subprocess.run([GIT, "add", "linked.txt"], cwd=worktree, check=True)
    assert ExecutableAgentAdapter._git_metadata(worktree) != linked_before


def test_agent_detects_ref_mutation_and_allows_ordinary_file_edit(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path / "repo")
    edit_code = (
        "import json,os,pathlib; pathlib.Path('ordinary.txt').write_text('ok'); "
        "pathlib.Path(os.environ['DEV_AUTOPILOT_OUTPUT_FILE']).write_text(json.dumps({'summary':'done'}))"
    )
    adapter = ExecutableAgentAdapter("codex", AgentCommand(command=(sys.executable, "-c", edit_code), timeout_seconds=5))
    allowed = adapter.execute(task="work", repository=repository, context={"gates": {}}, output_contract="json")
    assert allowed.status is ResultStatus.SUCCESS
    ref_code = (
        "import os,subprocess,time; "
        "subprocess.run(['/usr/bin/git','update-ref','refs/heads/unsafe','HEAD'],check=True); "
        "time.sleep(.01)"
    )
    adapter = ExecutableAgentAdapter("codex", AgentCommand(command=(sys.executable, "-c", ref_code), timeout_seconds=5))
    result = adapter.execute(task="work", repository=repository, context={"gates": {}}, output_contract="json")
    assert result.status is ResultStatus.SECURITY


def test_timeout_prefers_security_and_terminates_child_process(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path / "repo")
    code = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "pathlib.Path('child.pid').write_text(str(child.pid)); "
        "index=pathlib.Path('.git/index'); index.write_bytes(index.read_bytes()+b'x'); "
        "time.sleep(60)"
    )
    adapter = ExecutableAgentAdapter("codex", AgentCommand(command=(sys.executable, "-c", code), timeout_seconds=5))
    result = adapter.execute(task="work", repository=repository, context={"gates": {}}, output_contract="json")
    assert result.status is ResultStatus.SECURITY
    pid = int((repository / "child.pid").read_text(encoding="utf-8"))
    for _ in range(40):
        try:
            __import__("os").kill(pid, 0)
        except ProcessLookupError:
            break
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file() and stat_path.read_text(encoding="utf-8").split()[2] == "Z":
            break  # terminated zombie awaiting init reaping; it cannot execute
        time.sleep(0.05)
    else:
        pytest.fail("timed-out child process was still running")


def test_local_commands_use_argv_unless_shell_is_explicit(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    command = (sys.executable, "-c", "import pathlib; pathlib.Path('marker').write_text('ok')")
    result = LocalCommandAdapter().run(command, repository=tmp_path, timeout_seconds=5)
    assert result.status is ResultStatus.SUCCESS and marker.read_text(encoding="utf-8") == "ok"
    rejected = LocalCommandAdapter().run("echo one && echo two", repository=tmp_path, timeout_seconds=5)
    assert rejected.status is ResultStatus.MALFORMED
    assert "allow_shell=True" in rejected.summary
    trusted = LocalCommandAdapter().run("echo trusted > shell-marker", repository=tmp_path, timeout_seconds=5, allow_shell=True)
    assert trusted.status is ResultStatus.SUCCESS
    assert (tmp_path / "shell-marker").read_text(encoding="utf-8").strip() == "trusted"
