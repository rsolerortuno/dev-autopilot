from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from dev_autopilot.budget import BudgetConfig, BudgetExceeded, BudgetStore, ReservationAlreadyClaimed
from dev_autopilot.providers import BudgetedAgentAdapter


def test_budget_idempotency_and_pending_survives_restart(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "budget.sqlite")
    config = BudgetConfig("p", "m", max_calls=2, max_micro_usd=10)
    assert store.reserve(config, call_id="same", estimated_micro_usd=4) == "same"
    assert store.reserve(config, call_id="same", estimated_micro_usd=4) == "same"
    assert store.reservations("p", "m")[0]["status"] == "pending"
    assert BudgetStore(tmp_path / "budget.sqlite").reserve(config, call_id="second", estimated_micro_usd=4) == "second"
    with pytest.raises(BudgetExceeded):
        store.reserve(config, call_id="third", estimated_micro_usd=1)


def test_concurrent_reservations_are_bounded(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "budget.sqlite")
    config = BudgetConfig("p", "m", max_calls=2)
    outcomes: list[bool] = []

    def reserve(i: int) -> None:
        try:
            store.reserve(config, call_id=str(i))
            outcomes.append(True)
        except BudgetExceeded:
            outcomes.append(False)

    threads = [threading.Thread(target=reserve, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(outcomes) == 2


def test_two_stores_atomic_max_one(tmp_path: Path) -> None:
    config = BudgetConfig("p", "m", max_calls=1)
    stores = [BudgetStore(tmp_path / "budget.sqlite") for _ in range(2)]
    outcomes: list[bool] = []

    def reserve(store: BudgetStore, call_id: str) -> None:
        try:
            store.reserve(config, call_id=call_id)
            outcomes.append(True)
        except BudgetExceeded:
            outcomes.append(False)

    threads = [threading.Thread(target=reserve, args=(store, str(i))) for i, store in enumerate(stores)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(outcomes) == 1


def test_project_cap_applies_across_milestones_and_is_immutable(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "budget.sqlite")
    first = BudgetConfig("p", "m1", max_calls=2, project_max_micro_usd=10)
    second = BudgetConfig("p", "m2", max_calls=2, project_max_micro_usd=10)
    store.reserve(first, call_id="one", estimated_micro_usd=6)
    store.reserve(second, call_id="two", estimated_micro_usd=4)
    with pytest.raises(BudgetExceeded):
        store.reserve(second, call_id="three", estimated_micro_usd=1)
    with pytest.raises(ValueError, match="immutable"):
        store.configure(BudgetConfig("p", "m3", max_calls=2, project_max_micro_usd=11))


def test_monetary_budget_requires_positive_estimate_and_claim_is_single_use(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "budget.sqlite")
    config = BudgetConfig("p", "m", max_calls=2, max_micro_usd=10)
    with pytest.raises(BudgetExceeded):
        store.reserve(config, call_id="zero", estimated_micro_usd=0)
    store.reserve(config, call_id="one", estimated_micro_usd=2)
    store.claim_execution("one")
    with pytest.raises(ReservationAlreadyClaimed):
        store.claim_execution("one")


def test_measured_overrun_is_persisted_and_blocks_next_reservation(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "budget.sqlite")
    config = BudgetConfig("p", "m", max_calls=3, max_micro_usd=5)
    store.reserve(config, call_id="one", estimated_micro_usd=2)
    with pytest.raises(BudgetExceeded, match="persisted"):
        store.settle("one", actual_micro_usd=6)
    assert store.reservations("p", "m")[0]["actual_micro_usd"] == 6
    with pytest.raises(BudgetExceeded):
        store.reserve(config, call_id="two", estimated_micro_usd=1)


def test_conflicting_concurrent_settlements_have_one_winner(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "budget.sqlite")
    config = BudgetConfig("p", "m", max_calls=2)
    store.reserve(config, call_id="one", estimated_micro_usd=1)
    store.claim_execution("one")
    outcomes: list[str] = []

    def settle(value: int) -> None:
        try:
            store.settle("one", value)
            outcomes.append("ok")
        except ValueError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=settle, args=(value,)) for value in (2, 3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict", "ok"]


def test_provider_duplicate_invocation_executes_adapter_once(tmp_path: Path) -> None:
    calls = 0

    class Adapter:
        def execute(self, *, task, repository, context, output_contract):
            nonlocal calls
            calls += 1
            return SimpleNamespace(status="SUCCESS", usage_micro_usd=None)

    wrapped = BudgetedAgentAdapter(
        Adapter(),
        provider="test",
        model=None,
        budget=BudgetStore(tmp_path / "budget.sqlite"),
        config=BudgetConfig("p", "m", max_calls=2),
        estimated_micro_usd=1,
    )
    context = {"invocation_id": "stable"}
    wrapped.execute(task="task", repository=tmp_path, context=context, output_contract="json")
    with pytest.raises(ReservationAlreadyClaimed):
        wrapped.execute(task="task", repository=tmp_path, context=context, output_contract="json")
    assert calls == 1


def test_provider_requires_invocation_id(tmp_path: Path) -> None:
    wrapped = BudgetedAgentAdapter(
        object(),
        provider="test",
        model=None,
        budget=BudgetStore(tmp_path / "budget.sqlite"),
        config=BudgetConfig("p", "m", max_calls=1),
    )
    with pytest.raises(ValueError, match="invocation_id"):
        wrapped.execute(task="task", repository=tmp_path, context={}, output_contract="json")


def test_provider_crash_leaves_claimed_reservation_ambiguous(tmp_path: Path) -> None:
    class Crashing:
        def execute(self, *, task, repository, context, output_contract):
            raise RuntimeError("crash")

    wrapped = BudgetedAgentAdapter(
        Crashing(),
        provider="test",
        model=None,
        budget=BudgetStore(tmp_path / "budget.sqlite"),
        config=BudgetConfig("p", "m", max_calls=2),
        estimated_micro_usd=1,
    )
    context = {"invocation_id": "crashed"}
    with pytest.raises(RuntimeError):
        wrapped.execute(task="task", repository=tmp_path, context=context, output_contract="json")
    with pytest.raises(ReservationAlreadyClaimed):
        wrapped.execute(task="task", repository=tmp_path, context=context, output_contract="json")
