# Multi-entity outstanding-share threshold: source-first ablation v1

Date: 2026-08-30  
Status: clean immutable local best-effort A/B; not an official leaderboard score

## Objective

This batch targets the conditional multi-issuer family where the question asks
how many named companies have more than a specified number of outstanding
shares at the end of one reporting year. The typed planner leaves both
questions in `conditional_analytical` with `operation_ast.op=plan_required`,
so the legacy program lane can apply the wrong generic predicate.

The source-first route was deliberately limited to two observable patterns in
the public question set:

- Q990: four explicitly named parent-company reports, more than 350 million
  outstanding shares at the end of 2021;
- Q1005: four explicitly named issuers, more than 400 million ordinary
  outstanding shares at the end of 2020.

The route counts only values obtained by replaying the current structured table
cell for every issuer. It is a prediction candidate, not a hidden-gold lookup,
human semantic approval, strict certificate, or leaderboard authority.

## Route contract

The adapter accepts only a question with all of the following properties:

- `conditional_analytical` or `multi_entity_or_period_aggregation` family and
  a count-like operation;
- exactly one report year;
- explicit `Có bao nhiêu`, outstanding-share wording, an end-of-year cue, and
  a threshold expressed as `vượt/lớn hơn/trên N triệu cổ phiếu`;
- at least two and at most eight issuer aliases recovered from the frozen
  `code_stock.csv` registry;
- a complete issuer list: the number of recovered legal-entity anchors must
  equal the number of recovered issuers, so a partial planner or alias list
  fails closed;
- exact current-year source replay, one current row per issuer, no report-year
  neighbor fallback, no mệnh giá/cổ phiếu quỹ row, and unit multiplier exactly
  one for share counts;
- explicit `công ty mẹ` maps to `separate` scope. An unqualified question is
  replayed in both `consolidated` and `separate` scopes and is accepted only if
  every issuer's pass/fail predicate is identical. The canonical emitted
  evidence is consolidated in that case.

Observed OCR row variants are bounded, not generic fuzzy synonyms:

```text
Cổ phiếu đang lưu hành
Cổ phiếu đang lưu hànhCổ phiếu phổ thông
Số lượng cổ phiếu đang lưu hành
Số lượng cổ phiếu phổ thông đang lưu hành
```

Any missing issuer, ambiguous row/column, scope conflict, source-year drift,
non-unit multiplier, or duplicate failure leaves the complete question on the
existing candidate/program lane.

## Frozen immutable A/B snapshot

Both arms loaded the same snapshot before execution. The snapshot is copied
under [the A/B snapshot](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/snapshot/).

```text
builder_sha256: e8f1729d04fc7d62a549064fa4d7743f6a29f11bc0e84ce8668c5b331f847d64
source_first_lookup_sha256: d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528
questions_sha256: 64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0
code_stock_sha256: c2de8de56276f2bc4161fee3270b1cbbd8bec2428f79460a305a782e3d18b626
full_table_assets_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false (both arms)
```

The control disabled only
`--disable-source-first-multi-entity-share-threshold`. The variant used the
same inputs without that flag. Both arms disabled report-year-neighbor
fallback and used the same review bundle, full 146,246-table corpus, source
line map, replay ledger, research candidates, route overlay, model candidate
ledger, and candidate-validity model.

The first non-immutable attempt is intentionally excluded: its build report
recorded `changed_during_build=true` because another worker edited the shared
builder while it was running. No result from that attempt is used below.

## Causal A/B result

An independent comparison of the two serialized `submission.json` files,
keyed by question ID, found exactly two changed records out of 1,012:

| Question | Control answer / tier | Variant answer / tier | Variant interpretation |
|---:|---|---|---|
| Q990 | `1.0` / `program_multi_entity_plan` | `3.0` / `source_first_multi_entity_share_threshold_v1` | NLG, DXG, and SNZ exceed 350 million; VPI does not |
| Q1005 | `2.0` / `program_multi_entity_plan` | `1.0` / `source_first_multi_entity_share_threshold_v1` | Only MWG exceeds 400 million |

No other answer or prediction-tier record changed. This is a two-question
local prediction delta, not a claimed `+2` official accuracy delta.

Variant route telemetry:

```text
questions_considered: 2
questions_resolved: 2
questions_unresolved_or_ambiguous: 0
replayed issuer count: 4 for each question
explicit_scope: 1 (Q990)
scope_invariant: 1 (Q1005)
threshold pass count: 3 (Q990), 1 (Q1005)
promotion_allowed: false
```

## Independent source replay

### Q990: explicit parent-company scope, 2021, threshold 350,000,000

The route used `separate` scope because the question explicitly says
`công ty mẹ`:

| Issuer | Replayed value | Predicate | Source row |
|---|---:|---|---|
| VPI | 219,999,780 | false | `Cổ phiếu đang lưu hành` |
| NLG | 382,940,013 | true | `Cổ phiếu đang lưu hànhCổ phiếu phổ thông` |
| DXG | 596,025,562 | true | `Cổ phiếu đang lưu hànhCổ phiếu phổ thông` |
| SNZ | 376,491,800 | true | `Số lượng cổ phiếu phổ thông đang lưu hành` |

The source UIDs and coordinates are retained in
[Q990 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/data/q0990_evidence.csv):

```text
VPI  7a0538e956ec74e29ab21f3e6b0ec6e0cbee50d4be906295dabc50f6df4d3956  row 3  col 1
NLG  e20bf52271ff93539d71185bd6cccecb9dce1c8963a444f0d819fb19d68f8e97  row 5  col 1
DXG  bc7e1759c8fbdd71d7241b93acc2752c4ed0bef7d344c778062e0aaa1eb32cbc  row 5  col 1
SNZ  8e1b9bfe8ca108c9ffa5d3e3b77dea273c3be2c25839ba7fc9ef2fc105f27b61  row 4  col 1
```

The resulting source-replayed count is `3`.

### Q1005: unqualified scope, 2020, threshold 400,000,000

The route independently replayed both reporting perimeters. The values and
predicate vectors were identical:

```text
consolidated: MWG=true, HHS=false, PNJ=false, HUT=false
separate:     MWG=true, HHS=false, PNJ=false, HUT=false
```

The canonical emitted evidence is consolidated:

| Issuer | Replayed value | Predicate | Source row |
|---|---:|---|---|
| MWG | 452,605,894 | true | `Cổ phiếu đang lưu hànhCổ phiếu phổ thông` |
| HHS | 274,744,063 | false | `Số lượng cổ phiếu đang lưu hành` |
| PNJ | 227,442,803 | false | `Số lượng cổ phiếu đang lưu hành` |
| HUT | 268,631,965 | false | `Số lượng cổ phiếu đang lưu hành` |

The source UIDs and canonical coordinates are retained in
[Q1005 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/data/q1005_evidence.csv).
The resulting source-replayed count is `1`.

## Integrity gates

Both immutable arms passed the local build gates:

```text
records: 1012
queries_replayed: 1012
validation.errors: []
source-line map: 146246 / 146246; missing 0; extra 0; status PASS
changed_during_build: false (control and variant)
control ZIP: unzip -t PASS; 541530 bytes
variant ZIP: unzip -t PASS; 541531 bytes
```

ZIP SHA-256:

```text
control: 32599f202cd30bd8381e7b9865a4e2b0f98ca37cc834b3202a8d177a2574053
variant: 31ba4437ef20b3a7bb6e837b4c2f7b58c2748f70c2e1b24beac21e5c90f900e2
```

The two build reports also agree on the full-corpus hash, map hash, question
count, map coverage, and implementation fingerprints. The variant has
`PARTIAL=961`, `UNRESOLVED=51`, no strict certificate, and no local verifier
authority. `promotion_allowed=false` remains explicit.

## Artifacts

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/build_report.json)
- [control submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/control/submission/submission.json)
- [variant submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/submission.json)
- [variant diagnostics](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/diagnostics.jsonl)
- [variant prediction audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/prediction_audit_ledger_v1.jsonl)
- [Q990 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/data/q0990_evidence.csv)
- [Q1005 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/data/q1005_evidence.csv)
- [control ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/control/submission.zip)
- [variant ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission.zip)
- [persisted snapshot builder](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/snapshot/scripts/e2e/build_competition_submission_v1.py)
- [persisted snapshot source resolver](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/snapshot/src/finance_query/e2e/core/source_first_lookup.py)

## Tests and verification boundary

The focused route tests and the full source-first lookup test file pass:

```text
.venv/bin/python -m pytest -q tests/e2e/test_source_first_lookup.py -k 'multi_entity_share_threshold'
2 passed, 90 deselected

.venv/bin/python -m pytest -q tests/e2e/test_source_first_lookup.py
92 passed
```

The current workspace regression after this A/B also passes:

```text
PYTHONPATH=.:src .venv/bin/python -m pytest -q
564 passed, 1 skipped
```

These tests prove the route's replay contract and scope fail-closed behavior;
they do not provide gold answers for the public 1,012-question prediction
set. The workspace has no authoritative local scorer or complete strict E2E
certificate for this split, so the A/B result must be described as a stronger
source-replayed prediction candidate until evaluated on an official split.

## Next research gate

The Q932 lease-threshold family is now covered by the separate clean A/B in
[the lease report](MULTI_ENTITY_LEASE_THRESHOLD_SOURCE_FIRST_ABLATION_V1.md).
The next gate is the existing metric-specific
`source_first_multi_entity_conditional_count_v1` family. It still requires a
bounded metric/context contract, exact source replay, and fail-closed handling
of missing or ambiguous issuer evidence before it can be enabled for any new
question.
