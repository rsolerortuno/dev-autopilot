from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_autopilot.adapters.sandbox import DockerAgentAdapter
from dev_autopilot.models import AgentCommand, ResultStatus

IMAGE = "registry.example.invalid/agent@sha256:" + "a" * 64


def adapter(**kwargs):
    return DockerAgentAdapter("agent", AgentCommand(command=("agent",)), image=IMAGE, **kwargs)


@pytest.mark.parametrize("image", ["agent:latest", "agent@sha256:abc", "agent@sha256:" + "g" * 64, ""])
def test_image_must_be_immutable_digest(image):
    with pytest.raises(ValueError, match="pinned"):
        DockerAgentAdapter("a", AgentCommand(command=("agent",)), image=image)


def test_command_contains_isolation_and_mount_policy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.os.name", "posix")
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.os.getuid", lambda: 1000)
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.os.getgid", lambda: 1000)
    (tmp_path / ".git").mkdir()
    command = adapter()._docker_command(tmp_path, tmp_path / "context.json", tmp_path / "output", "container")
    joined = " ".join(command)
    required = (
        "--pull never", "--read-only", "--cap-drop ALL", "no-new-privileges:true",
        "--network none", "--pids-limit 128", "--memory 512m", "--cpus 1",
        "/tmp:rw,noexec,nosuid,size=64m", "--user 1000:1000",
    )
    for item in required:
        assert item in joined
    assert "dst=/workspace/.git,readonly" in joined
    assert "DEV_AUTOPILOT_OUTPUT_FILE=/dev_autopilot/output/output.json" in joined


def test_linked_worktree_is_rejected(tmp_path: Path):
    (tmp_path / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    with pytest.raises(ValueError, match="standalone checkout"):
        adapter()._docker_command(tmp_path, tmp_path / "context.json", tmp_path / "output", "container")


def test_missing_docker_is_structured_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.shutil.which", lambda _: None)
    result = adapter().execute(task="x", repository=tmp_path, context={}, output_contract="json")
    assert result.status is ResultStatus.ERROR
    assert "Docker" in result.summary


def test_timeout_removes_container(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.os.name", "posix")
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.os.getuid", lambda: 1000)
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.os.getgid", lambda: 1000)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.shutil.which", lambda _: "docker")
    calls = []

    class FakeProcess:
        returncode = None

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("docker", timeout)
            self.returncode = -9
            return "out", "err"

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr("dev_autopilot.adapters.sandbox.subprocess.Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr("dev_autopilot.adapters.sandbox.subprocess.run", lambda command, **kwargs: calls.append(command))
    result = adapter().execute(task="x", repository=tmp_path, context={}, output_contract="json")
    assert result.status is ResultStatus.TIMEOUT
    assert "kill" in calls
    assert any(command[-3:-1] == ["rm", "-f"] for command in calls if isinstance(command, list))
