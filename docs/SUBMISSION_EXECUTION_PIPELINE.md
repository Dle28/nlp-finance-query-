# ViFinQA — autonomous execution to submission

This pipeline converts a full review bundle into an executable submission
without silently treating a machine recommendation as a human label.

```text
full V3 bundle
  -> repair-tables (immutable V2 raw-HTML grid)
  -> preprocess (canonical header / period context)
  -> autonomous (independent source + semantic + critic views)
  -> machine execution ledger (exact-cell direct answers)
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

The runner derives file names from the bundle's question count.  Therefore a
1,012-question run writes `machine_reviews_1012_autonomous.jsonl` and does not
overwrite the existing 60-question pilot artifacts.

`submission` is deliberately fail-closed: it creates a ZIP only when the
execution ledger covers every public question with allowed provenance.  The
compiler re-runs every `pandas_query` against the packaged `data/*.csv` both
before and after creating the ZIP.

## Current executor coverage

`scripts/build_execution_ledger.py` currently produces execution records only
for direct lookup questions whose V4-selected exact cell passes all gates.
Temporal, ratio, comparison, aggregation, and multi-stage selection questions
remain explicitly `not_executable` until each operand/stage has a controlled
formula and exact evidence set.  They are never padded with a guessed answer.
