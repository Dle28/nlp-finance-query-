# Multi-entity conditional positive-count: source-first ablation v1

Date: 2026-08-30  
Status: accepted local best-effort A/B; not an official leaderboard score

## Objective

This batch targets a narrow conditional multi-entity failure family. The
question asks how many explicitly named parent-company issuers have a
positive operating cash flow in one reporting year. The legacy planner emits
`family=conditional_analytical`, `operation_ast.op=plan_required`, and the
generic program fallback uses a negative-value predicate. Q973 is the
representative case:

> Có bao nhiêu công ty trong số công ty mẹ CTCP Tập đoàn Hoa Sen, công ty mẹ
> CTCP Tập đoàn Hòa Phát và công ty mẹ CTCP Masan High-Tech Materials có lưu
> chuyển tiền thuần từ hoạt động kinh doanh dương trong năm 2022?

The source-first extension counts only values that are strictly greater than
zero after replaying each issuer's current structured-table cell. Its
fail-closed contract is:

- the plan must identify at least two unique tickers and exactly one year;
- the question must explicitly name the parent-company/separate-reporting
  scope, the operating-cash-flow metric, and the positive (`dương`) predicate;
- threshold, ranking, ratio, comparison, and percentage wording is rejected;
- every ticker must resolve independently from its own current table with the
  requested year, scope, statement kind, metric row, numeric cell, and unit;
- bounded OCR variants are allowed for this known metric, including
  `hoạt độngkinh doanh`, but unbounded fuzzy row substitution is not;
- any missing, ambiguous, duplicated, or scope-incompatible issuer rejects the
  complete count.

The accepted answer is therefore a source-replayed prediction candidate. It
is not human semantic approval, a strict verification certificate, or
leaderboard authority.

## Discarded v3 attempt

The first A/B attempt was not used as evidence. The implementation contained
the new route but its eligibility predicate accepted only
`multi_entity_or_period_aggregation`. Q973's actual review plan is
`conditional_analytical`, so the variant skipped all 1,012 questions and
produced no route delta. The v3 artifacts remain available for audit, but no
score or improvement is attributed to them.

The v4 snapshot below changes only this family eligibility check. The
existing `conditional_analytical` test is included in the focused test run.

## Frozen A/B snapshot

Both arms loaded the same persisted implementation and data inputs before
execution:

```text
builder_sha256: ed5329192b8f6b5d5c5dd54d205fdb6ba26c674c4232c6536c7edd8e0bd2fd1c
source_first_lookup_sha256: d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528
corpus_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false
```

The snapshot is preserved at
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/snapshot/`.
The implementation fingerprints are recorded in
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/snapshot_sha256.txt`.
The snapshot copy of `source_first_lookup.py` is byte-identical to the
workspace file and has the same SHA-256 fingerprint above.

The control disabled only
`--disable-source-first-multi-entity-conditional-count`. The variant enabled
the route. Both arms also disabled the report-year-neighbor and existing
multi-entity-threshold lanes and used the same questions, bundle, full table
corpus, replay ledger, source-line map, research candidates, route overlay,
model candidate ledger, direct evidence replay, and candidate-validity model.

## Result

An independent comparison of the two `submission.json` files, keyed by
question ID, found exactly one answer and prediction-tier change:

| Question | Control | Variant | Control query | Variant query |
|---|---:|---:|---|---|
| Q973 | `2.0`, `program_multi_entity_plan` | `1.0`, `source_first_multi_entity_conditional_count_v1` | `float((df1['operand_value'] < 0).sum())` | `float((df1['operand_value'] > 0).sum())` |

No other answer value changed across the remaining 1,011 question IDs. The
variant telemetry was:

```text
questions_considered: 1
questions_resolved: 1
questions_unresolved_or_ambiguous: 0
questions_skipped_not_eligible: 1011
ticker_count_3: 1
positive_count_1: 1
predicate: strictly_positive
```

The focused source-first test selection passed `9 passed, 77 deselected`.
It covers the three-issuer positive/negative replay, threshold rejection,
the compiled `conditional_analytical` family shape, and the existing direct
multi-entity aggregation guards.

## Q973 source replay evidence

The variant replayed one current structured-table cell for each requested
issuer. Values below are normalized to VND before applying the predicate:

```text
HSG  document HSG_financial_statements_2022_separate
     table 6880f137b74817db93e37f25906993743a85e574212d43a298f989d9866210fd
     row_index 18, column_index 3
     row "Lưu chuyển tiền thuần từ hoạt động kinh doanh"
     raw 1071767875098, source multiplier 1
     positive: true

HPG  document HPG_financial_statements_2022_separate
     table fca2d6a69c5141a83d6304c5337d4afab244fb111d379b49f00a1860e1a85f5c
     row_index 16, column_index 3
     row "Lưu chuyển tiền thuần từ hoạt động kinh doanh"
     raw -761380984482, source multiplier 1
     positive: false

MSR  document MSR_financial_statements_2022_separate
     table 20229dc22cafa011d41a30978767666143e766ef7275d282e8c449ca041e76ab
     row_index 11, column_index 3
     row "Lưu chuyển tiền thuần từ hoạt độngkinh doanh"
     raw -259020090, source multiplier 1000
     normalized -259020090000
     positive: false
```

The resulting predicate vector is `[true, false, false]`, so the replayed
count is `1`. The evidence CSV preserves the exact coordinates, raw cells,
unit multipliers, and route tier. The prediction audit ledger also records
the selected proposal, all three issuer claims, and the strict-positive
query.

## Integrity gates

Both v4 arms passed the local submission checks:

```text
records: 1012
queries_replayed: 1012
validation.errors: []
source-line map entries: 146246
map coverage: 146246 / 146246
source-line status: PASS
changed_during_build: false
control submission.zip: unzip -t PASS; 538605 bytes
variant submission.zip: unzip -t PASS; 538653 bytes
```

The independent A/B comparison used the two generated submissions rather
than trusting route counters. The variant Q973 CSV and audit record agree on
the answer, query, three unique table UIDs, and all source multipliers.

The local verification summary remains:

```text
PARTIAL: 1010
UNRESOLVED: 2
strict certificate: none
local verifier authority: none
promotion_allowed: false
```

The route's deterministic replay and predicate are evidence for a stronger
prediction candidate; they do not upgrade the record to `VERIFIED`.

## Artifacts

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/variant/submission/build_report.json)
- [control submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/control/submission.zip)
- [variant submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/variant/submission.zip)
- [variant Q973 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/variant/submission/data/q0973_evidence.csv)
- [variant prediction audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/variant/submission/prediction_audit_ledger_v1.jsonl)
- [variant diagnostics](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/variant/submission/diagnostics.jsonl)
- [persisted snapshot hashes](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/snapshot_sha256.txt)

## Score boundary and next research gate

This batch demonstrates one locally supported prediction correction in a
controlled same-snapshot A/B pair. It does not establish an official score
increase: the current workspace has no authoritative local scorer or strict
gold-answer certificate for the 1,012-question public prediction setup.
The candidate remains in the authorized best-effort lane and must be
evaluated against the official split before any leaderboard claim.

The next family should remain explicit-scope and source-replayable, but must
be measured as a separate A/B. Priority is another conditional/selection
pattern with a recoverable natural-language predicate and at least two
independent current-table cells. Any family with missing semantic operands,
ambiguous scope, duplicated sources, or unclear sign/unit semantics stays
unresolved rather than being folded into this count route.
