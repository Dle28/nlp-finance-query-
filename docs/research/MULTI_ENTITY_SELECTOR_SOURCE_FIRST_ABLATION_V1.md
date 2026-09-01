# Multi-entity selector: source-first ablation v1

Date: 2026-08-30  
Status: clean local best-effort A/B; not an official leaderboard score

## Objective

Question 536 asks:

> Trong năm 2023, tổng chi phí thuế thu nhập doanh nghiệp hiện hành của doanh
> nghiệp có tổng vốn chủ sở hữu cuối năm cao nhất trong số Tập đoàn Công
> nghiệp Cao su Việt Nam, Tổng công ty Phân bón và Hóa chất Dầu khí, CTCP Xi
> Măng Vicem Hà Tiên và CTCP Thép Nam Kim là bao nhiêu tỷ đồng?

The legacy planner retained only `NKG` and no selector operands. Its fallback
therefore emitted the NKG separate-statement tax cell
`-60.755885161`, without replaying the four-issuer `max` operation.

The new lane is intentionally narrow. It recognizes only this exact family of
question shape, recovers the issuer list from the immutable ticker-alias
registry when the planner is incomplete, and then performs source-first direct
replay for each issuer. It does not accept a result unless:

1. the report year is exact and every issuer has a source table;
2. the equity selector has one unique winner in both consolidated and separate
   scopes; and
3. the winner is the same in both scopes.

The final tax lookup is emitted from the selected issuer's consolidated table,
because the question does not state a reporting perimeter. That policy is
explicitly recorded as `unqualified_question_canonical_consolidated`; it is a
best-effort submission candidate, not semantic authorization or a strict
certificate.

Implementation and tests:

- [selector lane and proposal priority](../../scripts/e2e/build_competition_submission_v1.py#L2321)
- [trailing-digit ticker alias registry](../../src/finance_query/e2e/core/questions.py#L127)
- [selector replay tests](../../tests/e2e/test_source_first_lookup.py#L3934)

## A/B contract and immutable inputs

The clean A/B used snapshot
`/tmp/vifinqa-multi-entity-selector-ab-v3.XXXXXX`. It was copied from the
previous clean selector snapshot and received only the missing
`source_first_multi_entity_selector_v1` proposal priority (`91.3`). This
isolated the selector from unrelated period-extreme changes that appeared in
the shared working tree during an earlier attempted A/B.

Both arms loaded identical implementation fingerprints:

```text
builder_sha256: 3ed2f98a8618d252972fcaf31634c5884488a9d538a1a6b9f521761bb9da5433
source_first_lookup_sha256: d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528
questions_sha256: 6530b36bbe1ed4f8950b634e7bd56e217545aa2ef6076eee2d34448edd938554
code_stock_sha256: c2de8de56276f2bc4161fee3270b1cbbd8bec2428f79460a305a782e3d18b626
full_table_assets_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false (both arms)
```

The full structured corpus contained 146,246 tables and the source-line map
contained 146,246 entries. Control passed
`--disable-source-first-multi-entity-selector`; variant used the same inputs
without that flag. Both arms also disabled only report-year-neighbor answer
fallback, so a comparative column could not change the selector result.
The exact fingerprints are preserved in
[snapshot_sha256.txt](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/snapshot_sha256.txt).

## Causal result

The serialized submission diff contains exactly one changed question:

| Question | Control answer / tier | Variant answer / tier | Interpretation |
|---:|---|---|---|
| Q536 | `-60.755885161` / `semantic_cell_heuristic` | `688.075163368` / `source_first_multi_entity_selector_v1` | GVR is the source-replayed max-equity issuer; its consolidated current-tax expense is emitted |

The variant telemetry is:

```text
questions_considered: 1
questions_resolved: 1
ticker_count_4: 1
selector_winner_scope_invariant: 1
canonical_scope_consolidated: 1
promotion_allowed: false
```

Both arms emitted 1,012 records, replayed 1,012 queries, and reported
`errors=[]`. The source-line coordinate gate passed for all 146,246 table/map
entries. Both ZIPs passed `unzip -t`.

| Gate | Control | Variant |
|---|---:|---:|
| records | 1,012 | 1,012 |
| query replay | 1,012 | 1,012 |
| validation errors | 0 | 0 |
| source-line map coverage | 146,246 / 146,246 | 146,246 / 146,246 |
| `changed_during_build` | false | false |
| ZIP integrity | pass | pass |

ZIP SHA-256:

```text
control: 5686907b382358d23c8c571368522645afc3517d3db7a8ff442b250aa45bef8b
variant: 468a17fe0dda26068f30f8b86f43719789411feb9b406cd21c8c670aa19bd698
```

## Independent source replay

The variant evidence is
[q0536_evidence.csv](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/variant/submission/data/q0536_evidence.csv).
The selector replay used exact current-year table coordinates and converted
the raw VND cells to billion VND:

| Issuer | Consolidated equity (bn VND) | Separate equity (bn VND) | Selector outcome |
|---|---:|---:|---|
| GVR | 54,977.202916058 | 43,387.438797510 | unique max in both scopes |
| DPM | 11,545.199773844 | 11,374.466826463 | not max |
| HT1 | 4,832.911358204 | 4,823.626811287 | not max |
| NKG | 5,423.073956647 | 5,250.237400929 | not max |

The trailing-digit registry fix is material: without accepting `HT1` in the
alias registry, the planner/runtime path can silently reduce this four-issuer
question to an incomplete set. The variant evidence contains selector rows
for `GVR`, `DPM`, `HT1`, and `NKG`, followed by the selected GVR output row.

The selected output cell is:

```text
document_id: GVR_financial_statements_2023_consolidated
internal_table_uid: fe42645ee633a7d5fe7300553eb3a889e8db1c784ff99f017c05fdb69f9e9cfe
row_index: 3
column_index: 1
row_label: Tổng chi phí thuế thu nhập doanh nghiệp hiện hành
raw_value: 688075163368 VND
answer: 688.075163368 billion VND
```

The equity winner is invariant, but the tax output itself is not proven
perimeter-invariant: the separate-statement probe did not yield a comparable
stable GVR current-tax value. Therefore the answer deliberately records the
canonical consolidated policy and remains `PARTIAL_SCOPE_ASSUMPTION` in the
route diagnostics. This is a declared uncertainty boundary, not a hidden
scope conversion.

## Artifacts

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/variant/submission/build_report.json)
- [control submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/control/submission/submission.json)
- [variant submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/variant/submission/submission.json)
- [variant diagnostics](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/variant/submission/diagnostics.jsonl)
- [variant audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/variant/submission/prediction_audit_ledger_v1.jsonl)
- [variant Q536 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/variant/submission/data/q0536_evidence.csv)
- [control ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/control/submission.zip)
- [variant ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/variant/submission.zip)
- [snapshot directory](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/snapshot_dir.txt)
- [snapshot hashes](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/snapshot_sha256.txt)

## Verification boundary and next queue

This is one causal local answer change in an authorized best-effort candidate.
It is not `Answer Accuracy +1`, `Execution Accuracy +1`, or an official
leaderboard delta: the workspace has no matching gold/scorer for this split,
no complete canonical E2E certificate, and no Kaggle submission was made.
The report records `machine_artifact_is_not_human_verified=true` and
`promotion_allowed=false`; the generic verifier class remains `PARTIAL`.

The route is enabled by default in the current builder, but it remains
fail-closed for any other metric, missing/ambiguous alias, missing source
pair, non-exact year, duplicate selector maximum, or consolidated/separate
winner disagreement. Q465/Q539/Q553 remain the next high-leverage family, but
their debt/equity and interest-coverage output still changes with reporting
scope or lacks a safe row binding. They should not be opened by a generic
`max` heuristic until a separate scope/output contract is available.

## Tests

Focused regression after the route and priority changes:

```text
PYTHONPATH=.:src .venv/bin/pytest -q \
  tests/e2e/test_source_first_lookup.py \
  tests/e2e/test_submission_integration.py \
  tests/e2e/test_question_compiler.py
116 passed
```

The full repository suite was also run after the implementation and document
updates:

```text
PYTHONPATH=.:src .venv/bin/pytest -q
492 passed, 1 skipped
```
