# T12 offline evaluation harness

`tasks-v1.json` is a versioned smoke dataset containing 30 small runnable tasks in three categories
(`scientific`, `software`, and `data`), with ten tasks per category and
five train/five holdout tasks per category. The dataset is versioned and each
task creates a fresh temporary repository. A candidate is an external command
run with that directory as its working directory; it must create the output
file named by the task prompt. The candidate receives only task files and the
task ID environment variable. Oracle data is retained by the evaluator and is
never copied into the temporary repository or candidate environment. It is an
installation and protocol smoke suite, not evidence of the M09 agent-quality
threshold.

`--dataset tasks-code-v1` selects 30 distinct code-repair exercises: numerical,
data and software transformations. Each starts with a broken `solve(inputs)` in
`solution.py`; `TASK.md` describes the contract. Expected answers remain in the
parent evaluator, while a separate Python subprocess receives only inputs and
returns JSON values. The evaluator bounds its readback to 1 MiB and terminates
the oracle after two seconds. This is process isolation, not an OS sandbox;
run untrusted candidates in a disposable Linux container. The published dataset
is not a confidential benchmark, and no model-quality result is yet claimed.

Reports include dataset SHA-256, configuration label and per-result repetition ID.
For comparison, freeze the dataset, model, allowed tools and budget; run the
single-agent, reviewer pipeline and no-audit variants with separate labels and
three repetitions each. Keep train results out of the holdout score. Report
failures and unknown costs as recorded, and retain provider billing separately.
The fixture correctness tests are not an agent benchmark.

Run a candidate and write both JSON and HTML reports with:

```console
python -m dev_autopilot.evals --command "python candidate.py" --split holdout --repetitions 3 --output eval-results
```

Reports record success, category, split, and measured wall-clock latency for
every repetition. Token or monetary cost is explicitly `unknown` because the
external command protocol has no billing telemetry. A deliberately unsolved
baseline may be used as a negative control, but its failures must remain in
the report and cannot be presented as successful evaluation evidence.
