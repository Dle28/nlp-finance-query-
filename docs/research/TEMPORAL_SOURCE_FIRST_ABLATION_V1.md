# Temporal source-first ablation V1

Ngày: 2026-08-30  
Phạm vi: 1,012 câu ViFinQA, cùng review bundle, replay artifact và full OCR
table corpus.

## Mục tiêu

Nhánh `temporal_change` trước đây thường lấy hai cell qua semantic heuristic
rồi mới trừ hoặc tính phần trăm. Khi metric trong compiled plan bị trộn với
operation shell, route này có thể chọn hai dòng không liên quan. Ablation này
thêm một proposal lane có điều kiện hẹp:

- đúng một ticker trong `question_plan`;
- đúng hai report year rõ ràng;
- tách metric từ câu hỏi, không dùng nguyên operation shell làm row label;
- lookup từng năm độc lập qua `source_first_exact_row_v1`;
- chỉ nhận `exact_contiguous` hoặc `ordered_with_ocr_gap`;
- không nhận comparative report year trong thí nghiệm này;
- mọi metric variant thành công của cùng một năm phải đồng nhất giá trị và
  row label;
- nếu câu không chỉ rõ scope thì hai operand phải cùng scope;
- phép tính cuối vẫn qua Decimal query replay và proposal verifier.

Đây là candidate/replay lane. `PARTIAL` không phải `VERIFIED`, và source
coordinates/model/replay không tự cấp semantic authorization.

## A/B result

Hai build dùng cùng full corpus:

`artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl`

Baseline tắt route temporal mới; variant bật route đó. Cả hai cùng giữ
strict metric-token patch của `source_first_lookup.py`, nên delta dưới đây cô
lập route temporal, không phải cô lập riêng patch metric-token.

| Local metric | Baseline | Variant | Delta |
|---|---:|---:|---:|
| Source-first temporal proposals resolved | 0 | 7 | +7 |
| Source-first temporal predictions selected | 0 | 7 | +7 |
| Program growth heuristic | 15 | 12 | -3 |
| Program subtract heuristic | 10 | 6 | -4 |
| Source-first direct predictions | 81 | 81 | 0 |
| Semantic-cell predictions | 686 | 686 | 0 |
| Non-zero predictions | 999 | 999 | 0 |
| Records | 1,012 | 1,012 | 0 |
| Query replay | 1,012 | 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| `VERIFIED` certificates | 0 | 0 | 0 |

Các câu được route mới chọn là:

`Q578, Q579, Q600, Q603, Q620, Q622, Q644`.

Q603 và Q644 giữ nguyên số answer so với heuristic nhưng có evidence row
và coordinate được materialize lại từ source-first. Q578, Q579, Q600, Q620
và Q622 đổi cả route lẫn số answer; đây là các ứng viên cần ưu tiên kiểm tra
leaderboard hoặc human semantic review.

Các source-first temporal proposal đều có class `PARTIAL`; toàn build có
`PARTIAL=1,010`, `UNRESOLVED=2`, không có certificate hoàn chỉnh.

## Artifact

- Baseline report: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/baseline/submission/build_report.json`
- Variant report: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/variant/submission/build_report.json`
- Baseline submission: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/baseline/submission/submission.json`
- Variant submission: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/variant/submission/submission.json`
- Variant diagnostics: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/variant/submission/diagnostics.jsonl`
- Variant proposal audit: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/variant/submission/prediction_audit_ledger_v1.jsonl`
- Baseline ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/baseline/submission.zip`
- Variant ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v1/variant/submission.zip`

Both ZIP files pass `unzip -t`. The reports record the full-corpus SHA-256,
all 1,012 records, all 1,012 replayed pandas queries and an empty validation
error list.

## Implementation and tests

The route is in:

- `scripts/e2e/build_competition_submission_v1.py`
- `src/finance_query/e2e/core/source_first_lookup.py`

The source-first core also now keeps a strict metric variant containing
`tài`/`tại` while retaining the historical OCR-relaxed variant, and no longer
creates a generic `khách hàng` qualifier from `cho vay khách hàng`. This was
covered by regression tests. Current verification:

```text
PYTHONPATH=src .venv/bin/pytest -q tests/e2e
163 passed in 2.27s
```

## Interpretation and next queue

Không có gold answer hoặc official local scorer trong workspace. Vì vậy `+7`
ở trên là route coverage/answer replacement trong best-effort artifact, không
phải tuyên bố `Answer Accuracy +7` hay `Execution Accuracy +7`.

The next high-leverage queue remains the 32 temporal questions rejected by the
strict gate and the larger `composed_execution_required`/`MISSING_OPERANDS`
family. The next experiment should first inspect exact source rows for missing
period columns, scope conflicts and staged selection; lowering global semantic
thresholds would not be a controlled improvement.
