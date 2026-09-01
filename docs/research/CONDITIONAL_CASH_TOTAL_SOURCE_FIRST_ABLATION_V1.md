# Conditional cash-total source-first ablation V1

Ngày: 2026-08-30  
Protocol: `source_first_conditional_temporal_v1` với contextual match
`contextual_cash_and_equivalents_total`

## Kết luận

Một mở rộng nhỏ cho conditional-temporal đã mở thêm đúng 1 answer: các bảng
“Tiền và các khoản tương đương tiền” thường có dòng `TỔNG CỘNG`, vì vậy guard
chấp nhận dòng generic này chỉ khi resolver đã bind context metric
`tiền + các khoản tương đương tiền`; nó không cho `TỔNG CỘNG` làm bằng chứng cho
metric khác.

Kết quả A/B: `5` answer/tier thay đổi thay vì `4`. Đây là source-replayed
best-effort gain, chưa phải `Answer Accuracy +5` vì không có gold answer key hay
official scorer trong workspace.

## Controlled A/B

Hai nhánh dùng cùng snapshot code, questions, review bundle, full-corpus table
asset, replay, source-line map và các research inputs. Cả hai cùng tắt
report-year neighbor, temporal, candidate-bound và cross-entity; control tắt
thêm conditional-temporal, variant bật conditional-temporal.

| Metric local | Control | Variant | Delta |
|---|---:|---:|---:|
| Conditional questions considered | 0 | 26 | +26 |
| Conditional answers resolved | 0 | 5 | +5 |
| Conditional unresolved/ambiguous | 0 | 21 | +21 |
| Actual answer changes | 0 | 5 | +5 |
| Tier changes | 0 | 5 | +5 |
| Records / query replay | 1,012 / 1,012 | 1,012 / 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| ZIP integrity | PASS | PASS | 0 |

Fingerprint:

- Builder: `447e94ddfe28fa43cda28a2bd7c6a3c67523d173987e2ce70d48bf9c42573d60`
- Source-first lookup:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- Structured table asset:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- Source-line map:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`

## Answer diff

| Question | Control | Variant | Evidence path |
|---:|---:|---:|---|
| Q505 | `343.284396028` | `4.5` | IJC; max short-term bank borrowing at 2024; answer related-party short-term borrowing in 2024 |
| Q514 | `126.676968019` | `62.125525058` | HDG; min financing cash flow at 2017; answer apartment customer advance at 2017 |
| Q521 | `11.672448686` | `500.688616629` | HDG; max cash + cash-equivalent total at 2023; answer interest expense at 2023 |
| Q523 | `21.57966351` | `2.055255183` | DCM; max accrued term-deposit interest at 2023; answer cash at 2023 |
| Q526 | `-394.259405152` | `-83.790676915` | MWG; max basic/diluted EPS at 2025; answer other expense at 2025 |

Q521 là case mở rộng: condition cells được replay từ dòng `TỔNG CỘNG` của
structured tables HDG consolidated ở 2021, 2023 và 2025:

- 2021: `230395142669` VND
- 2023: `694458293386` VND — năm được chọn
- 2025: `265730670677` VND
- answer 2023: `500688616629` VND = `500.688616629` tỷ đồng

Generic-total guard chỉ cho phép match mode
`contextual_cash_and_equivalents_total` khi requested condition metric chứa đủ
context tokens. Các `TỔNG CỘNG` khác vẫn bị reject nếu không có contract này.

## Artifacts

- Control report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/control/submission/build_report.json`
- Variant report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/conditional_cash_total/submission/build_report.json`
- Variant submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/conditional_cash_total/submission/submission.json`
- Variant audit ledger:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/conditional_cash_total/submission/prediction_audit_ledger_v1.jsonl`
- Control ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/control/submission.zip`
- Variant ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/conditional_cash_total/submission.zip`

## Quyết định pipeline

Giữ contextual cash-total contract trong conditional proposal lane. Không mở
rộng generic subtotal cho metric khác và không nới fuzzy threshold toàn cục.
Proposal vẫn là `PARTIAL/BEST_EFFORT`, `promotion_allowed=false`; source
binding và Decimal replay không tự nâng thành semantic authorization hoặc
`VERIFIED`.
