# Bounded direct-row extension: subsidiary investment

Ngày: 2026-08-31  
Trạng thái: local best-effort candidate; chưa có official leaderboard score.

## 1. Mục tiêu

Probe này nhắm vào nhóm câu hỏi direct lookup có dạng `đầu tư vào công ty
con` trong báo cáo riêng của công ty mẹ. Trước adapter, bốn câu có kết quả
`fallback_zero` hoặc sai scale dù structured table đã có dòng nguồn chính xác.

Adapter không lấy số từ Question-ID, model answer hay rank. Nó chỉ nhận một
family khi câu hỏi có đúng một ticker, một năm, scope `separate`, tín hiệu
`công ty mẹ`/báo cáo riêng, và source table có dòng chính xác cho đầu tư vào
công ty con.

## 2. Contract fail-closed

Route `program_subsidiary_investment_v1` yêu cầu:

- typed plan là `direct_lookup`, câu hỏi chứa cụm đầu tư vào công ty con và
  scope công ty mẹ/báo cáo riêng;
- đúng một ticker, đúng một report year và scope `separate`;
- row label được normalize thành `Đầu tư vào công ty con`; row của công ty
  liên doanh/liên kết không được coi là match;
- cột được chọn bởi semantic-period chooser và phải là cột current period;
- source unit phải được khai báo trong header, context nguồn hoặc một unit
  anchor độc lập cùng ticker/năm/scope. `unit_hint` suy luận không tự cấp
  quyền cho answer;
- `Nguyên giá`/`Giá gốc` phải còn được bind trong row, column context hoặc
  source context khi câu hỏi yêu cầu nguyên giá;
- duplicate table chỉ được giữ khi các Decimal answer đồng ý; conflicting
  duplicate bị reject; và
- answer được tính duy nhất theo công thức `raw_value * source_multiplier /
  requested_unit_multiplier`, sau đó canonical builder replay lại tọa độ.

Đây là route candidate-only. `PARTIAL`/`UNRESOLVED` vẫn khác
`VERIFIED`; không có human semantic approval hoặc strict E2E certificate.

## 3. Implementation và fingerprints

Code route:

`scripts/research/run_subsidiary_investment_variant_v1.py`

Materializer:

`scripts/research/materialize_validated_route_overlay_v1.py`

Hai arm dùng cùng builder snapshot, source lookup, full structured asset,
source-line map và auxiliary inputs. Fingerprints quan trọng:

| Input | SHA-256 |
|---|---|
| builder snapshot `semantic_contract_v7_final_snapshot_8Z6WNf/scripts/e2e/build_competition_submission_v1.py` | `d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8` |
| source lookup `src/finance_query/e2e/core/source_first_lookup.py` | `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9` |
| full structured asset, 146,246 tables | `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7` |
| source-line map, 146,246 entries | `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615` |
| candidate-validity model | `19fe579a16c480efa0097e39fa624b600a943bf72b48f164378a073de53a155a` |

The control and variant both report `changed_during_build=false` and the
builder/source lookup fingerprints are identical. Therefore the four-row
answer delta is attributable to the subsidiary-investment adapter, subject to
the usual absence of an accuracy scorer.

Control:

`artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/control/`

Variant:

`artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/variant/`

## 4. Full-population A/B

Both arms passed the completion gate:

| Gate | Control | Variant |
|---|---:|---:|
| validation.valid | `true` | `true` |
| records | 1,012 | 1,012 |
| queries replayed | 1,012 | 1,012 |
| validation errors | `[]` | `[]` |
| source-line-map coverage | 146,246/146,246 | 146,246/146,246 |
| ZIP integrity | PASS | PASS |

The variant adds four accepted rows with tier
`program_subsidiary_investment_v1`. The exact answer/tier diff against control
is `Q76, Q77, Q238, Q290`; no other question changed.

| Question | Control | Variant | Requested unit |
|---:|---:|---:|---|
| Q76 | `0` (`fallback_zero`) | `3126.89704` | tỷ đồng |
| Q77 | `0` (`fallback_zero`) | `13976356` | triệu đồng |
| Q238 | `0` (`fallback_zero`) | `1059688` | triệu đồng |
| Q290 | `28075666712.31` (`semantic_cell_heuristic`) | `28.07566671231` | trăm tỷ đồng |

Control ZIP SHA-256:
`b4a05bb9352ea36d3373911e5029a7847e5686ee1f85d2660af83ccd1b6561ca`.

Variant ZIP SHA-256:
`18e57850637e7efa1ae012eeaab9d6ef24e9cd0fac82984e9693f756f6db6c3a`.

## 5. Independent source replay

The audit independently reloaded the full table asset, then checked document,
ticker, year, separate scope, UID, row/column coordinate, raw cell, source
multiplier, requested unit and Decimal output. All four rows passed.

| Q | Source table / UID | Row, col | Raw source cell | Source unit | Decimal calculation |
|---:|---|---:|---:|---|---:|
| 76 | `HHV_financial_statements_2024_separate` / `6f04b9edc3cde4631a55fa038e3290988284f3d83d7ae02de7773b753c09b349` | 2, 1 | `3.126.897.040.000` | VND | `3126897040000 / 1000000000 = 3126.89704` |
| 77 | `VRE_financial_statements_2024_separate` / `8b26da70b29d327165bd95470eb0d024671b747257713172125c4344294ef55d` | 17, 4 | `13.976.356` | triệu VND | `13976356 * 1000000 / 1000000 = 13976356` |
| 238 | `HDB_financial_statements_2021_separate` / `da8dbf66bb38c4dc67138826b67c8afcd8b7558c7be9b9d2a2ddf0ebcd4d7e59` | 1, 1 | `1.059.688` | triệu đồng | `1059688 * 1000000 / 1000000 = 1059688` |
| 290 | `AAA_financial_statements_2023_separate` / `8fb0baee5346d8f58534e1d02cc2e88aef5da57488764a3202f588a3fce3fc66` | 2, 1 | `2.807.566.671.231` | VND | `2807566671231 / 100000000000 = 28.07566671231` |

Q290 also has an independent same-value unit anchor in
`AAA_financial_statements_2023_separate`, UID
`85e39923a3f02c9b84dc4daef12d5b0a0ca6968cc0162f3d2a34782cb435a68`, at row
15, column 3. The anchor is the current-period balance-sheet row
`1. Đầu tư vào công ty con` and confirms VND; its source-map entry is 254.
The four primary source-map entries are 1545 (Q76), 236 (Q77), 1455 (Q238)
and 1005 (Q290).

Audit result: `4/4 PASS`, `errors=[]`.

Evidence CSVs are in:

`artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/variant/submission/data/`

## 6. Materialized candidate overlay

The four rows were copied explicitly on top of the previous 47-replacement
candidate overlay:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_subsidiary_v1/`

ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_subsidiary_v1.zip`

Materializer result:

- `replacement_count=4`, `inherited_replacement_count=47`,
  `total_replacement_count=51`;
- route `program_subsidiary_investment_v1` for Q76, Q77, Q238 and Q290;
- `new_numeric_arithmetic_invented=false`;
- 1,012 records, 1,012 replay, `errors=[]`;
- ZIP has 1,012 CSV members plus `submission.json`; and
- ZIP SHA-256 is
  `8602ac4d4a013bd355f896e44cf2105dbddd2537b9546af87dfd3035c15a418b`.

The manifest retains `human_verified=false` and `promotion_allowed=false`.

## 7. Decision

Retain this family route in the authorized best-effort candidate lane. It is a
source-replayed candidate improvement, not a claim of `Answer Accuracy +4`,
`Execution Accuracy +4`, or official leaderboard gain. The workspace still
has no matching gold/scorer for this 1,012-question population; the recorded
official baseline remains Answer Accuracy `0.17` and Execution Accuracy `0.17`.

The next research step is to inspect the next high-yield residual family with
the same immutable A/B, exact-source audit and explicit overlay allow-list.
