# ViFinQA — autonomous execution to submission

This pipeline converts a full review bundle into an executable submission
without silently treating a machine recommendation as a human label.

```text
full V3 bundle
  -> repair-tables (immutable V2 raw-HTML grid)
  -> preprocess (numeric-safe V3 canonical header / period context)
  -> autonomous (independent source + semantic + critic views)
  -> machine execution ledger (exact-cell direct answers + tiny formula allow-list)
  -> submission compiler (all questions only)
  -> directory + ZIP pandas re-execution validation
```

## Required provenance gates

| State | Can train retrieval | Can enter execution ledger | Can enter final submission |
| --- | --- | --- | --- |
| `human_verified` | yes | yes, after exact V2 revalidation | yes |
| `machine_calibrated` | yes, as machine silver | yes, after critic + exact-cell validation | yes |
| `machine_provisional` | no | no | no |
| `needs_human` | no | no | no |

`machine_calibrated` never becomes `human_verified`.  Every final source cell
must match the V2 grid's row, column label, raw value, and parsed numeric value.

## Full-bundle commands

Assuming Kaggle output was extracted at `/path/to/run_full_001`:

```bash
.venv/bin/python local/run_local_review_stage.py repair-tables \
  --bundle-dir /path/to/run_full_001

.venv/bin/python local/run_local_review_stage.py preprocess \
  --bundle-dir /path/to/run_full_001

.venv/bin/python local/run_local_review_stage.py autonomous \
  --bundle-dir /path/to/run_full_001

.venv/bin/python local/run_local_review_stage.py autotrain \
  --bundle-dir /path/to/run_full_001

.venv/bin/python local/run_local_review_stage.py submission \
  --bundle-dir /path/to/run_full_001
```

The runner derives file names from the bundle's question count and context
contract. Therefore a 1,012-question V3 run writes
`machine_reviews_1012_autonomous_context_v3.jsonl` (or
`machine_reviews_1012_autonomous_replanned_context_v3.jsonl` with an override)
and does not overwrite V1/V2 or 60-question pilot artifacts.

`preprocess` writes `tables_evidence_context_v3.jsonl`; new autonomous and
ledger stages default to it and require its exactly-one-reliable-number binding
policy, conservative inline-header provenance and exact raw metric identity for
machine-silver promotion. `tables_evidence_context_v1.jsonl` and `v2` are
retained only to audit historic sidecars, never overwritten by this flow.

`submission` is deliberately fail-closed: it creates a ZIP only when the
execution ledger covers every public question with allowed provenance.  The
compiler re-runs every `pandas_query` against the packaged `data/*.csv` both
before and after creating the ZIP.

## Current executor coverage

`scripts/build_execution_ledger.py` produces direct lookup records when the
V4-selected exact cell passes all gates. It additionally accepts only a
complete, defined, confidence ≥0.95 `percentage_change` EvidenceSet whose
`x_old` and `x_new` cells pass a second exact V2 row/header/provenance/parse
check and have identical resolved units. The record carries its FormulaSet hash
and original review consensus; it does not modify a review label or create a
silver training example. Temporal formulas outside this one allow-list, ratio,
comparison, aggregation, filtering and multi-stage selection remain explicitly
`not_executable`; none is padded with a guessed answer.
