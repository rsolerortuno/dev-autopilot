from __future__ import annotations

import threading

import pytest

from dev_autopilot.authorization import ApprovalError, ApprovalGrantStore

DIFF = "a" * 64


def test_grant_binds_action_diff_actor_and_is_one_use(tmp_path):
    store = ApprovalGrantStore(tmp_path / "auth.sqlite")
    grant = store.issue(action="publish", diff_sha256=DIFF, actor="human", ttl_seconds=60, now=100)
    store.consume(grant, action="publish", diff_sha256=DIFF, actor="human", now=101)
    with pytest.raises(ApprovalError):
        store.consume(grant, action="publish", diff_sha256="abc", actor="human", now=102)


@pytest.mark.parametrize("field", ["action", "diff_sha256", "actor"])
def test_mismatch_rejected(field, tmp_path):
    store = ApprovalGrantStore(tmp_path / "auth.sqlite")
    grant = store.issue(action="publish", diff_sha256=DIFF, actor="human", ttl_seconds=60, now=100)
    values = {"action": "publish", "diff_sha256": DIFF, "actor": "human"}
    values[field] = "wrong"
    with pytest.raises(ApprovalError):
        store.consume(grant, **values, now=101)


def test_expired_rejected(tmp_path):
    store = ApprovalGrantStore(tmp_path / "auth.sqlite")
    grant = store.issue(action="publish", diff_sha256=DIFF, actor="human", ttl_seconds=1, now=100)
    with pytest.raises(ApprovalError, match="expired"):
        store.consume(grant, action="publish", diff_sha256="abc", actor="human", now=101)


def test_concurrent_consumers_only_one_succeeds(tmp_path):
    store = ApprovalGrantStore(tmp_path / "auth.sqlite")
    grant = store.issue(action="publish", diff_sha256=DIFF, actor="human", ttl_seconds=60, now=100)
    outcomes = []

    def consume():
        try:
            store.consume(grant, action="publish", diff_sha256=DIFF, actor="human", now=101)
            outcomes.append(True)
        except ApprovalError:
            outcomes.append(False)

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count(True) == 1
