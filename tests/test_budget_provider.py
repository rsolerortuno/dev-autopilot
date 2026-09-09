from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dev_autopilot.budget import BudgetConfig, BudgetExceeded, BudgetStore


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
