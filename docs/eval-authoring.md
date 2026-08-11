# Scientific eval authoring profile

This experimental profile turns a scientific paper or article URL plus optional supporting data into a validated scientific-agent eval pack. It is designed around the public conventions visible in LatchBio's `txbench-pp` examples and the public `latch-eval-tools` package; it is not a claim about any private or unreleased hiring rubric.

## Install the branch

```bash
python -m pip install -U "dev-autopilot[eval] @ git+https://github.com/rsolerortuno/dev-autopilot.git@agent/latch-eval-authoring"
```

The normal Dev Autopilot commands remain available. The branch adds `dev-autopilot eval ...` and the equivalent `dev-autopilot-eval ...` entry point.

## Paper or article -> evals

Local PDF:

```bash
dev-autopilot eval build paper.pdf \
  --data supplementary_table.csv \
  --data assay_images/ \
  --out latch-eval-workspace \
  --count 3 \
  --verbose
```

Web article:

```bash
dev-autopilot eval build "https://example.org/article" \
  --data supplementary_table.csv \
  --out latch-eval-workspace \
  --count 3
```

Remote supporting files can be added with repeated `--data-url` arguments. Remote downloads are deliberately capped; large biological datasets should be downloaded explicitly and passed with `--data`.

To inspect the staged source and project charter before allowing agents to run:

```bash
dev-autopilot eval prepare paper.pdf --data supplement.csv --out latch-eval-workspace
```

Then run the generated charter through the ordinary project engine if desired:

```bash
dev-autopilot --db latch-eval-state.sqlite3 project start latch-eval-workspace/project.yaml --verbose
```

## What the profile generates

```text
latch-eval-workspace/
├── AUTHORING_GUIDE.md
├── project.yaml
├── source/
│   ├── paper.txt
│   ├── source_manifest.json
│   ├── <original paper>
│   └── data/
├── evals/
│   ├── <eval-id>/
│   │   ├── eval.json
│   │   ├── ground_truth.json
│   │   ├── oracle.py
│   │   ├── calibration.json
│   │   └── README.md
│   └── ...
├── tests/
├── manifest.json
└── REPORT.md
```

The source evidence is committed as the clean git baseline before the authoring agents run. The agents may only change eval outputs, analysis artifacts, tests, the eval manifest and the final report.

## v2.5 schema-first authoring, review cards, and solver evidence boundary

Each prepared workspace includes `SCHEMA_CONTRACT.json`, a machine-readable first-write template for `eval.json`, `calibration.json`, `manifest.json`, and the required `REPORT.md` review-card fields. The authoring objective requires Codex to read it before creating outputs and to run the full deterministic gate itself before returning the implementation report. The goal is to fix simple shape/test mistakes inside the first implementation phase instead of spending an outer correction round on known mechanical errors.

The report contract now requires one review card per eval with explicit `Capability`, `Scientific decision`, `Ground truth`, `Load-bearing failure mode`, `Grader`, `Expected naive failure`, `Confidence`, `Open risks`, and `Source interpretation / tension` fields. The final field must either state `No material tension identified` or describe a concrete tension between the data-derived oracle and the source paper's interpretation; placeholders such as TBD/None/N/A are rejected.

The contract also recommends diversity among wrong-answer patterns when multiple scientifically distinct failure modes exist, but this is intentionally a soft rule: binary scientific decisions are not forced to invent artificial answer diversity.

### Data-backed solver evidence boundary

When `source/source_manifest.json` contains one or more staged data records, the full paper is **author/reviewer-only**. `eval.json.task` may point the solver to files under `source/data/`, but must not point to `source/paper.txt`, the staged original paper file, or the paper source origin URL/path. This prevents a data-analysis eval from collapsing into paper recall when the results narrative states the graded conclusion directly.

The validator derives the blocked paper references from `source/source_manifest.json` and rejects the task deterministically. If a data-backed capability genuinely requires textual context, stage a narrowly curated excerpt as explicit evidence rather than exposing the complete results narrative. Literature-only evals with no staged data are not subject to this restriction.

## What is enforced

The deterministic profile rejects incomplete packs and asks the normal Autopilot correction loop to repair them. Among other checks, each eval must have:

- a meaningful scientific decision rather than only a low-level calculation;
- a unique benchmark canary;
- an exact `<EVAL_ANSWER>` JSON contract;
- a reproducible `ground_truth.json` plus `oracle.py`;
- a public `latch-eval-tools`-style grader;
- ground truth consistent with the grader configuration for common grader types;
- notes documenting the tested capability, ground-truth derivation, scoring rationale, cold-test prediction, traps and K/Q/N/D prompt design;
- a `calibration.json` with an explicit analysis-choice/non-leakage record (including `withheld_from_prompt` and literal `forbidden_prompt_cues`), at least one defensible passing method and two plausible failing methods;
- full `predicted_answer` objects for every calibration method, executed against the configured grader so passing methods pass and failing methods fail; every graded field must be changed by at least one plausible failing method;
- deterministic tests;
- manifest/grader consistency across the complete pack;
- a complete `REPORT.md` review card per eval, including confidence, open risks, and an explicit source-interpretation/tension statement.
- for data-backed packs, no solver task may expose the full staged paper, its extracted `paper.txt`, or the paper origin; the task must require work from staged data or a deliberately curated evidence excerpt.



### REPORT.md review cards

For every eval, `REPORT.md` must contain a human-review card with these labels:

```text
Capability:
Scientific decision:
Ground truth:
Load-bearing failure mode:
Grader:
Expected naive failure:
Confidence:
Open risks:
Source interpretation / tension:
```

`Source interpretation / tension` is deliberately not an agreement-with-the-paper check. It records whether the data-derived oracle is aligned with, narrower than, or in tension with the paper narrative, helping reviewers detect paper-recall contamination or legitimate reanalysis.

### Schema-first pre-return gate

The authoring agent must use `SCHEMA_CONTRACT.json` on the first write and, before returning its implementation report, run the **full** deterministic gate:

```bash
python -m dev_autopilot.eval_authoring gate . --expected-count N
```

This catches schema errors, pytest collection failures, and failing generated tests inside the implementation phase instead of spending an outer correction round on mechanical issues. The fast validator also performs a lightweight static discovery check so a `test_*.py` file with no pytest/unittest-style tests is rejected immediately; the full gate then executes pytest for authoritative collection and test results.

The final `gate` command also executes the generated tests:

```bash
dev-autopilot eval gate latch-eval-workspace --expected-count 3
```

## Authoring strategy encoded in the profile

The profile asks the implementer to work in this order:

1. Understand the scientific decision supported by the paper/data.
2. Derive a deterministic oracle before writing the prompt.
3. Identify a plausible, load-bearing agent failure mode.
4. Write the task so the agent must discover load-bearing analysis choices rather than receive or be strongly cued toward the filter/comparator/control-structure/aggregation/normalization/ranking/statistic as a recipe. Declare literal `forbidden_prompt_cues` in calibration and keep them out of task text.
5. Use a structured answer and deterministic grader.
6. Execute the configured grader on the canonical answer, defensible alternative methods and plausible shortcuts; correct answers must pass and every declared failure mode must actually fail. Reject decorative grader fields that no plausible failing method changes.
7. Predict the cold-test failure.
8. Let AGY attack ambiguity/leakage and verify corrections, then require an independent Claude approval on the exact same final diff before final gates can succeed.

For Function-oriented work the guide emphasizes binding/affinity, stability, titration/dose-response, DMS, potency, cell-based readouts, immune escape, viral fitness, protein design, assay artifacts, controls, replicate structure, comparator choice, sign/direction and multi-axis go/no-go decisions when the supplied evidence supports them.

## Inputs and limits

Supported primary sources: local PDF, HTML, text/Markdown/RST/CSV/TSV/JSON, or an HTTP(S) URL returning PDF/HTML/text. PDFs are preserved byte-for-byte and text is extracted with `pypdf`. A scanned PDF with no extractable text should be OCRed before use or accompanied by a text source.

`data_node` is optional in `latch-eval-tools`, so the profile does **not** invent `latch://...` identifiers for local take-home data. Provenance instead lives in `source/source_manifest.json` until a real Latch data node is available.

## Why this stays on a branch first

The profile changes the top-level console entry point but delegates every non-`eval` invocation to the existing v0.6 CLI. The core orchestration/state machine is untouched. This makes the branch suitable for take-home use while keeping the feature isolated for later review before a normal release.
