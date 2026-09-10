"""Standard-library semantic checks for the offline code repair dataset."""

import json
import math
import statistics
import urllib.parse
from collections import Counter
from pathlib import Path

import pytest

DATASET = Path(__file__).parents[2] / "src/dev_autopilot/evals/data/tasks-code-v1.json"


def ref(task_id, x):
    if task_id == "N01":
        return sum(x) / len(x)
    if task_id == "N02":
        return statistics.median(x)
    if task_id == "N03":
        mean = sum(x) / len(x)
        return sum((v - mean) ** 2 for v in x) / len(x)
    if task_id == "N04":
        lo, hi = min(x), max(x)
        return [0.0 if lo == hi else (v - lo) / (hi - lo) for v in x]
    if task_id == "N05":
        return sum(a * b for a, b in zip(x["a"], x["b"], strict=True))
    if task_id == "N06":
        return (
            [sum(x["values"][i : i + x["k"]]) / x["k"] for i in range(len(x["values"]) - x["k"] + 1)]
            if x["k"] <= len(x["values"])
            else []
        )
    if task_id == "N07":
        return sum(c * x["x"] ** i for i, c in enumerate(x["coefficients"]))
    if task_id == "N08":
        g = math.gcd(x["a"], x["b"])
        return [g, 0 if not g else abs(x["a"] * x["b"]) // g]
    if task_id == "N09":
        return [max(x["lo"], min(x["hi"], v)) for v in x["values"]]
    if task_id == "N10":
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(x["a"], x["b"], strict=True)))
    if task_id == "D01":
        out = {}
        for row in x:
            out[row["group"]] = out.get(row["group"], 0) + row["value"]
        return out
    if task_id == "D02":
        out = {}
        for row in x:
            out[row["id"]] = row
        return list(out.values())
    if task_id == "D03":
        return sorted(x, key=lambda row: -row["score"])
    if task_id == "D04":
        right = {row["id"]: row for row in x["right"]}
        return [{**row, **right[row["id"]]} for row in x["left"] if row["id"] in right]
    if task_id == "D05":
        return dict(Counter(row["category"] for row in x))
    if task_id == "D06":
        out = []
        for start, end in sorted(x):
            if out and start <= out[-1][1] + 1:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end])
        return out
    if task_id == "D07":
        out = []

        def visit(v):
            for item in v:
                visit(item) if isinstance(item, list) else out.append(item)

        visit(x)
        return out
    if task_id == "D08":
        return dict(Counter(map(str, x)))
    if task_id == "D09":
        return [row for row in x["rows"] if all(k in row for k in x["required"])]
    if task_id == "D10":
        out, total = [], 0
        for v in x:
            total += v
            out.append(total)
        return out
    if task_id == "S01":
        return dict(urllib.parse.parse_qsl(x, keep_blank_values=True))
    if task_id == "S02":
        return [min(x["max_delay"], x["base"] * 2**i) for i in range(x["attempts"])]
    if task_id == "S03":
        incoming = {n: 0 for n in x["nodes"]}
        graph = {n: [] for n in x["nodes"]}
        for before, after in x["edges"]:
            graph[before].append(after)
            incoming[after] += 1
        ready, out = sorted(n for n in x["nodes"] if not incoming[n]), []
        while ready:
            n = ready.pop(0)
            out.append(n)
            for child in graph[n]:
                incoming[child] -= 1
                if not incoming[child]:
                    ready.append(child)
                    ready.sort()
        return out
    if task_id == "S04":
        pairs, stack = {")": "(", "]": "[", "}": "{"}, []
        for c in x:
            if c in "([{":
                stack.append(c)
            elif c in pairs and (not stack or stack.pop() != pairs[c]):
                return False
        return not stack
    if task_id == "S05":
        a, b = [int(v) for v in x["a"].split(".")], [int(v) for v in x["b"].split(".")]
        n = max(len(a), len(b))
        a += [0] * (n - len(a))
        b += [0] * (n - len(b))
        return (a > b) - (a < b)
    if task_id == "S06":
        return [x["items"][i : i + x["size"]] for i in range(0, len(x["items"]), x["size"])]
    if task_id == "S07":
        absolute, parts = x.startswith("/"), []
        for part in x.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        return ("/" if absolute else "") + "/".join(parts)
    if task_id == "S08":
        return {
            k: "[REDACTED]"
            if k.lower() in {"password", "token", "secret"}
            else (
                {kk: "[REDACTED]" if kk.lower() in {"password", "token", "secret"} else vv for kk, vv in v.items()}
                if isinstance(v, dict)
                else v
            )
            for k, v in x.items()
        }
    if task_id == "S09":
        out = []
        for c in x:
            if out and out[-1][0] == c:
                out[-1][1] += 1
            else:
                out.append([c, 1])
        return out
    if task_id == "S10":
        lo, hi = 0, len(x["items"])
        while lo < hi:
            mid = (lo + hi) // 2
            if x["items"][mid] < x["target"]:
                lo = mid + 1
            else:
                hi = mid
        return lo if lo < len(x["items"]) and x["items"][lo] == x["target"] else -1
    raise AssertionError(task_id)


DATA = json.loads(DATASET.read_text(encoding="utf-8"))


@pytest.mark.parametrize("task", DATA["tasks"], ids=lambda t: t["id"])
def test_reference_matches_oracle_cases(task):
    assert task["files"]["solution.py"].startswith("def solve(inputs):")
    assert task["oracle"]["kind"] == "python_function"
    assert task["oracle"]["module"] == "solution.py"
    assert task["oracle"]["function"] == "solve"
    for inputs, expected in task["oracle"]["cases"]:
        assert ref(task["id"], inputs) == expected


def test_dataset_identity_and_stratified_holdout():
    tasks = DATA["tasks"]
    assert len(tasks) == len({t["id"] for t in tasks}) == 30
    assert {c: sum(t["category"] == c for t in tasks) for c in ("numerical", "data", "software")} == {
        "numerical": 10,
        "data": 10,
        "software": 10,
    }
    assert {c: sum(t["split"] == "holdout" and t["category"] == c for t in tasks) for c in ("numerical", "data", "software")} == {
        "numerical": 3,
        "data": 3,
        "software": 4,
    }
