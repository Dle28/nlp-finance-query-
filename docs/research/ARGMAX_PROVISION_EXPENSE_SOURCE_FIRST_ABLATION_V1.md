# Bounded argmax extension: specific loan-loss provision expense

Ngày: 2026-08-31  
Trạng thái: local best-effort candidate; chưa có official leaderboard score.

## 1. Mục tiêu

Q928 hỏi:

> Ngân hàng TMCP Phương Đông (OCB) công ty mẹ có chi phí trích lập dự phòng cụ thể cho vay khách hàng lớn nhất vào năm nào trong các năm 2017, 2018, 2019 và 2025?

Typed plan đã nhận diện đúng hình dạng `arg_extreme_period`, nhưng source
được phân loại thành `debt_schedule` trong các năm 2017--2019 và
`financial_note_detail` trong năm 2025. Đây là một accounting family thống
nhất về mặt source context, nhưng không an toàn để collapse bằng một alias
toàn cục.

Thay đổi lần này chỉ nhận diện family khi đồng thời có row chính xác
`Trích lập dự phòng cụ thể cho vay khách hàng` và source title chứa
`Chi phí dự phòng rủi ro tín dụng`. Bảng detail/schedule khác không được
ảnh hưởng.

## 2. Contract fail-closed

Route chỉ nhận khi toàn bộ điều kiện sau đúng:

- typed plan có `operation_ast.op=arg_extreme_period`, một ticker OCB, scope
  `separate`, bốn năm explicit `2017, 2018, 2019, 2025` và operator `max`;
- mỗi source table bind đúng `report_year`, ticker, scope và current-period
  column;
- row label sau normalize giữ đúng cụm
  `trich lap du phong cu the cho vay khach hang`, chỉ bỏ phần chú thích
  thuyết minh ở cuối row;
- source context có title của nhóm
  `chi phi du phong rui ro tin dung`;
- raw `debt_schedule` và `financial_note_detail` chỉ được coi là cùng family
  trong contract row/context ở trên; generic financial-note detail hoặc debt
  schedule không được hưởng alias;
- cả bốn bảng khai báo cùng source unit VND/multiplier `1`, và giá trị max là
  duy nhất.

Route không lấy số từ câu hỏi, model answer, retrieval rank hoặc metadata.
Mọi số trong candidate được đọc lại từ exact table UID/row/column hiện hành
và replay bằng `Decimal`. Tie, thiếu source unit, mixed scope, mixed row
family, unsupported table kind hoặc coordinate không tồn tại vẫn bị reject.

## 3. Implementation và input fingerprints

Code route:

`scripts/research/run_arg_extreme_period_variant_v1.py`

Materializer contract:

`scripts/research/materialize_validated_route_overlay_v1.py`

Focused contract tests:

`tests/research/test_arg_extreme_period_variant_v1.py`

Kết quả focused test: `15 passed in 0.17s`. Cả hai script cũng qua
`py_compile` và `git diff --check`.

Hai arm dùng cùng builder snapshot, source lookup snapshot, full asset,
source-line map và runtime auxiliary inputs:

| Input | SHA-256 |
|---|---|
| builder snapshot | `d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8` |
| source-first lookup snapshot | `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9` |
| typed plans | `212097bf0a892d9cf7ab703b9f690b854c9fbb36ce3cc07ebd73c390c22c0319` |
| full structured asset, 146,246 tables | `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7` |
| source-line map, 146,246 entries | `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615` |
| argmax adapter after this change | `e8b8f5e18b034127c8c560773b4643423956257f3452c3388bb69d778960ab3b` |
| materializer after Q928 allow-list | `7f1fdee1776e8b45066c1fe3ef57c1f298056cbcf3437cebeaf90a3e20449485` |

Control:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_provision_expense_ab_v1/control/submission/`

Variant:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_provision_expense_ab_v1/variant/submission/`

The control calls the pinned canonical builder without the argmax adapter.
The variant enables only the typed argmax adapter with
`--strict-source-contract`; both use `--disable-source-first-period-extreme`
so the comparison does not mix this experiment with the canonical period
route.

## 4. Full-population A/B

Reports:

- control: `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_provision_expense_ab_v1/control/submission/build_report.json`;
- variant: `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_provision_expense_ab_v1/variant/submission/build_report.json`.

| Gate | Control | Variant |
|---|---:|---:|
| `validation.valid` | `true` | `true` |
| records | 1,012 | 1,012 |
| queries replayed | 1,012 | 1,012 |
| validation errors | `[]` | `[]` |
| source-line-map status | `PASS` | `PASS` |
| ZIP integrity | `PASS` | `PASS` |

Variant stats: 43 typed argmax plans seen, 26 accepted, 12 cohort-rejected,
1 unique-extreme tie rejected, 3 derived-ratio plans skipped, 1 multi-row
composition plan skipped. Strict checks also rejected 97 mixed-kind, 23
undeclared-unit, 79 unsafe-kind and 12 unsupported-kind candidates. These
counts are diagnostics for the adapter, not accuracy scores.

The variant changed 28 records against the raw control: 26 are the accepted
argmax cohort, while Q368 and Q369 are collateral changes between model and
semantic proposals outside this family. They are deliberately excluded from
the materialized overlay. This attribution boundary prevents the Q928 report
from claiming every wrapper-level output change as a provision-expense gain.

For Q928 specifically:

| Arm | Answer | Tier |
|---|---:|---|
| control | `112060694398.0` | `semantic_cell_heuristic` |
| strict variant | `2025.0` | `program_arg_extreme_period_v1` |

The strict variant trace is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_provision_expense_ab_v1/variant/submission/arg_extreme_period_trace_v1.jsonl`

## 5. Independent source replay for Q928

The four cells were reloaded independently from
`full_table_assets_v1.jsonl`, rather than trusting the selector trace. All
four records have OCB, `separate` scope, the requested report year, source
context unit `VND`, row index `2`, column index `1`, and an entry in the
146,246-entry source-line map.

| Year | Raw table kind | Table UID | Row | Col | Raw value | Source-map entry |
|---:|---|---|---:|---:|---:|---:|
| 2017 | `debt_schedule` | `e02f8563ca135a149595835a328c885290d3bdb90a952cdb0119eab632e4de28` | 2 | 1 | `112.060.694.398` | 1896 |
| 2018 | `debt_schedule` | `89caf3f6a36bed732ccd80340de30babbe0eb7a47c6d9713737f424e5ec8564c` | 2 | 1 | `633.084.224.721` | 1940 |
| 2019 | `debt_schedule` | `8ed5fddc9f54f54c6bdda27071c1fdc284b5f32aebf46e509b7df014d4e97ef7` | 2 | 1 | `822.479.834.736` | 2086 |
| 2025 | `financial_note_detail` | `c36e7878f2282cb25b819cee63c565faf2b16221fcd07f538df3fdb56aa80f62` | 2 | 1 | `2.163.777.088.772` | 2770 |

The source titles are `32. Chi phí dự phòng rủi ro tín dụng` for 2017,
`31. Chi phí dự phòng rủi ro tín dụng` for 2018, and the same `32.` title for
2019 and 2025. The row labels are the exact requested accounting row with
only note-number decorations differing by year. `unit_hint` is null in the
structured records, but `context_trace.unit_labels=["VND"]` is present for
all four source tables and the source headers explicitly carry `2017VND`,
`2018VND`, `2019VND` and `2025VND`.

After Decimal replay with multiplier `1`, the vector is:

`[112060694398, 633084224721, 822479834736, 2163777088772]`

The maximum is unique and occurs in `2025`. The evidence CSV is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_provision_expense_ab_v1/variant/submission/data/q0928_evidence.csv`

Independent audit result: 4/4 UID matches, 4/4 exact coordinate matches,
4/4 raw-value matches, unique winner `2025`, `errors=[]`.

## 6. Materialized overlay

Only Q928 was copied from the strict variant onto the latest existing
60-replacement candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_v1/`

Output overlay:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_v1/`

ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_v1.zip`

The materializer report records:

- `replacement_count=1`, `inherited_replacement_count=60` and
  `total_replacement_count=61`;
- route `program_arg_extreme_period_v1` for Q928;
- four used source UIDs and evidence `data/q0928_evidence.csv`;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false` and `promotion_allowed=false`;
- 1,012 records, 1,012 replay, `errors=[]`; and
- 1,012 CSV members plus `submission.json`.

An independent JSON comparison of base and output found exactly one changed
ID: Q928. Q368 and Q369 are byte/value-preserved from the 60-replacement base.
The overlay ZIP passed `unzip -t`.

## 7. Decision and score boundary

Retain this row/context bridge in the authorized best-effort submission lane.
It is a source-replayed prediction change, not `Answer Accuracy +1`,
`Execution Accuracy +1`, or an official leaderboard delta. The local build
has no matching gold/scorer for the 1,012-question population, and the
artifact has no human semantic approval or strict release certificate.

The next research target should remain one failure family at a time. The
collateral Q368/Q369 selection behavior should be measured separately before
any global wrapper change is promoted. Generic table-kind relaxation remains
disabled.
