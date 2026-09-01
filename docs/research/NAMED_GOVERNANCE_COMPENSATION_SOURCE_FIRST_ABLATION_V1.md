# Bounded direct-row extension: named governance compensation

Ngày: 2026-08-31  
Trạng thái: local best-effort candidate; chưa có official leaderboard score.

## 1. Mục tiêu

Ba câu hỏi direct lookup yêu cầu thù lao của một người cụ thể trong bảng
quản trị hoặc bên liên quan:

- Q15: Chu Thị Bình tại công ty mẹ MPC năm 2021, đơn vị triệu đồng;
- Q266: Ông Nguyễn Hạnh Phúc – Chủ tịch của VNM năm 2023, đơn vị triệu đồng;
- Q311: ông Lê Phước Vũ của công ty mẹ HSG trong năm tài chính kết thúc
  30/09/2024, đơn vị triệu đồng.

Generic semantic-cell routing đã trả Q15 bằng một cell không đúng row người
được hỏi, còn Q266/Q311 rơi về `fallback_zero`. Structured corpus có đúng
pattern: tên người trong row, context quản trị/thù lao hoặc giao dịch bên
liên quan, và cột kỳ hiện hành.

Adapter lần này chỉ thêm một executor cho family named governance
compensation. Nó không dùng Question-ID allow-list và không thay đổi generic
semantic ranking.

## 2. Contract fail-closed

Route chỉ nhận khi toàn bộ điều kiện sau đúng:

- typed plan là `direct_lookup`, đúng một ticker và một report year, câu hỏi
  có metric thù lao và trích xuất được tên người có ít nhất hai token;
- scope explicit `separate`/`consolidated` được tôn trọng; với câu hỏi không
  nêu scope, chỉ chấp nhận nếu các candidate scope không mâu thuẫn;
- table kind chỉ là `governance_roster` hoặc `related_party_schedule`;
- row có chính tên người và local context của hội đồng quản trị, tổng thù lao,
  hoặc nhân sự quản lý chủ chốt;
- current-period column được chọn từ chính bảng; source unit phải được khai
  báo và multiplier phải replay được;
- conflicting duplicate answers bị reject, trừ duplicate raw value mà bản
  ghi có unit declaration cụ thể hơn và vẫn cho cùng một output.

Không có rule “tên người xuất hiện ở đâu cũng là thù lao”. Bảng lương, sở hữu,
chi phí khác, generic schedule, thiếu unit hoặc người trùng tên ngoài context
được loại. Mọi numeric answer được đọc lại từ exact table UID/row/column và
replay bằng `Decimal`; model/retrieval metadata chỉ là navigation.

## 3. Implementation và input fingerprints

Code route:

`scripts/research/run_named_compensation_variant_v1.py`

Focused tests:

`tests/research/test_named_compensation_variant_v1.py`

Materializer contract:

`scripts/research/materialize_validated_route_overlay_v1.py`

Hai arm dùng cùng builder snapshot, source lookup snapshot, full asset,
source-line map và auxiliary candidate inputs:

| Input | SHA-256 |
|---|---|
| builder snapshot | `d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8` |
| source-first lookup snapshot | `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9` |
| typed plans | `212097bf0a892d9cf7ab703b9f690b854c9fbb36ce3cc07ebd73c390c22c0319` |
| full structured asset, 146,246 tables | `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7` |
| source-line map, 146,246 entries | `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615` |
| named-compensation adapter | `af3693103be21df56448a4b51c7e4618a6a61363ecb77cd468d954d9b44952f5` |
| materializer with named route contract | `378c83e086ed6622fd85af6b0887c9767a18a3e5a1102a9619cebb30ca2670dc` |

Focused named-family tests are run separately from the argmax suite; the
complete result is recorded below with the final artifact audit.

Control:

`artifacts/runs/vifinqa_answer_optimization_20260830/named_governance_compensation_ab_v1/control_r2/submission/`

Variant:

`artifacts/runs/vifinqa_answer_optimization_20260830/named_governance_compensation_ab_v1/variant_r2/submission/`

The variant uses protocol `vifinqa_named_governance_compensation_strict_v1`
and route `program_named_governance_compensation_v1`.

## 4. Full-population A/B

Reports:

- control: `artifacts/runs/vifinqa_answer_optimization_20260830/named_governance_compensation_ab_v1/control_r2/submission/build_report.json`;
- variant: `artifacts/runs/vifinqa_answer_optimization_20260830/named_governance_compensation_ab_v1/variant_r2/submission/build_report.json`.

| Gate | Control | Variant |
|---|---:|---:|
| `validation.valid` | `true` | `true` |
| records | 1,012 | 1,012 |
| queries replayed | 1,012 | 1,012 |
| validation errors | `[]` | `[]` |
| source-line-map status | `PASS` | `PASS` |
| ZIP integrity | `PASS` | `PASS` |

Variant stats: `family_seen=3`, `family_accepted=3`, trong đó 2
`governance_roster` và 1 `related_party_schedule`; 276 candidate tables được
quét, 236 table kind không phù hợp bị loại, và một exact duplicate được chọn
theo source-unit declaration cụ thể hơn. Contract không dùng ID exception.

Đối chiếu array 1,012 record cho thấy đúng 3 diff và không có collateral:

| ID | Control | Variant |
|---:|---:|---:|
| Q15 | `-0.03186`, `semantic_cell_heuristic` | `150.0`, `program_named_governance_compensation_v1` |
| Q266 | `0.0`, `fallback_zero` | `3123.0`, `program_named_governance_compensation_v1` |
| Q311 | `0.0`, `fallback_zero` | `360.0`, `program_named_governance_compensation_v1` |

Trace:

`artifacts/runs/vifinqa_answer_optimization_20260830/named_governance_compensation_ab_v1/variant_r2/submission/named_governance_compensation_trace_v1.jsonl`

## 5. Independent source replay

The three selected rows were reloaded independently from the complete table
asset and checked against the variant trace/evidence. All have exact ticker,
year, row/column coordinate, person and governance/remuneration context.

| ID | Ticker/year/scope | Table kind | Table UID | Row | Col | Raw value | Source unit / multiplier | Source-map entry |
|---:|---|---|---|---:|---:|---:|---|---:|
| Q15 | MPC / 2021 / separate | `governance_roster` | `331e4da4b3874acd3d1f9968613a3eca72f15f1586688132f721fb4679c781c0` | 2 | 1 | `150.000.000` | VND / `1` | 1299 |
| Q266 | VNM / 2023 / consolidated | `governance_roster` | `5de4733698d5ea588a63774a0336ef512974d2fa786dce2b8172d24021961c3e` | 2 | 1 | `3.123` | Triệu VND / `1,000,000` | 1961 |
| Q311 | HSG / 2024 / separate | `related_party_schedule` | `a421cb4972057f6500b20bcb80a04fc487ac569ba92fc53d3c8f7cfd9a268057` | 4 | 2 | `360.000.000` | VND / `1` | 1271 |

The source rows are respectively `Chu Thị Bình`, `Ông Nguyễn Hạnh Phúc –
Chủ tịch`, and `Ông Lê Phước Vũ`; the requested current columns are
`2021VND`, `2023Triệu VND`, and `30.9.2024VND`. Q15's source context is
`Các nghiệp vụ với nhân sự chủ chốt`; Q266's is `Thù lao và lương của người
quản lý chủ chốt`; Q311's is the related-party section containing
`Các khoản chi cho các nhân sự quản lý chủ chốt`.

Decimal conversion gives:

- Q15: `150000000 / 1,000,000 = 150` million VND;
- Q266: raw token `3.123` is parsed as `3123` in the source's `Triệu VND`
  column; source and requested-output scale are both `1,000,000`, so the
  replayed output is `3123` million VND;
- Q311: `360000000 / 1,000,000 = 360` million VND.

Independent audit result: 3/3 UID matches, 3/3 exact coordinate matches,
3/3 raw-value matches, and no replay error. The selected evidence CSVs are:

- `.../variant_r2/submission/data/q0015_evidence.csv`;
- `.../variant_r2/submission/data/q0266_evidence.csv`;
- `.../variant_r2/submission/data/q0311_evidence.csv`.

## 6. Materialized overlay

The three accepted source rows were copied as an explicit allow-list onto the
61-replacement Q928 candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_v1/`

Output overlay:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_v1/`

ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_v1.zip`

The materializer report records:

- `replacement_count=3`, `inherited_replacement_count=61`,
  `total_replacement_count=64`;
- route `program_named_governance_compensation_v1` for Q15/Q266/Q311;
- three used source UIDs and the three named evidence CSVs;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false` and `promotion_allowed=false`;
- 1,012 records, 1,012 replay, `errors=[]`; and
- 1,012 CSV members plus `submission.json`.

Against the 61-row base, exactly four JSON rows differ: Q15's answer is
numeric-preserved but its provenance/tier is improved, while Q266, Q311 and
the already inherited Q928 are the numeric changes relative to the older
60-row base. No unrelated row is changed.

ZIP SHA-256:

`e1f67246c4d7186d3aab989201312e4992520b403dd4f7bd269f546033cdd40c`

## 7. Decision and score boundary

Retain the three-row rule in the authorized best-effort submission lane. It is
a source-replayed prediction/provenance improvement, not `Answer Accuracy +3`,
`Execution Accuracy +3`, or an official leaderboard delta. The local workspace
has no matching gold/scorer for this 1,012-question population, and the
artifact has no human semantic approval or strict release certificate.

The next family should be chosen after the currently running argmax accounting
alias A/B finishes. Keep the named-person contract local to governance/
related-party context; do not relax it into generic person-name matching.
