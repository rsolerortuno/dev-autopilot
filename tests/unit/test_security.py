from __future__ import annotations

import json
import sys

import pytest

from dev_autopilot.adapters.subprocess import ExecutableAgentAdapter
from dev_autopilot.models import AgentCommand, ResultStatus
from dev_autopilot.security import redact


@pytest.mark.parametrize(
    ("payload", "secret"),
    [(value, "s3cr3t") for value in (
        "s3cr3t", "prefix s3cr3t suffix", ["s3cr3t"], {"x": "s3cr3t"},
        {"x": ["prefix s3cr3t"]}, ("s3cr3t",), {"nested": {"v": "s3cr3t"}},
        "s3cr3t,s3cr3t", "S3CR3T", "x-s3cr3t-y", {"a": 1, "b": None},
        [1, True, None], {"token": "s3cr3t"}, {"token": ["s3cr3t"]},
        {"a": ("s3cr3t",)}, "", {"secret": ""}, ["safe", {"v": "s3cr3t"}],
        {1: "s3cr3t"}, {"long": "xxs3cr3tyy"},
    )],
)
def test_redact_adversarial_shapes(payload, secret):
    result = redact(payload, (secret,))
    assert "s3cr3t" not in json.dumps(result, default=str)


def test_adapter_uses_minimal_environment_and_redacts_nested_result(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "top-secret-value")
    monkeypatch.setenv("UNLISTED_SECRET", "should-not-be-visible")
    code = (
        "import json,os; "
        "print(os.environ.get('SECRET_TOKEN','missing')); "
        "print(os.environ.get('UNLISTED_SECRET','missing')); "
        "open(os.environ['DEV_AUTOPILOT_OUTPUT_FILE'],'w').write("
        "json.dumps({'nested': {'token': os.environ.get('SECRET_TOKEN')}}))"
    )
    adapter = ExecutableAgentAdapter(
        "agent", AgentCommand(command=(sys.executable, "-c", code), timeout_seconds=5), ("SECRET_TOKEN",)
    )
    result = adapter.execute(
        task="work", repository=tmp_path, context={"credentials": "context-secret"}, output_contract="json"
    )
    assert result.status is ResultStatus.SUCCESS
    assert "top-secret-value" not in result.stdout
    assert "top-secret-value" not in json.dumps(result.output)
    assert "should-not-be-visible" not in result.stdout
    assert "context-secret" not in json.dumps(result.output)
