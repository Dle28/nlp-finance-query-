# Temporal recovery for misclassified one-ticker questions: ablation V1

Ngày: 2026-08-30  
Protocol: `source_first_temporal_v1`

## Mục tiêu

Một phần câu hỏi có hình dạng temporal rõ ràng nhưng planner gán
`family=cross_entity_comparison` với chỉ một ticker và hai năm. Route này chỉ
nhận đúng structural shape đó; câu hỏi có hai issuer không được đi vào temporal
fallback. Hai năm được replay độc lập từ exact report-year table trước khi thực
hiện subtract hoặc percentage change.

## Controlled A/B

Snapshot control/variant dùng cùng full-corpus asset, review bundle, replay,
source-line map và source-first module. Cả hai cùng tắt candidate-bound,
conditional-temporal, cross-entity và report-year neighbor; variant chỉ bật
temporal source-first.

| Metric local | Control | Variant | Delta |
|---|---:|---:|---:|
| Temporal questions considered | 0 | 78 | +78 |
| Recovered from one-ticker cross-entity family | 0 | 39 | +39 |
| Temporal source-first answers resolved | 0 | 10 | +10 |
| Actual answer changes | 0 | 2 | +2 |
| Tier-only changes | 0 | 0 | 0 |
| Records / query replay | 1,012 / 1,012 | 1,012 / 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| ZIP integrity | PASS | PASS | 0 |

Implementation fingerprint của snapshot:

- Builder: `447e94ddfe28fa43cda28a2bd7c6a3c67523d173987e2ce70d48bf9c42573d60`
- Source-first lookup:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- Structured table asset:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`

## Hai answer changes

| Question | Control | Variant | Operation | Source rows |
|---:|---:|---:|---|---|
| Q578 | `-29.059234208` | `333.269120577` | subtract: BAF 2025 minus BAF 2022, tỷ đồng | `Trả trước cho người bán ngắn hạn`, exact cells in 2022 and 2025 |
| Q579 | `-81.57491945944844` | `167.23992696016566` | percentage change: IJC 2021 vs 2016 | `Lãi tiền gửi có kỳ hạn`, exact cells in 2016 and 2021 |

The other 8 resolved source-first proposals were covered by higher-priority
routes in the integrated answer output, so they did not alter a final answer.
This is why “10 resolved” and “2 answer changes” must not be conflated.

The source guard requires exact requested report year, an allowed match mode
(`exact_contiguous` or `ordered_with_ocr_gap`), essential metric tokens and a
consistent row signature across both years. Reclassification/transfer rows,
generic totals and qualified-vs-unqualified tax rows remain quarantined.

## Artifacts

- Control report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v1/control/submission/build_report.json`
- Variant report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v1/variant/submission/build_report.json`
- Variant submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v1/variant/submission/submission.json`
- Variant audit ledger:
  `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v1/variant/submission/prediction_audit_ledger_v1.jsonl`
- Control ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v1/control/submission.zip`
- Variant ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v1/variant/submission.zip`

## Quyết định

Giữ route ở best-effort candidate lane vì hai thay đổi answer có exact source
replay và pass structural gates. Không gọi đây là `Answer Accuracy +2`; không
để route temporal bind câu hỏi hai issuer, không dùng route metadata để cấp
authority, và giữ `PARTIAL/BEST_EFFORT` khi chưa có gold/official scorer.
