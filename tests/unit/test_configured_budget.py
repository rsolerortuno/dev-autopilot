import json

import pytest

from dev_autopilot.adapters.fake import ScriptedAgentAdapter
from dev_autopilot.budget import BudgetExceeded, ReservationAlreadyClaimed
from dev_autopilot.cli import _configured_agent
from dev_autopilot.db import SQLiteStore
from dev_autopilot.models import AgentCommand, BudgetPolicy, ExecutionResult, ResultStatus


def test_configured_roles_share_durable_budget_and_trace(tmp_path, job, monkeypatch):
    result = ExecutionResult(status=ResultStatus.SUCCESS, summary="done")
    monkeypatch.setattr(
        "dev_autopilot.cli.ExecutableAgentAdapter",
        lambda name, settings: ScriptedAgentAdapter(name, [result]),
    )
    job = job.model_copy(update={"budget": BudgetPolicy(max_calls=1, project_max_calls=1)})
    store = SQLiteStore(tmp_path / "runs.sqlite3")
    settings = AgentCommand(command=("unused",))
    first = _configured_agent("codex", settings, job, store)
    context = {"invocation_id": "logical-call", "run_id": "run-1", "state": "IMPLEMENTING"}
    assert first.execute(task="work", repository=tmp_path, context=context, output_contract="{}").status == ResultStatus.SUCCESS
    restarted = _configured_agent("codex", settings, job, store)
    with pytest.raises(ReservationAlreadyClaimed):
        restarted.execute(task="work", repository=tmp_path, context=context, output_contract="{}")
    second = _configured_agent("agy", settings, job, store)
    with pytest.raises(BudgetExceeded):
        second.execute(task="audit", repository=tmp_path, context=context, output_contract="{}")
    trace = json.loads((tmp_path / "provider-traces.jsonl").read_text())
    assert trace["run_id"] == "run-1" and trace["phase"] == "IMPLEMENTING"
    assert "work" not in trace.values()
