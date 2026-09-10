from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from .harness import run_evaluation

parser = argparse.ArgumentParser(description="Run the versioned offline evaluation dataset")
parser.add_argument("--command", required=True, help="candidate command, executed in an isolated task repository")
parser.add_argument("--dataset", default="tasks-v1")
parser.add_argument("--label", default="default")
parser.add_argument("--split", choices=("train", "holdout", "all"), default="holdout")
parser.add_argument("--repetitions", type=int, default=1)
parser.add_argument("--timeout", type=float, default=10)
parser.add_argument("--output", type=Path, default=Path("eval-results"))
args = parser.parse_args()
results = run_evaluation(
    shlex.split(args.command),
    split=args.split,
    repetitions=args.repetitions,
    timeout=args.timeout,
    output=args.output,
    dataset=args.dataset,
    config_label=args.label,
)
print(f"{sum(r.success for r in results)}/{len(results)} successful; cost=unknown")
