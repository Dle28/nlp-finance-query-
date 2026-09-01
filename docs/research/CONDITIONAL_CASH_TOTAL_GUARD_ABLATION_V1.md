# Conditional-temporal cash-total guard ablation

Ngày: 2026-08-30

## Kết luận

Một nới guard rất hẹp cho phép dòng `TỔNG CỘNG` được dùng trong
`conditional-temporal` khi và chỉ khi source resolver đã phát ra contract
`contextual_cash_and_equivalents_total` và metric condition chứa đầy đủ các
token của ngữ cảnh tiền và các khoản tương đương tiền. Guard này mở thêm một
proposal Q521; không làm thay đổi các câu khác ngoài bốn proposal
conditional-temporal đã được audit trước đó.

Đây là thay đổi trong lane
`authorized_best_effort_submission_candidate`, không phải chứng nhận vàng.
Workspace không có gold answer/official scorer nên con số dưới đây là answer
and tier coverage delta của A/B, không phải điểm Answer Accuracy.

## A/B bất biến

Control và variant dùng cùng snapshot:

- builder SHA-256:
  `447e94ddfe28fa43cda28a2bd7c6a3c67523d173987e2ce70d48bf9c42573d60`
- `source_first_lookup.py` SHA-256:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- `changed_during_build=false` ở cả hai report
- structured corpus: 146,246 tables; SHA-256
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- source-line map: 146,246/146,246 entries; SHA-256
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`
- snapshot runtime:
  `/tmp/vifinqa-conditional-cash-total-ab.zN085J`

Các lane source-first khác đều bị tắt ở cả hai arm. Chỉ khác biệt là
`--disable-source-first-conditional-temporal` ở control.

Artifacts:

- control report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/control/submission/build_report.json`
- control ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/control/submission.zip`
- variant report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/conditional_cash_total/submission/build_report.json`
- variant ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/conditional_cash_total_ab_v1/conditional_cash_total/submission.zip`

Hai arm đều có 1,012 record, 1,012 query replay, `errors=[]`,
`validation.valid=true`, source-line map coverage pass và ZIP `testzip=None`.
Verification class vẫn là `PARTIAL=1,010`, `UNRESOLVED=2`; không có strict
certificate.

## Delta quan sát được

| ID | control | variant | thay đổi |
|---:|---:|---:|---|
| 505 | `343.284396028` / semantic heuristic | `4.5` / conditional | đã có ở conditional baseline; IJC, max vay ngắn hạn ngân hàng tại 2024 |
| 514 | `126.676968019` / semantic heuristic | `62.125525058` / conditional | đã có ở conditional baseline; HDG, min lưu chuyển tài chính tại 2017 |
| 521 | `11.672448686` / semantic heuristic | `500.688616629` / conditional | proposal mới; HDG, max tổng tiền và tương đương tiền tại 2023 |
| 523 | `21.57966351` / semantic heuristic | `2.055255183` / conditional | đã có ở conditional baseline; DCM, max lãi dự thu tại 2023 |
| 526 | `-394.259405152` / semantic heuristic | `-83.790676915` / conditional | đã có ở conditional baseline; MWG, max EPS tại 2025 |

Ngoài năm thay đổi answer trên, diff trực tiếp của A/B chỉ nằm ở
`relevant_docs`, `relevant_tables`, `pandas_query` và `prediction_tier` của
cùng 5 ID; `evidence` schema không đổi. Variant thống kê:

- conditional questions considered: 26
- resolved: 5, unresolved/ambiguous: 21
- operator: max 4, min 1
- confidence tier `source_first_conditional_temporal_v1`: 5
- confidence tier `semantic_cell_heuristic`: giảm từ 679 xuống 674

## Audit độc lập Q521

Resolver đọc lại các cell hiện tại trong structured corpus, không lấy số từ
retrieval text. Condition values được so sánh ở source-normalized unit:

| Năm | source row | raw VND |
|---:|---|---:|
| 2021 | `TỔNG CỘNG` trong section `TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN` | `230395142669` |
| 2023 | `TỔNG CỘNG` trong section `TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN` | `694458293386` |
| 2025 | `TỔNG CỘNG` trong section `TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN` | `265730670677` |

Giá trị lớn nhất là năm 2023. Answer cell cùng report/scope là dòng
`Chi phí lãi vay`, raw `500688616629` VND; requested output unit là tỷ đồng,
nên answer replay là `500.688616629`.

Các condition tables đều có `scope=consolidated`, exact requested report year
và match mode contextual `cash_and_equivalents_total`. Answer table cũng là
exact-year `HDG_financial_statements_2023_consolidated`, với row label đúng
`Chi phí lãi vay`. Q521 được thay đổi vì context contract chứng minh ý nghĩa
của generic total row; metric khác vẫn bị guard loại.

## Guard và regression

Guard chỉ thêm match mode contextual vào allowlist và yêu cầu metric condition
chứa token set `tien`, `cac`, `khoan`, `tuong`, `duong`; generic `TỔNG CỘNG`
với metric khác vẫn bị reject. Derived rows như `Trích trước ...` không được
nới lỏng.

Focused tests sau thay đổi:

```text
67 passed in 0.60s
```

## Quyết định

Giữ thay đổi ở proposal lane vì nó có source replay và semantic context rõ
ràng, nhưng chưa promotion thành `VERIFIED`. Không hạ threshold chung và
không mở generic subtotal cho các metric khác. Bước tiếp theo nên kiểm tra
gold/human semantic cho 5 proposal conditional; sau đó mới cân nhắc route
ratio/composed-execution cho các câu còn lại, theo family thay vì sửa từng
Question-ID.
