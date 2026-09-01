# Candidate-bound và conditional source-first ablation

Ngày: 2026-08-30

## Kết luận ngắn

Hai lane đều tạo ra thay đổi answer có thể replay từ structured corpus, nhưng
đây vẫn là lane `authorized_best_effort_submission_candidate`: không có gold
answer/official scorer cục bộ, không có human semantic certificate, và
`promotion_allowed=false`.

- `candidate-bound`: giữ được 3 thay đổi answer sau khi replay độc lập các
  source cell; lane này có leverage nhỏ nhưng rõ ràng trên nhóm direct lookup
  bị conflict hoặc OCR table boundary.
- `conditional-temporal`: sau khi thêm exact-year/match-mode/row-token và
  derived-row guard, còn 4 thay đổi answer. Bản trước guard nhận 6 proposal,
  trong đó đã loại được hai lớp false positive: `Trích trước ...` thay cho
  tổng metric và `TỔNG CỘNG` thay cho metric có tên.
- Cả hai lane không làm thay đổi các answer ngoài tập proposal của chúng trong
  A/B tương ứng.

## Candidate-bound A/B

Snapshot dùng cho control và candidate:

- builder SHA-256:
  `bae9fcd35008b3cb542fba58d0c12b15ee2bc71aab9db66f457b819987b0b33f`
- `source_first_lookup.py` SHA-256:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- `changed_during_build=false` ở cả hai report
- full table asset: 146,246 tables; SHA-256
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- source-line map: 146,246/146,246 entries; SHA-256
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`

Artifacts:

- control: `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/control/submission.zip`
- candidate: `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission.zip`

Cả hai có 1,012 record, 1,012 query replay, `errors=[]`, ZIP test pass và
map coverage pass. Control tắt cả candidate-bound và conditional-temporal;
candidate chỉ bật candidate-bound. Candidate-bound đã xem xét 339 direct
questions và resolve 6 proposal, nhưng chỉ 3 proposal trở thành answer/tier
thay đổi do các tier ưu tiên khác che phủ 3 proposal còn lại.

| ID | control | candidate-bound | source-bound replay |
|---:|---:|---:|---|
| 102 | `-232221` / semantic heuristic | `933246` / `source_first_candidate_bound_v1` | MBB 2018 consolidated, row `Tại ngày cuối năm`, column `Quyền sử dụng đất` |
| 141 | `124747.488` / semantic heuristic | `299716.827` / `source_first_candidate_bound_v1` | HAG 2021 separate, row `TỔNG CỘNG`, column `Số cuối năm Ngàn VND` |
| 306 | `2977510` / semantic heuristic | `156564919` / `source_first_candidate_bound_v1` | EIB 2020 separate, total financial-assets row/column in the transposed table |

Ba UID candidate được đọc lại trực tiếp từ
`full_table_assets_v1.jsonl`; mỗi UID khớp ticker, report year, scope, row,
header và cell được chọn. Đây là kiểm tra provenance/replay, không phải
chứng minh semantic gold correctness.

## Conditional-temporal A/B

Snapshot dùng cho control và candidate:

- builder SHA-256:
  `c72b4e6df515123fe88deec3e23172860a4448a413f10e87b1fa1e4c319f3887`
- `source_first_lookup.py` SHA-256:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- `changed_during_build=false` ở cả hai report

Artifacts:

- control: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v2/control/submission.zip`
- candidate: `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_temporal_ab_v2/conditional_temporal/submission.zip`

Cả hai có 1,012 record, 1,012 query replay, `errors=[]`, ZIP test pass và map
coverage 146,246/146,246. Control tắt conditional-temporal; candidate bật
duy nhất lane này. Candidate xem xét 26 câu có grammar chọn năm và resolve 4
proposal, với 3 `select_year_by_max` và 1 `select_year_by_min`.

| ID | control | conditional candidate | selected condition/source result |
|---:|---:|---:|---|
| 505 | `343.284396028` / semantic heuristic | `4.5` / conditional | IJC; max `Vay ngắn hạn ngân hàng` tại 2024; trả `Vay ngắn hạn phải trả các bên liên quan` |
| 514 | `126.676968019` / semantic heuristic | `62.125525058` / conditional | HDG; min `Lưu chuyển tiền thuần từ hoạt động tài chính` tại 2017; trả `Khách hàng mua căn hộ trả tiền trước` |
| 523 | `21.57966351` / semantic heuristic | `2.055255183` / conditional | DCM; max `Lãi dự thu tiền gửi có kỳ hạn` tại 2023; trả `Tiền mặt` |
| 526 | `-394.259405152` / semantic heuristic | `-83.790676915` / conditional | MWG; max EPS tại 2025; trả `13. Chi phí khác` |

Các condition values được so sánh ở source-normalized unit; chỉ answer metric
mới áp dụng requested output unit. Tất cả condition và answer cells đều có
exact requested report year, match mode được phép (`exact_contiguous` hoặc
`ordered_with_ocr_gap`) và row label vượt qua token/derived-row guard.

## Guard và kiểm thử

Đã thêm:

- `--disable-source-first-candidate-bound`
- `--disable-source-first-conditional-temporal`
- conditional source safety gate cho exact report year, allowed match mode,
  essential row tokens và derived/subtotal row rejection
- candidate-bound source index dựng một lần cho cả run thay vì dựng lại trong
  từng direct question

Focused regression:

```text
62 passed in 0.66s
```

`py_compile` và `git diff --check` đều pass trong lần kiểm tra snapshot/worktree
tương ứng. Runtime end-to-end sau index refactor chưa được dùng để tuyên bố
speedup chính thức; cần một benchmark control/candidate riêng nếu runtime là
subscore ưu tiên.

## Policy / score status

Các artifact trên là submission candidates đã validated về schema, replay và
coordinate provenance. Chúng không phải `VERIFIED` release: verifier local
ghi `authority=none`, proposal class là `PARTIAL`, và workspace không có
official answer scorer/gold split. Vì vậy các số 3 và 4 ở đây là answer/tier
coverage delta của A/B, không phải Answer Accuracy tăng 3 hoặc 4 điểm.

## Next research queue

1. Giữ candidate-bound ở dạng proposal lane; kiểm tra thêm semantic labels của
   3 ID bằng human review trước khi cho phép promotion.
2. Giữ conditional lane chỉ ở 4 proposal đã qua guard; không mở rộng bằng
   cách hạ row/match threshold toàn cục.
3. Ưu tiên phân tích nhóm `multi_entity_or_period_aggregation` còn lại:
   conditional grammar mới bao phủ 26/1,012 câu và còn nhiều câu
   `composed_execution_required`; cần source-first operand graph theo family,
   không sửa theo Question-ID.
