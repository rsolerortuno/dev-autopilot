# dev-autopilot-evals

Turn a scientific paper (plus optional supporting data) into a validated
Latch-style scientific-agent eval pack, using an **unmodified** Dev Autopilot as
the execution engine.

This is a separate distribution, not a feature of the core. `dev-autopilot`
stays a general orchestrator that knows nothing about papers, PDFs, Latch,
`latch-eval-tools` or evals. This package knows all of that and drives the core
through its public command line:

```text
paper.pdf + data
        │
        ▼
dev-autopilot-eval prepare / build
        │  stages source/, writes SCHEMA_CONTRACT.json,
        │  AUTHORING_GUIDE.md and project.yaml
        ▼
dev-autopilot --db … project start project.yaml --json
        │
   ┌────┼─────┐
   ▼    ▼     ▼
 Codex AGY  Claude          (unchanged core loop)
        │
        ▼
     evals/
        │
        ▼
dev-autopilot-eval gate
```

It is designed around the public conventions visible in LatchBio's `txbench-pp`
examples and the public `latch-eval-tools` package; it is not a claim about any
private or unreleased hiring rubric.

## Install

The core must be installed first, since this package depends on it:

```bash
python -m pip install -e .                                   # dev-autopilot
python -m pip install -e 'packages/dev-autopilot-evals[dev]' # dev-autopilot-eval
```

Optional extras:

| Extra   | Enables                                                                 |
| ------- | ----------------------------------------------------------------------- |
| `pdf`   | PDF ingestion via `pypdf` (otherwise only text/HTML/Markdown sources)    |
| `test`  | `pytest`, required by the final `gate` step that runs generated tests    |
| `latch` | grading calibration answers through the real public `latch-eval-tools`   |

Without `latch`, calibration is graded by a small built-in fail-closed fallback
that covers the common numeric/categorical grader types.

## Commands

```bash
# Stage evidence and write the charter, without running any agent.
dev-autopilot-eval prepare paper.pdf --data supplement.csv --out eval-workspace --count 3

# Prepare, run the multi-agent authoring loop, and validate the result.
dev-autopilot-eval build paper.pdf --data supplement.csv --out eval-workspace --count 3 --verbose

# Start a fresh run over an already-prepared workspace, keeping the previous
# attempt in the same recovery database. Use this after a terminal failure:
# a run that exhausted its retry budget cannot be resumed.
dev-autopilot-eval rerun eval-workspace --verbose

# Validate schema only, or validate and execute the generated pytest suite.
dev-autopilot-eval validate eval-workspace --expected-count 3
dev-autopilot-eval gate eval-workspace --expected-count 3
```

Remote sources work too: pass an `http(s)` URL as the source, and repeat
`--data-url` for supporting files. Remote downloads are capped at 64 MiB;
large biological datasets should be downloaded explicitly and passed with
`--data`.

`--fake` on `build`/`rerun` runs the core with its scripted offline adapters.
It authors nothing — it is a wiring smoke test that proves the charter, the
subprocess boundary and the validator are connected, without spending agent
calls.

### Locating the core CLI

`resolve_autopilot_command()` picks, in order: `$DEV_AUTOPILOT_BIN`, the
`dev-autopilot` console script on `PATH`, then `python -m dev_autopilot`. Set
`DEV_AUTOPILOT_BIN` to pin a specific checkout or virtualenv.

## Generated workspace

```text
eval-workspace/
├── AUTHORING_GUIDE.md      # the authoring standard handed to the agents
├── SCHEMA_CONTRACT.json    # machine-readable first-write shapes
├── project.yaml            # the charter consumed by `dev-autopilot project start`
├── source/
│   ├── paper.txt           # extracted text (author/reviewer-only when data are staged)
│   ├── source_manifest.json
│   ├── <original paper>    # preserved byte-for-byte
│   └── data/
├── evals/<eval-id>/{eval.json,ground_truth.json,oracle.py,calibration.json,README.md}
├── tests/
├── manifest.json
└── REPORT.md
```

The staged evidence is committed as the clean git baseline before the agents
run, so the orchestrator's scope validation can only see what the agents
actually produced. The agents may write only to `evals/`, `tests/`,
`analysis/`, `manifest.json` and `REPORT.md`.

## What the validator enforces

Each eval must have:

- a meaningful scientific decision rather than only a low-level calculation;
- a unique benchmark canary;
- an exact `<EVAL_ANSWER>` JSON contract in the task;
- a reproducible `ground_truth.json` plus `oracle.py`;
- a public `latch-eval-tools`-style grader, with ground truth consistent with
  the grader configuration for the common grader types;
- notes covering the tested capability, ground-truth derivation, scoring
  rationale, cold-test prediction, traps and K/Q/N/D prompt design;
- a `calibration.json` with an explicit analysis-choice/non-leakage record
  (including `withheld_from_prompt` and literal `forbidden_prompt_cues`), at
  least one defensible passing method and two plausible failing methods;
- a full `predicted_answer` for every calibration method, executed against the
  configured grader, so passing methods pass and failing methods fail — and
  every graded field must be changed by at least one plausible failing method;
- deterministic tests that pytest can actually collect;
- manifest/grader consistency across the whole pack;
- a complete `REPORT.md` review card per eval.

### Data-backed solver evidence boundary

When `source/source_manifest.json` records staged data, the full paper is
**author/reviewer-only**: `eval.json.task` may point the solver at
`source/data/`, but never at `source/paper.txt`, the staged original paper file,
or the paper origin URL. Otherwise a data-analysis eval collapses into paper
recall as soon as the results narrative states the graded conclusion. The
validator derives the blocked references from the manifest and rejects the task
deterministically. Literature-only packs with no staged data are exempt.

### REPORT.md review cards

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

`Source interpretation / tension` is not an agreement-with-the-paper check. It
records whether the data-derived oracle is aligned with, narrower than, or in
tension with the paper narrative, which helps reviewers spot paper-recall
contamination and legitimate reanalysis alike. Placeholders (TBD/None/N/A) are
rejected.

### Schema-first pre-return gate

The charter requires the implementer to read `SCHEMA_CONTRACT.json` before its
first write and to run the full gate itself before returning:

```bash
python -m dev_autopilot_evals.authoring gate . --expected-count N
```

This catches schema errors, pytest collection failures and failing generated
tests inside the implementation phase, instead of spending an outer correction
round on mechanical mistakes. The fast validator additionally does a static
discovery check, so a `test_*.py` with no pytest/unittest-style tests is
rejected immediately.

## Authoring strategy encoded in the charter

1. Understand the scientific decision the paper/data support.
2. Derive a deterministic oracle *before* writing the prompt.
3. Identify a plausible, load-bearing agent failure mode.
4. Write the task so the agent must discover load-bearing analysis choices
   rather than receive the filter / comparator / control structure /
   aggregation / normalization / ranking / statistic as a recipe. Declare
   literal `forbidden_prompt_cues` in calibration and keep them out of the task.
5. Use a structured answer and a deterministic grader.
6. Execute the grader on the canonical answer, defensible alternatives and
   plausible shortcuts. Correct answers must pass, declared failure modes must
   fail, and decorative grader fields are rejected.
7. Predict the cold-test failure.
8. Let AGY attack ambiguity and leakage, then require an independent Claude
   approval on the exact same final diff before final gates can succeed.

For Function-oriented work the guide emphasizes binding/affinity, stability,
titration/dose-response, DMS, potency, cell-based readouts, immune escape, viral
fitness, protein design, assay artifacts, controls, replicate structure,
comparator choice, sign/direction, and multi-axis go/no-go decisions when the
supplied evidence supports them.

## Inputs and limits

Supported primary sources: local PDF, HTML, text/Markdown/RST/CSV/TSV/JSON, or
an HTTP(S) URL returning PDF/HTML/text. PDFs are preserved byte-for-byte and
text is extracted with `pypdf`; a scanned PDF with no extractable text must be
OCRed first or accompanied by a text source.

`data_node` is optional in `latch-eval-tools`, so this package never invents
`latch://…` identifiers for local data. Provenance lives in
`source/source_manifest.json` until a real Latch data node exists.

## The decoupling contract

`tests/test_decoupling.py` asserts mechanically that no module here imports
`dev_autopilot` or any submodule. The single permitted reference is the string
`dev_autopilot.bridge` inside generated agent commands — that is the core's own
public bridge entry point, invoked as a subprocess like any other CLI.

That boundary is the point: the core can refactor its internals freely, this
package keeps working against any compatible release, and the same pattern can
host future companions (`dev-autopilot-bioinformatics`, …) without turning the
orchestrator into a monolith.
