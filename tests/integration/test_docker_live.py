from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dev_autopilot.adapters.sandbox import DockerAgentAdapter
from dev_autopilot.models import AgentCommand, ResultStatus

IMAGE = os.environ.get("DEV_AUTOPILOT_DOCKER_IMAGE")
pytestmark = pytest.mark.integration


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    return repository


@pytest.mark.skipif(not IMAGE, reason="live Docker smoke test is opt-in")
def test_docker_runtime_is_nonroot_readonly_networkless_and_cleans_timeout(tmp_path: Path) -> None:
    assert IMAGE is not None
    repository = _repository(tmp_path)
    readonly_probe = (
        "def write_probe():\n"
        " try:\n"
        "  open('/etc/sandbox-write-check','w').write('x')\n"
        " except OSError:\n"
        "  return False\n"
        " return True"
    )
    command = (
        "python",
        "-c",
        "import json,os,pathlib; "
        "uid=os.getuid(); "
        "assert uid != 0; "
        "assert int(open('/proc/self/status').read().split('CapEff:\\t',1)[1].splitlines()[0],16)==0; "
        "assert set(os.listdir('/sys/class/net')) <= {'lo'}; "
        "root_mount=next(line for line in open('/proc/mounts') if line.split()[1]=='/'); "
        "assert 'ro' in line.split()[3].split(','); "
        f"exec({readonly_probe!r}); "
        "assert not write_probe(); "
        "pathlib.Path(os.environ['DEV_AUTOPILOT_OUTPUT_FILE']).write_text(json.dumps({'uid':uid,'ok':True}))",
    )
    adapter = DockerAgentAdapter(
        "live-smoke",
        AgentCommand(command=command, timeout_seconds=10),
        image=IMAGE,
        network_policy="none",
    )
    result = adapter.execute(task="runtime smoke", repository=repository, context={}, output_contract="json")
    assert result.status is ResultStatus.SUCCESS, result
    assert isinstance(result.output, dict)
    assert result.output.get("ok") is True
    assert isinstance(result.output.get("uid"), int) and result.output["uid"] != 0

    before = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=dev-autopilot-", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    timeout_adapter = DockerAgentAdapter(
        "live-timeout",
        AgentCommand(command=("python", "-c", "import time; time.sleep(30)"), timeout_seconds=1),
        image=IMAGE,
        network_policy="none",
    )
    timed_out = timeout_adapter.execute(task="timeout smoke", repository=repository, context={}, output_contract="json")
    assert timed_out.status is ResultStatus.TIMEOUT
    names = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=dev-autopilot-", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert set(names) <= set(before)
