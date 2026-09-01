# Multi-entity operating-lease threshold: source-first ablation v1

Date: 2026-08-30  
Status: clean immutable local best-effort A/B; not an official leaderboard score

## Objective

Q932 asks:

```text
Trong năm 2020, có bao nhiêu công ty mẹ của MBB, HDB, KLB và NAB có cam kết
thuê hoạt động đến hạn trong 1 năm lớn hơn 40 tỷ đồng?
```

The typed planner leaves this as a four-issuer `conditional_analytical`
`plan_required` question. The legacy multi-entity program selected the wrong
generic predicate and emitted `0.0`. This ablation adds a bounded
source-first operating-lease maturity route for the explicit parent-company
scope.

## Route contract

The route accepts only all of the following:

- one exact report year and at least two, at most eight planner tickers;
- a complete source-backed ticker list matching the visible issuer aliases;
- explicit `công ty mẹ`, mapped to `separate` reporting scope;
- the operating-lease commitment context and a one-year maturity phrase;
- a threshold expressed in billion VND with a strict `greater_than` predicate;
- one current-year source row per issuer, with no report-year-neighbor fallback;
- exact row/column replay from a current structured table;
- a unit multiplier proven from the table/header or, when the table header is
  missing the unit, from a SHA-256-checked unit declaration in the same source
  document.

Accepted OCR row variants are bounded to `đến hạn trong 1 năm`, `trong vòng 1
năm`, `đến một năm`, and `đến 1 năm`. A generic maturity table without
operating-lease context, a partial issuer list, an unverified unit, an
ambiguous row, or a conflicting source remains unresolved on the existing
candidate lane.

## Frozen A/B snapshot

Both arms used the same persisted snapshot at
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/snapshot/`.
The implementation and input identities are:

```text
builder_sha256: 3fafd53281efd46e57d8e543890de9a81131a45de414b8784cc76fe52a42c490
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

The control passed `--disable-source-first-multi-entity-lease-threshold`; the
variant used the same inputs with the route enabled. All other candidate-lane
inputs and the full structured asset were held constant.

## Causal A/B result

An independent comparison keyed by Question ID found exactly one changed
record out of 1,012:

| Question | Control answer / tier | Variant answer / tier | Interpretation |
|---:|---|---|---|
| Q932 | `0.0` / `program_multi_entity_plan` | `2.0` / `source_first_multi_entity_lease_threshold_v1` | KLB and NAB exceed 40 billion VND; MBB and HDB do not |

No other answer or prediction-tier record changed. This is one local
source-replayed prediction change, not a claimed `+1` official accuracy delta.

Variant route telemetry is:

```text
questions_considered: 1
questions_resolved: 1
replayed_tickers: 4
threshold_pass_count_2: 1
explicit_scope: 1
unit_provenance_table_header_or_unit_label: 1
unit_provenance_hash_checked_source_document_unit: 1
promotion_allowed: false
```

## Independent source replay

Every selected coordinate was replayed against the current 146,246-table
asset. The table literals are in million VND; the output is converted to
billion VND by `raw * 1,000,000 / 1,000,000,000` before comparing with 40.

| Issuer | Source row | Raw source value | Output billion VND | `> 40` | Internal table UID |
|---|---|---:|---:|---|---|
| MBB | `- đến hạn trong 1 năm` | `31.007` million | `31.007` | false | `a578b75334c0e06a592ad2489fc642cb129703fd2457f11410423825951cc4e8` |
| HDB | `- Đến hạn trong 1 năm` | `17.186` million | `17.186` | false | `4b861137c452519bc9bb51d94020f649e31ab1c5083ea88d6e1f5c4c3cf363a5` |
| KLB | `Trong vòng 1 năm` | `49.649` million | `49.649` | true | `75c6e17e8550a5e67b7d59b862752603635ee79a5fb54bcbf25f1a121be268dd` |
| NAB | `Đến một năm` | `79.657` million | `79.657` | true | `14fbc3056ed85f8a7ee6dd309dc66398a79ef84e4af1aa7f86c8e1136818687d` |

The resulting count is `2`. The replay also verified the exact row and column
coordinates retained in
[Q932 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission/data/q0932_evidence.csv):

```text
MBB row 2, col 1; HDB row 3, col 1; KLB row 1, col 1; NAB row 1, col 1
```

MBB, HDB, and NAB expose the million-VND unit in the table/header/context
labels. KLB's selected table omits the unit from its local header, so the
route accepted it only after checking the table's recorded source SHA-256 and
finding the document-level declaration `Đơn vị tính: triệu VND`. A missing or
mismatched source hash would fail closed.

## Integrity gates

Both arms passed the build and packaging gates:

```text
records: 1012
queries_replayed: 1012
validation.errors: []
source-line map: 146246 / 146246; missing 0; extra 0; status PASS
changed_during_build: false (control and variant)
control ZIP: unzip -t PASS; 541531 bytes
variant ZIP: unzip -t PASS; 541505 bytes
```

ZIP SHA-256:

```text
control: d2d26c064a054066e2323fb72596adaf3e7330d4f624d7217e7c278242f55713
variant: cad1a6f8646f93ae27ff4679cb87cee70b90562d061638d7c95aa43b29e1bfbd
```

The variant report records `PARTIAL=961`, `UNRESOLVED=51`,
`certificate=null`, and `promotion_allowed=false`. The local validity and
source replay gates do not authorize semantic truth or release.

## Artifacts

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission/build_report.json)
- [control submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/control/submission/submission.json)
- [variant submission JSON](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission/submission.json)
- [variant diagnostics](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission/diagnostics.jsonl)
- [variant prediction audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission/prediction_audit_ledger_v1.jsonl)
- [Q932 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission/data/q0932_evidence.csv)
- [control ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/control/submission.zip)
- [variant ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission.zip)
- [snapshot manifest](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/snapshot/)

## Tests and decision

The route-specific source-first tests include the HDB maturity binding,
context rejection, and four-issuer lease-threshold replay. The current focused
and full checks are:

```text
PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/e2e/test_source_first_lookup.py -k 'lease'
2 passed, 93 deselected

PYTHONPATH=.:src .venv/bin/python -m pytest -q
564 passed, 1 skipped
```

Decision: keep `source_first_multi_entity_lease_threshold_v1` enabled in the
authorized best-effort candidate lane. This remains one local candidate
change, not an official score delta. No matching gold/scorer, complete strict
certificate, human semantic approval, or Kaggle submission exists. The route
must remain fail-closed for missing issuer aliases, wrong scope, missing
operating-lease context, unverified units, wrong year/column, or ambiguous
source coordinates.
