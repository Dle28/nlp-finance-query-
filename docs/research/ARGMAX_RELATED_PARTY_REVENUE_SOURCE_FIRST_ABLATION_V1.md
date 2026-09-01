# Bounded argmax extension: related-party revenue

Ngày: 2026-08-31  
Trạng thái: local best-effort candidate; chưa có official leaderboard score.

## 1. Mục tiêu

Q1008 hỏi:

> Tổng doanh thu cung cấp dịch vụ cho các bên liên quan của công ty mẹ SSH đạt mức cao nhất vào năm nào trong các năm 2020, 2021, 2022, 2023?

Typed plan đã nhận diện đúng hình dạng `arg_extreme_period`, nhưng row tổng
trong source không lặp lại qualifier `bên liên quan`. Qualifier này nằm trong
source title của bảng giao dịch. Ngoài ra, bản ghi 2020 được extractor phân
loại là `financial_note`, còn 2021--2023 được phân loại là
`related_party_schedule`.

Contract mới chỉ nối đúng accounting family này. Nó không chọn số từ câu hỏi,
model, rank hay metadata; mọi số vẫn được đọc lại từ tọa độ của structured
table hiện hành và replay bằng Decimal.

## 2. Contract fail-closed

Route chỉ nhận khi toàn bộ điều kiện sau đúng:

- typed plan là `arg_extreme_period`, một ticker SSH, scope `separate`, bốn
  năm explicit `2020, 2021, 2022, 2023`, operator `max`;
- mỗi bảng có đúng row tổng đã normalize thành
  `Doanh thu cung cấp dịch vụ`;
- source title của bảng có đồng thời context giao dịch và `bên liên quan`;
- raw `financial_note`/`related_party_schedule` chỉ được collapse thành một
  family cho row/context chính xác này; bảng schedule generic không được
  hưởng alias;
- mỗi source record có đúng `report_year`, ticker, scope, source-line-map và
  multiplier khai báo; cả bốn multiplier giống nhau (`1`, VND); và
- giá trị max là duy nhất. Tie, thiếu unit, mixed scope, mixed family, row
  drift và unsupported composition vẫn bị reject.

Qualifier từ source title chỉ được dùng để bổ sung các token `bên`, `liên`,
`quan` còn thiếu trong row label của đúng contract trên. Không có rule tổng
quát kiểu “note nào gần nghĩa cũng là related-party schedule”.

## 3. Implementation và input fingerprints

Code route:

`scripts/research/run_arg_extreme_period_variant_v1.py`

Các test contract:

`tests/research/test_arg_extreme_period_variant_v1.py`

Focused result: `11 passed`.

Hai arm dùng cùng builder snapshot, source lookup snapshot, full asset, source
line map và runtime auxiliary inputs:

| Input | SHA-256 |
|---|---|
| builder snapshot `period_extreme_ab_v5/snapshot/scripts/e2e/build_competition_submission_v1.py` | `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e` |
| source lookup snapshot `period_extreme_ab_v5/snapshot/src/finance_query/e2e/core/source_first_lookup.py` | `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528` |
| typed plans | `212097bf0a892d9cf7ab703b9f690b854c9fbb36ce3cc07ebd73c390c22c0319` |
| full structured asset, 146,246 tables | `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7` |
| source-line map, 146,246 entries | `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615` |
| direct evidence replay | `d67c24a0e8d220187ce14db1c30907e8430b913d72f1c02e20b3d71ba3309a52` |
| candidate-validity model | `19fe579a16c480efa0097e39fa624b600a943bf72b48f164378a073de53a155a` |

The control is the completed pinned arm
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v2/control/`:
the canonical period route is disabled and all other experimental source-first
routes are disabled. The new variant is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v1/variant/`
and uses the strict adapter with the same flags and inputs.

Một control khác được chạy sau đó nhưng load worktree source lookup
`72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`; vì khác
implementation fingerprint, control đó bị loại khỏi attribution và không
được dùng trong kết luận dưới đây.

## 4. Full-population A/B

Variant terminal report:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v1/variant/submission/build_report.json`

| Gate | Control | Variant |
|---|---:|---:|
| validation.valid | `true` | `true` |
| records | 1,012 | 1,012 |
| queries replayed | 1,012 | 1,012 |
| validation errors | `[]` | `[]` |
| source-line-map coverage | 146,246/146,246 | 146,246/146,246 |
| ZIP integrity | PASS | PASS |

Pinned control ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v2/control/submission.zip`

SHA-256: `a627760d3574bc9b23289b92dac30969a686f09f922dfbeab367d313dfc94c17`.

Variant ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v1/variant/submission.zip`

SHA-256: `cf0dc1abfa04cea9bc3645c27530dab5273ff7d694721c5eefa914d695fae6fc`.

The variant saw 43 typed argmax plans and accepted 25. The accepted set is the
23 previously validated strict argmax IDs, Q879 from the tax-paid extension,
and the new Q1008. Compared with the pinned control, the exact 25 answer/tier
diff set is:

`Q813, Q832, Q841, Q850, Q860, Q874, Q876, Q879, Q890, Q897, Q904, Q906,
Q910, Q929, Q933, Q936, Q946, Q948, Q953, Q974, Q981, Q997, Q999, Q1000,
Q1008`.

The full 25-ID diff is an attribution check for the adapter arm. The
incremental change introduced by this report is only Q1008; the other 24
changes are inherited research families already materialized separately.

For Q1008 specifically:

| Arm | Answer | Tier |
|---|---:|---|
| pinned control | `11863882275.0` | `semantic_cell_heuristic` |
| strict variant | `2023.0` | `program_arg_extreme_period_v1` |

## 5. Independent source replay for Q1008

The audit reloaded the full asset independently of the selector trace and
checked the four UID/row/column coordinates against the evidence CSV. Every
record has ticker SSH, separate scope, VND unit, exact report year, source-map
entry and related-party transaction context.

| Year | Raw table kind | Table UID | Row | Col | Raw value | Source-map line |
|---:|---|---|---:|---:|---:|---:|
| 2020 | `financial_note` | `5946d7823d2a841c7ea144a0a6f28fe0a72d9be18ac174f7fca8c4d590a6c050` | 2 | 1 | `11.863.882.275` | 1053 |
| 2021 | `related_party_schedule` | `cc59ccd23828136af77bcadc9e9abd92efd55f9beae59b5c275e57503a0d30c7` | 2 | 2 | `61.614.783.673` | 1128 |
| 2022 | `related_party_schedule` | `a1f1008de04174c1d8c35d1e332322e6e3fdc9bcfb7d4acfa8de0c57a9187099` | 2 | 2 | `62.744.631.137` | 1059 |
| 2023 | `related_party_schedule` | `d370407de41c66bfe6d2165598b69f09fe98b863e40e12d4fac7b3b2297efc32` | 2 | 2 | `88.961.131.800` | 1118 |

The 2020 source title says the company had major transactions with related
parties; the 2021--2023 source titles say the same explicitly. The exact row
label remains `Doanh thu cung cấp dịch vụ` (with only note-number decoration
in later years). After Decimal replay with multiplier `1`, the vector is:

`[11863882275, 61614783673, 62744631137, 88961131800]`

There is one signed numeric maximum, `88961131800`, at 2023. The selector does
not use absolute values and does not invent a sum of the individual related
parties; this is the disclosed aggregate row itself.

Evidence CSV:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v1/variant/submission/data/q1008_evidence.csv`

Independent audit result: 4/4 UID matches, 4/4 Decimal evidence matches,
unique winner `2023`, `errors=[]`.

## 6. Materialized overlay

Q1008 was copied as exactly one explicit replacement on top of the existing
46-replacement signed lease/argmax/tax-paid overlay:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_v1/`

ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_v1.zip`

The materializer report records:

- `replacement_count=1` and `total_replacement_count=47`;
- route `program_arg_extreme_period_v1` for Q1008;
- four used source UIDs;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false` and `promotion_allowed=false`;
- 1,012 records, 1,012 replay, `errors=[]`; and
- 1,012 CSV members plus `submission.json`.

Overlay ZIP SHA-256:
`bc940f5615cc1cdd880a614e6c45e40180c0dfaee397a54971c36357152e5b27`.

## 7. Decision and remaining queue

Keep this accounting-family rule in the authorized best-effort candidate lane.
It is a source-replayed prediction change, not `Answer Accuracy +1`,
`Execution Accuracy +1`, or an official score delta. The workspace still has
no matching gold/scorer for this 1,012-question population and no human
semantic approval or strict release certificate.

The argmax queue after this extension is 18 rejected plans: 13
`REJECTED_NO_COHERENT_ROW_FAMILY`, four unsupported formula/composition cases,
and one extreme tie. The next experiment should choose one failure family,
audit it by source coordinates, and run another immutable same-snapshot A/B;
global unit/table-kind gates must remain fail-closed.

After the concurrent shared-worktree changes settled, the full pytest suite is
green at `607 passed, 1 skipped`. The route-specific suite is also green at
`11 passed`.
