# Multi-entity positive-threshold count: source-first ablation v1

Date: 2026-08-30  
Status: clean local best-effort A/B; not an official leaderboard score

## Objective

Question 949 asks how many of four banks have positive net foreign-exchange
trading income greater than 1 thousand billion VND in 2025. The legacy planner
classified the question as `multi_entity_or_period_aggregation`, retained only
`ACB`, and had no operands. Its fallback therefore selected a semantic cell
from an unrelated table (`6.5e-11`).

The new lane is deliberately narrow. It recognizes this metric/predicate
family, recovers the complete issuer list only from the frozen public
`code_stock.csv` alias registry, and then performs an independent direct
source replay for every issuer. Because the question does not specify
consolidated versus separate statements, it replays both scopes and accepts
the count only when every issuer's pass/fail predicate is identical in both
scopes. The emitted evidence uses the consolidated replay as the canonical
scope and records the invariance check in the audit metadata.

Implementation:

- [builder lane and snapshot-safe alias lookup](../../scripts/e2e/build_competition_submission_v1.py#L1997)
- [snapshot-safe `code_stock.csv` path resolution](../../scripts/e2e/build_competition_submission_v1.py#L4100)
- [fixture for all aliases and scope-invariance rejection](../../tests/e2e/test_source_first_lookup.py#L2884)

## Frozen A/B snapshot

Both arms loaded the same copied implementation before execution:

```text
snapshot_dir: /tmp/vifinqa-multi-entity-threshold-ab-v3.l4v4yk
builder_sha256: d429ffae13eeb92f322b446a3acca374ccfe8ac47dc5bb0432ec78b5b53e3513
source_first_lookup_sha256: d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528
full_table_assets_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false (both arms)
```

Control passed `--disable-source-first-multi-entity-threshold`; variant used
the same command and inputs without that flag. Both also disabled only the
report-year-neighbor answer fallback so navigation-only research could not
confound the answer diff. The full corpus contained 146,246 tables and the
canonical source-line map contained 146,246 entries.

## Causal result

The serialized submission diff contains exactly one changed question:

| Question | Control answer / tier | Variant answer / tier | Interpretation |
|---:|---|---|---|
| Q949 | `6.5e-11` / `semantic_cell_heuristic` | `3.0` / `source_first_multi_entity_threshold_v1` | Count of four independently replayed bank values above 1,000 billion VND |

Lane telemetry in the variant is `questions_considered=1`,
`questions_resolved=1`, `ticker_count_4=1`, and `scope_invariant=1`. The
control and variant each emitted 1,012 records, replayed 1,012 queries, and
reported `errors=[]`.

The local integrity results are:

| Gate | Control | Variant |
|---|---:|---:|
| records | 1,012 | 1,012 |
| query replay | 1,012 | 1,012 |
| validation errors | 0 | 0 |
| source-line map | 146,246 / 146,246 | 146,246 / 146,246 |
| `changed_during_build` | false | false |
| ZIP integrity | `unzip -t` pass | `unzip -t` pass |

ZIP SHA-256:

```text
control: caf9536d12744ee85c6be4260c39150e0cf18a11203e63532c75690a7ed32057
variant: c67e1ac0352fa82df0c0025fa8b8848c670b7f4a452b81f38232c07c3dbef690
```

## Independent source replay

The variant evidence file is
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/variant/submission/data/q0949_evidence.csv`.
The four canonical consolidated cells are all row 7 of the 2025 income
statement and use `unit_hint=million_vnd`, source multiplier 1,000,000 and
question divisor 1,000,000,000:

| Issuer | Table UID | Column | Raw million VND | Replayed billion VND | Pass `>1000` and positive |
|---|---|---:|---:|---:|---|
| ACB | `b55df9d3…2fc858` | 3 | 1,731,886 | 1,731.886 | yes |
| MBB | `527183f8…1f2e88` | 2 | 1,756,922 | 1,756.922 | yes |
| EIB | `d4875ba8…76234d0` | 3 | 580,096 | 580.096 | no |
| BID | `9f972baa…310ee4` | 3 | 3,791,593 | 3,791.593 | yes |

An independent streaming check reread every submitted UID from the full
structured asset and verified document ID, ticker, report year, scope, row and
column bounds, raw cell, unit, and source-line-map membership. It also
reread the separate-statement counterparts:

```text
consolidated: ACB=1731.886, MBB=1756.922, EIB=580.096, BID=3791.593
separate:     ACB=1731.300, MBB=1758.592, EIB=580.096, BID=3718.008
predicates:   ACB=true, MBB=true, EIB=false, BID=true in both scopes
count:        3 in both scopes
INDEPENDENT_Q949_REPLAY: PASS
```

The separate replay includes the OCR row labels `ngoại hồi` for ACB and EIB;
the source-first resolver accepts these only through its bounded
`ordered_with_ocr_gap` row match, while the source coordinates and numeric
cell remain exact. No value was copied from retrieval, model output, or the
candidate score.

## Artifacts

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/variant/submission/build_report.json)
- [control submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/control/submission/submission.json)
- [variant submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/variant/submission/submission.json)
- [variant audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/variant/submission/prediction_audit_ledger_v1.jsonl)
- [variant Q949 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/variant/submission/data/q0949_evidence.csv)
- [control ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/control/submission.zip)
- [variant ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/variant/submission.zip)

## Verification boundary

This is a local prediction improvement with deterministic current-table replay,
not a strict `VERIFIED` answer and not an official score delta. The reports
show `machine_artifact_is_not_human_verified=true`, `promotion_allowed=false`,
and no strict certificate. The local workspace has no matching gold answer and
official scorer for this split, and no Kaggle submission was made. Therefore
the measured result is **one causal answer change in a best-effort candidate**;
it must not be reported as `Answer Accuracy +1` or `Execution Accuracy +1`
until the same candidate is evaluated by the authoritative scorer.

The route is now enabled by default in the current builder because this clean
A/B passed. It remains fail-closed for any other metric, missing/ambiguous
issuer alias, missing source pair, year mismatch, scope disagreement, or
predicate disagreement. The next high-leverage unresolved family remains
multi-entity selection questions such as Q536 and Q539; those require a
separate exact entity-alias and per-entity selection contract rather than a
generic `max` heuristic.

## Tests

```text
.venv/bin/pytest -q tests/e2e/test_source_first_lookup.py tests/e2e/test_submission_integration.py
82 passed

PYTHONPATH=.:src .venv/bin/pytest -q
458 passed, 1 skipped
```

