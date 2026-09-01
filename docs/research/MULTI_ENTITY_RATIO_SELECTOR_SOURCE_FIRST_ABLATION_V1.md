# Multi-entity debt/equity selector: source-first ablation v1

Date: 2026-08-30  
Status: clean local best-effort A/B; not an official leaderboard score

## Objective

Q465 and Q553 ask for the interest-coverage ratio of the issuer whose
debt-to-equity ratio is highest among BSR, PLX, and PVT in 2019. The legacy
planner classified them as a generic `max` plan with no operands, so the
fallback selected an unrelated semantic cell (`0.39`) rather than executing
the selector and the downstream ratio.

Q539 has the same accounting shape but names the companies instead of giving
all tickers. Its planner retained only `PLX`. The route is intentionally
fail-closed for that case unless the public alias registry resolves every
legal-entity anchor; a partial `PLX/PVT` recovery must not be treated as the
complete BSR/PLX/PVT set.

Implementation and tests:

- [ratio-selector implementation](../../scripts/e2e/build_competition_submission_v1.py#L4060)
- [source-backed ticker/list gate](../../scripts/e2e/build_competition_submission_v1.py#L3614)
- [ratio-selector tests](../../tests/e2e/test_source_first_lookup.py#L4057)

## Route contract

The lane recognizes only a multi-entity `max` plan with one exact report year,
the phrase `nợ phải trả trên vốn chủ sở hữu cao nhất`, and either the standard
interest-coverage wording or the explicit `(lợi nhuận trước thuế + chi phí lãi
vay) / chi phí lãi vay` wording.

For every issuer and for both `consolidated` and `separate` scopes it replays:

1. total liabilities from a coded primary balance-sheet statement;
2. total equity from the same class of primary statement;
3. the debt/equity ratio using `Decimal`.

It requires one unique winner and the same winner in both scopes. It then
replays profit before tax and interest expense for that winner from one coded
primary income statement and computes `(PBT + interest) / interest`. Because
these questions do not specify a reporting perimeter, the emitted answer uses
the consolidated output and records
`unqualified_question_canonical_consolidated` with
`promotion_allowed=false`. The output is therefore an authorized best-effort
candidate, not semantic authorization or a strict certificate.

The source/list gate also requires the number of recovered tickers to equal the
number of legal-entity anchors in an incomplete plan. This makes Q539 remain
unresolved rather than silently answering from only PLX and PVT.

## Frozen A/B snapshot

Both arms used the same copied implementation from
`/tmp/vifinqa-multi-entity-ratio-ab-v4.33b2j3`:

```text
builder_sha256: 4142a01b855abf783df81284f9caf9d7bd8f3c663aaca4946a53db4d331f127e
source_first_lookup_sha256: d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528
questions_sha256: f599ce7feb6a90bbb731cd318f83d73da0fcd01081800abba795346ec9f31f98
code_stock_sha256: c2de8de56276f2bc4161fee3270b1cbbd8bec2428f79460a305a782e3d18b626
full_table_assets_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false (both arms)
```

The control passed `--disable-source-first-multi-entity-ratio-selector`; the
variant used the same inputs without that flag. Both also disabled the
period-extreme and equity-tax selector lanes, and disabled report-year-neighbor
answer fallback, so the measured changes are attributable to this ratio lane.
The full corpus contained 146,246 tables and the source-line map contained
146,246 entries.

## Causal result

The serialized submission diff contains exactly two changed questions:

| Question | Control answer / tier | Variant answer / tier | Interpretation |
|---:|---|---|---|
| Q465 | `0.39` / `semantic_cell_heuristic` | `8.138020523057806` / `source_first_multi_entity_ratio_selector_v1` | PLX has the highest replayed 2019 debt/equity ratio; consolidated interest coverage is emitted |
| Q553 | `0.39` / `semantic_cell_heuristic` | `8.138020523057806` / `source_first_multi_entity_ratio_selector_v1` | Same selector/output family and same PLX winner |

Q539 did not change: it remains the control semantic-cell candidate because its
incomplete plan could recover only part of the legal-entity list. Variant
telemetry is:

```text
questions_considered: 3
questions_resolved: 2
questions_unresolved_or_ambiguous: 1
ticker_count_3: 2
selector_winner_scope_invariant: 2
canonical_scope_consolidated: 2
output_replayed_both_scopes: 2
promotion_allowed: false
```

Both arms emitted 1,012 records, replayed 1,012 queries, and reported
`errors=[]`. The source-line coordinate gate passed with 146,246/146,246 map
entries in each arm, and both ZIPs passed `unzip -t`.

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
control: 5385bc4a3c39ecdc7d769d6ba0019ab13a35b32d1931669a7bfde4025ef5a00f
variant: 920317d54ab376d60c2d39807e52f623957cab5e07131897ebcb80f718e886e3
```

## Independent source replay

The variant evidence files are
[q0465_evidence.csv](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/data/q0465_evidence.csv)
and
[q0553_evidence.csv](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/data/q0553_evidence.csv).
All numeric values below are in billion VND after replaying the raw VND cells.

| Issuer | Consolidated debt/equity | Separate debt/equity | Winner in both scopes? |
|---|---:|---:|---|
| BSR | 0.5735193257328931 | 0.5369303050532161 | no |
| PLX | 1.3825039219333840 | 0.9954036136328806 | yes |
| PVT | 0.9324134217923489 | 0.7891161096395925 | no |

PLX is therefore the unique winner in both scopes. The selected consolidated
output cells are:

```text
profit_before_tax: 5,647.771555645 billion VND
interest_expense:   791.223776592 billion VND
(profit_before_tax + interest_expense) / interest_expense:
  8.1380205230578053942981855067200030400
```

The separate PLX replay yields `17.97147607867491197178670153271080292941`
times. That difference is why the route uses an explicit canonical consolidated
policy and remains best effort; selector-winner invariance does not prove
output-metric perimeter invariance.

The selected PLX consolidated income table is
`2461d90c9024a31fec7f6de63c601a34c6f1c7bd9337335a3d75f1472f9d7786`. Its
replayed rows are `Lợi nhuận kế toán trước thuế(50 = 30 + 40)` at row 16,
column 3, and `Trong đó: Chi phí lãi vay` at row 8, column 3. The selector
rows and their source coordinates are retained in the evidence CSV and audit
ledger; no value is copied from retrieval rank, model output, or a hidden gold
answer.

## Artifacts

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/build_report.json)
- [control submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/control/submission/submission.json)
- [variant submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/submission.json)
- [variant diagnostics](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/diagnostics.jsonl)
- [variant audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/prediction_audit_ledger_v1.jsonl)
- [Q465 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/data/q0465_evidence.csv)
- [Q553 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission/data/q0553_evidence.csv)
- [control ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/control/submission.zip)
- [variant ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/variant/submission.zip)

## Verification boundary and next queue

This is two causal local answer changes in an authorized best-effort candidate.
It is not `Answer Accuracy +2`, `Execution Accuracy +2`, or an official
leaderboard delta. The workspace has no matching gold/scorer for this split,
no complete canonical E2E certificate, and no Kaggle submission was made. The
generic local verifier reports `PARTIAL`; the route has
`promotion_allowed=false` and is not strict `VERIFIED`.

The route is enabled by default in the current builder but remains fail-closed
for missing/ambiguous issuers, missing primary coded statements, missing rows,
non-exact years, duplicate selector maxima, winner disagreement across scopes,
non-positive interest expense, or conflicting duplicate sources. Q539 remains
unresolved until its legal-name aliases are complete; it must not be repaired
by a Question-ID-specific alias or by assuming that PLX is the winner.

## Tests

```text
PYTHONPATH=.:src .venv/bin/pytest -q \
  tests/e2e/test_source_first_lookup.py \
  tests/e2e/test_submission_integration.py \
  tests/e2e/test_question_compiler.py
124 passed

PYTHONPATH=.:src .venv/bin/pytest -q
492 passed, 1 skipped
```
