# Conditional-temporal source-first ablation V1

Ngày: 2026-08-30  
Protocol: `source_first_conditional_temporal_v1`

## Kết luận

Lane này đáng giữ ở authorized best-effort answer lane. Trên một snapshot mã
bất biến, nó mở được 4 câu có dạng:

> trong các năm A, B, C, ... hãy chọn năm có metric điều kiện lớn nhất/nhỏ
> nhất, rồi trả về metric đích của chính năm đó.

Mỗi năm của metric điều kiện và metric đích đều được replay từ structured table
hiện tại bằng `Decimal`; không lấy số trực tiếp từ retrieval text. A/B làm thay
đổi 4/1.012 prediction và 4 tier. Đây là candidate gain, không phải tuyên bố
`Answer Accuracy +4`: workspace không có gold answer key hoặc official scorer.

## Controlled A/B

Hai nhánh dùng cùng questions, review bundle, replay artifact, full-corpus
structured tables và source-line map. Cả hai cùng tắt các lane khác để delta
chỉ đo conditional-temporal:

- `--disable-source-first-report-year-neighbor`
- `--disable-source-first-temporal`
- `--disable-source-first-candidate-bound`
- `--disable-source-first-cross-entity`

Control thêm `--disable-source-first-conditional-temporal`; variant bỏ flag đó.
Snapshot được copy trước khi chạy, nên `changed_during_build=false` ở cả hai
nhánh.

| Metric local | Control | Variant | Delta |
|---|---:|---:|---:|
| Conditional questions considered | 0 | 26 | +26 |
| Conditional answers resolved | 0 | 4 | +4 |
| Conditional unresolved/ambiguous | 0 | 22 | +22 |
| Actual answer changes | 0 | 4 | +4 |
| Tier changes | 0 | 4 | +4 |
| Records / query replay | 1,012 / 1,012 | 1,012 / 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| ZIP integrity | PASS | PASS | 0 |

Implementation fingerprint của hai nhánh:

- Builder: `f1a8feffddef0f4ded53edc4bfdee00917320963e91948fbb8a977cef5f1e282`
- Source-first lookup: `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- Structured table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- Source-line map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`

## Answer diff

| Question | Control | Variant | Rule replay |
|---:|---:|---:|---|
| Q505 | `343.284396028` | `4.5` | IJC: max `Vay ngắn hạn ngân hàng` ở 2024, lấy `Vay ngắn hạn phải trả các bên liên quan` năm 2024, tỷ đồng |
| Q514 | `126.676968019` | `62.125525058` | HDG: min `Lưu chuyển tiền thuần từ hoạt động tài chính` ở 2017, lấy `Khách hàng mua căn hộ trả tiền trước` năm 2017, tỷ đồng |
| Q523 | `21.57966351` | `2.055255183` | DCM: max `Lãi dự thu tiền gửi có kỳ hạn` ở 2023, lấy `Tiền mặt` năm 2023, tỷ đồng |
| Q526 | `-394.259405152` | `-83.790676915` | MWG: max EPS ở 2025, lấy `Chi phí khác` năm 2025, tỷ đồng |

Independent evidence trong variant ghi đủ condition rows theo từng năm và
answer row ở selected year. Ví dụ Q514 có 5 condition cells (`2015, 2016,
2017, 2018, 2019`), trong đó giá trị nhỏ nhất là `-641234950272` ở 2017; cell
answer là `62125525058`, chuyển thành `62.125525058` tỷ đồng.

Tất cả proposal vẫn có `verification_class=PARTIAL`,
`confidence_class=BEST_EFFORT`, `promotion_allowed=false`; source replay không
tự biến thành semantic authorization hoặc `VERIFIED` certificate.

## Artifacts

- Control report: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v4/control/submission/build_report.json`
- Variant report: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v4/variant/submission/build_report.json`
- Control submission: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v4/control/submission/submission.json`
- Variant submission: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v4/variant/submission/submission.json`
- Variant audit ledger: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v4/variant/submission/prediction_audit_ledger_v1.jsonl`
- Control ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v4/control/submission.zip`
- Variant ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v4/variant/submission.zip`
- Immutable implementation snapshot: `/tmp/vifinqa-conditional-ab-v4.W6ST0E`

## Quyết định pipeline

Giữ lane bật mặc định trong candidate lane vì nó tạo 4 thay đổi source-replayed
và pass toàn bộ structural/replay gates. Không trộn 4 thay đổi này vào một
strict score. 22 câu còn lại tiếp tục `UNRESOLVED/ambiguous`; không nới parser
hoặc hạ threshold toàn cục chỉ để tăng coverage.

Ưu tiên tiếp theo là kiểm tra các conditional proposal trên leaderboard cùng
split, sau đó audit theo family các câu `multi_entity_or_period_aggregation`
còn lại. Nếu có gold/official scorer, dùng answer diff theo ID để đo precision
của lane; nếu chưa có, chỉ báo cáo source binding và deterministic replay như
trên.
