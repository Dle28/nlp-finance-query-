# Multi-entity interest-expense threshold: source-first ablation v1

Date: 2026-08-31  
Status: clean immutable local best-effort A/B; not an official leaderboard score

## Objective

Q1002 asks:

```text
Căn cứ số liệu năm 2016 của công ty mẹ AAA, công ty mẹ NKG, công ty mẹ DCM
và công ty mẹ DPM, tổng số công ty có phát sinh chi phí lãi vay nhiều hơn 100
tỷ là bao nhiêu?
```

The typed planner leaves this as a four-issuer `conditional_analytical`
`plan_required` question. The legacy multi-entity program selected an unrelated
negative-value predicate and emitted `0.0`. This ablation adds a bounded
source-first route for an explicit parent-company interest-expense threshold.

## Route contract

The route accepts only all of the following:

- one exact report year and at least two, at most eight planner tickers;
- a complete source-backed ticker list matching the visible issuer aliases;
- explicit `công ty mẹ`, mapped to `separate` reporting scope;
- the explicit `chi phí lãi vay` and `phát sinh` concept;
- a strict threshold phrase such as `nhiều hơn 100 tỷ`;
- a complete current-year source replay for every issuer;
- an explicit source unit proven from the current table/header or source
  document context;
- an accepted income-statement or finance-note-detail table with the required
  row context.

The route accepts only bounded OCR row variants and exact coordinate replay.
Missing issuer aliases, wrong scope, missing unit provenance, wrong year or
column, ambiguous rows, conflicting tables, and partial issuer coverage remain
unresolved on the existing candidate lane. The route is a proposal/candidate
lane only: it cannot create a strict certificate, human approval, release, or
official score.

## Frozen A/B snapshot

Both arms used the same persisted snapshot at
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v2/snapshot/`.
The implementation and input identities are:

```text
builder_sha256: 650044f7045b5a42f01b08680bf26dfe5233b625a01b063cde6302d18a18f869
source_first_lookup_sha256: 23805052c5893c6032eba8b8126b743cd92c7071fcbe16f8b3e6a5dbde66290b
questions_sha256: 64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0
code_stock_sha256: c2de8de56276f2bc4161fee3270b1cbbd8bec2428f79460a305a782e3d18b626
full_table_assets_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
question_count: 1012
structured_table_count: 146246
source_line_map_entries: 146246
changed_during_build: false (control and variant)
```

The authoritative run directory is
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/`.
The control passed
`--disable-source-first-multi-entity-interest-threshold`; the variant used the
same snapshot and all the same candidate inputs with the route enabled. The
earlier v2 control was interrupted at approximately `q0810/1012` and had no
report, so it is retained for audit but excluded from this result.

## Causal A/B result

An independent comparison keyed by Question ID found exactly one changed
record out of 1,012:

| Question | Control answer / tier | Variant answer / tier | Interpretation |
|---:|---|---|---|
| Q1002 | `0.0` / `program_multi_entity_plan` | `2.0` / `source_first_multi_entity_interest_threshold_v1` | DCM and NKG exceed 100 billion VND; AAA and DPM do not |

No other answer, question ID, or prediction-tier record changed. This is one
local source-replayed prediction change, not a claimed `+1` official accuracy
delta.

Variant route telemetry is:

```text
questions_skipped_not_eligible: 1011
questions_considered: 1
questions_resolved: 1
ticker_count_4: 1
threshold_pass_count_2: 1
explicit_scope: 1
protocol: source_first_multi_entity_interest_threshold_v1
promotion_allowed: false
```

## Independent source replay

The source tables explicitly declare VND. The selected cells were replayed from
the current 146,246-table structured asset; `raw_value` is VND and the output
column is converted to billion VND before applying the strict `> 100` test.

| Issuer | Source row | Raw source value (VND) | Output (billion VND) | `> 100` | Internal table UID |
|---|---|---:|---:|---|---|
| AAA | `- Trong đó: Chi phí lãi vay` | 23,874,478,344 | 23.874478344 | false | `a1d1b908c90710edac83ef203ffe634f5bc03cf5afde96cc724ee4d291dbe76e` |
| NKG | `- Trong đó: chi phí lãi vay` | 141,639,235,578 | 141.639235578 | true | `7feb7f8dc7926fcaa9a9bf1d37b43f319b6ecddec8d0ddfaab001e5ee8217b47` |
| DCM | `- Trong đó: Chỉ phí lãi vay` (OCR variant) | 203,937,110,047 | 203.937110047 | true | `185b71ff3e4ff8d3655a730491c4268766a886eeb09df9a93a15884557e4ce58` |
| DPM | `Chi phí lãi vay` in finance-note detail | 4,473,655,664 | 4.473655664 | false | `be0ce1f7c7ec3ac8a98ccbca8236fc574b0ddd491b9a94adfb48df264d2ad56d` |

The resulting count is `2`. The replay retained row/column coordinates
`AAA (8,3)`, `NKG (8,3)`, `DCM (8,3)`, and `DPM (2,1)` in the variant evidence
CSV. The DCM spelling is an OCR-distorted form of the required label and was
accepted only through the bounded route matcher. DPM comes from the contextual
`32. CHI PHÍ TÀI CHÍNH` finance-note-detail table, whose two-level header
declares `Năm nay · VND`.

## Integrity gates

Both arms passed the build and packaging gates:

```text
records: 1012
queries_replayed: 1012
validation.errors: []
source-line map: 146246 / 146246; missing 0; extra 0; status PASS
changed_during_build: false (control and variant)
control ZIP: unzip -t PASS; 1013 members; 541505 bytes
variant ZIP: unzip -t PASS; 1013 members; 541533 bytes
```

ZIP SHA-256:

```text
control: d26a09c9de7d45479d7a25898ff6652a9b369100c6a149ee39b6542145f86ba9
variant: e3adc661a41b86c7a08866a863ede18c95a6ad4e42e855ea544530efd74009b8
```

The variant report records `PARTIAL=961`, `UNRESOLVED=51`, `certificate=null`,
and `promotion_allowed=false`. The local replay and packaging gates do not
authorize semantic truth. No matching gold/scorer, strict certificate, human
semantic approval, official leaderboard submission, or official score exists
in this run.

## Artifacts

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/variant/submission/build_report.json)
- [control submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/control/submission/submission.json)
- [variant submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/variant/submission/submission.json)
- [variant diagnostics](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/variant/submission/diagnostics.jsonl)
- [variant prediction audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/variant/submission/prediction_audit_ledger_v1.jsonl)
- [Q1002 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/variant/submission/data/q1002_evidence.csv)
- [control ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/control/submission.zip)
- [variant ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/variant/submission.zip)
- [frozen snapshot directory](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v2/snapshot/)

## Tests and decision

The route-specific unit tests cover the positive four-issuer replay and
rejection of an unqualified scope. The focused and full source-first checks
were:

```text
focused interest-threshold test: 1 passed
PYTHONPATH=src .venv/bin/pytest -q tests/e2e/test_source_first_lookup.py
105 passed
```

Decision: keep `source_first_multi_entity_interest_threshold_v1` enabled in
the authorized best-effort candidate lane. It produces one deterministic local
candidate improvement (`Q1002: 0.0 -> 2.0`) with no observed regression in the
frozen A/B. This remains a prediction improvement, not an official score
delta. The route must remain fail-closed for incomplete issuer lists, wrong
scope, missing units, wrong year/column, ambiguous rows, and conflicting
source coordinates.
