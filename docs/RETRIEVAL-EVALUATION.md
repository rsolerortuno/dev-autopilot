# Retrieval development evaluation

Twenty annotated queries were evaluated against the same clean repository
commit `ae9e13bafc4dc062f2dd39b066dd5e9e04d9f522`. Both strategies excluded
the retrieval annotation script and its tests from their corpus. No expected
paths were changed to turn failures into successes.

| Strategy | Recall@5 | MRR |
|---|---:|---:|
| Original lexical term overlap | 0.40 | 0.2708 |
| BM25-style scoring, filename intent and file diversity | 0.90 | 0.50 |

These are development queries used to diagnose and improve retrieval, not an
unseen holdout and not evidence that model answers improve. The earlier synthetic
fixture reached 1.0, which did not predict real-repository performance. The first
unfiltered real run reached 0.30 and exposed annotation contamination; retain it
as diagnostic evidence rather than comparing it directly to the filtered runs.

With filename intent enabled, the synthetic fixture retains Recall@5=1.0 but
MRR falls to 0.975: Q12 ranks `bundle.py` before its annotated `evidence.py`.
The regression test records that exact rank change. No plan acceptance threshold
was changed; the tradeoff is retained alongside the real-repository improvement.

The final strategy still misses the exact annotated path for R17 (provider
adapter timeout/malformed output) and R20 (untrusted retrieved repository data).
Alternative relevant documents do not count as passing those annotations.

Index construction reads immutable Git objects in batches. Metadata sizes are
checked before reading content; the total budget counts every path even when
paths share a blob. The development run built the index in approximately 1.3 s
on Windows. That is an observation, not a cross-platform latency guarantee.

Reproduce on a clean Git checkout, writing reports outside it:

```sh
python scripts/evaluate_retrieval.py --repository . --ranking baseline --output ../retrieval-baseline
python scripts/evaluate_retrieval.py --repository . --ranking bm25 --output ../retrieval-bm25
```

Check that `index_commit`, `queries_sha256` and `excluded_paths` match before
comparing reports. Each report preserves failed queries and source provenance.
The remaining release gate is measured context utility on real agent tasks and
an independent evaluation set; these development results do not close that gate.
