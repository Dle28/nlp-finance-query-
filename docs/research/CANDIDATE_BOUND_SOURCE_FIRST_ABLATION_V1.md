# Candidate-bound source-first ablation V1

Ngày: 2026-08-30  
Protocol: `source_first_candidate_bound_v1`

## Kết luận

Candidate-bound là một lane best-effort có giá trị cho direct lookup: nó dùng
top navigation candidate chỉ để giới hạn bảng/tài liệu cần kiểm tra, sau đó
replay lại row, column, period và unit từ structured table hiện tại. Trên A/B
đã hoàn tất, lane thay đổi 3 answer và 3 tier trong 1.012 câu. Cả 3 thay đổi
đều được replay bằng source cell; tuy nhiên semantic scope của câu hỏi không
được ghi rõ (riêng/hợp nhất), nên chưa thể gọi đây là strict verified gain.

Numeric value không bao giờ được copy từ candidate metadata. Candidate-bound
chỉ trở thành proposal tier `source_first_candidate_bound_v1`, có
`promotion_allowed=false`, `verification_class=PARTIAL` và
`confidence_class=BEST_EFFORT`.

## Controlled A/B

Hai run tắt candidate-bound/conditional-temporal/temporal/cross-entity và dùng
cùng full-corpus asset, review bundle, replay, source-line map. Variant chỉ
bật candidate-bound. Snapshot mã không đổi trong cả hai build.

| Metric local | Control | Candidate-bound | Delta |
|---|---:|---:|---:|
| Direct questions considered | 0 | 339 | +339 |
| Candidate-bound answers resolved | 0 | 6 | +6 |
| Boundary: table | 0 | 4 | +4 |
| Boundary: document | 0 | 2 | +2 |
| Actual answer changes | 0 | 3 | +3 |
| Tier changes | 0 | 3 | +3 |
| Records / query replay | 1,012 / 1,012 | 1,012 / 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| ZIP integrity | PASS | PASS | 0 |

Implementation fingerprint của controlled snapshot:

- Builder: `bae9fcd35008b3cb542fba58d0c12b15ee2bc71aab9db66f457b819987b0b33f`
- Source-first lookup: `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- Structured table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`

## Answer diff đã quan sát

| Question | Control | Candidate-bound | Source replay |
|---:|---:|---:|---|
| Q102 | `-232221` | `933246` | MBB consolidated, `Giá trị còn lại` → `Tại ngày cuối năm`, cột `Quyền sử dụng đất`, triệu VND |
| Q141 | `124747.488` | `299716.827` | HAG separate, bảng `Trái phiếu thường dài hạn`, dòng `TỔNG CỘNG`, nghìn VND |
| Q306 | `2977510` | `156564919` | EIB separate, bảng tài sản tài chính, cột tổng cộng, triệu VND |

Ba câu đều có top candidate và source table khác nhau giữa separate và
consolidated trong top-3 pool, trong khi question plan để `scope=null`. Vì vậy
đây là một trade-off rõ ràng:

- Q102: candidate table có context `15. TÀI SẢN CÓ ĐỊNH VÔ HÌNH` và row/column
  khớp trực tiếp; control chọn nhầm row cash-flow.
- Q141: candidate table có context trái phiếu và tổng cộng đúng đơn vị; control
  chọn một row related-party loan.
- Q306: candidate table có context tài sản tài chính; control chọn tổng tài sản
  của balance sheet.

Các câu trên rất có khả năng là semantic correction theo source context, nhưng
không có gold cục bộ để xác nhận. Không hạ scope gate hoặc biến review score
thành authority chỉ để tăng số câu.

## Artifacts

- Control report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/control/submission/build_report.json`
- Candidate report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission/build_report.json`
- Control submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/control/submission/submission.json`
- Candidate submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission/submission.json`
- Candidate evidence directory:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission/data/`
- Candidate ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission.zip`
- Immutable snapshot used:
  `/tmp/vifinqa-lane-ab.tDxDZU`

## Quyết định pipeline

Giữ candidate-bound ở best-effort submission path vì nó sửa ba direct answers
với source binding cụ thể và không làm hỏng record/replay gate. Không gọi đây là
`Answer Accuracy +3`, không đưa vào strict certificate và không dùng để tạo
human verification/training labels khi chưa có semantic approval.

Vòng tiếp theo cần A/B lại trên current stabilized snapshot sau các thay đổi
temporal/alias mới. Khi có official scorer, ưu tiên đo precision của đúng ba
ID đã đổi và kiểm tra riêng ảnh hưởng scope; nếu precision không đạt, chuyển
lane về proposal-only thay vì xóa evidence replay.
