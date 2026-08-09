# Q369 — grounded staged review

Ngày kiểm tra: 2026-08-08.

## Kết luận

Theo đúng quy ước trong câu hỏi:

```text
Quick Ratio = (Tài sản ngắn hạn − Hàng tồn kho) / Nợ ngắn hạn
GPM = Lợi nhuận gộp / Doanh thu thuần × 100%
ΔGPM = GPM_2023 − GPM_2022              # không dùng trị tuyệt đối
Interest Coverage = EBIT / |Chi phí lãi vay|
EBIT = Lợi nhuận kế toán trước thuế + |Chi phí lãi vay|
```

kết quả audit là **HSG, khoảng 1,75 lần**.

Đây là answer audit từ raw source. Nó không làm cho retrieval label của Q369
trở thành complete: các bảng nguồn chính bên dưới đều nằm ngoài Top-20 của
bundle V3 hiện tại. Review retrieval phải tiếp tục giữ
`human_verified_partial`/`needs_human` cho đến khi candidate set được bổ sung
và exact rows được bind trong ledger.

## Stage 1 — Quick Ratio 2022

Số 2022 lấy từ cột so sánh trong báo cáo hợp nhất 2023. MSR dùng đơn vị nghìn
VND; ba công ty còn lại dùng VND. Tỷ số không đổi theo đơn vị.

| Công ty | Tài sản ngắn hạn | Hàng tồn kho | Nợ ngắn hạn | Quick Ratio |
|---|---:|---:|---:|---:|
| HPG | 80.514.710.854.456 | 34.491.111.096.123 | 62.385.392.809.685 | 0,7377 |
| HSG | 9.834.993.231.398 | 7.395.309.339.966 | 6.009.187.395.647 | 0,4060 |
| MSR | 12.228.222.738 | 6.858.167.780 | 17.154.059.090 | 0,3130 |
| NKG | 10.414.909.064.976 | 7.000.417.214.505 | 8.108.870.806.964 | 0,4211 |

Thứ tự tăng dần là MSR, HSG, NKG, HPG. Với bốn giá trị:

```text
median = (0,4060 + 0,4211) / 2 = 0,4135
```

Điều kiện nghiêm ngặt `< median` giữ lại **MSR và HSG**.

## Stage 2 — thay đổi GPM

| Công ty | GPM 2022 | GPM 2023 | ΔGPM (điểm %) |
|---|---:|---:|---:|
| HSG | 9,9349% | 9,6701% | -0,2648 |
| MSR | 15,2857% | 5,5650% | -9,7207 |

`-0,2648 > -9,7207`, vì vậy HSG có mức thay đổi cao nhất theo sai phân có dấu.

## Stage 3 — Interest Coverage của HSG năm 2023

Exact rows trong báo cáo kết quả kinh doanh hợp nhất HSG:

```text
Mã 50 | Lợi nhuận kế toán trước thuế | 146.022.559.563
Mã 23 | Trong đó: Chi phí lãi vay     | 195.489.503.107
```

Không gắn dòng mã 50 trực tiếp thành EBIT. Phép dẫn xuất giữ cả hai dòng:

```text
EBIT = 146.022.559.563 + 195.489.503.107
     = 341.512.062.670

Interest Coverage = 341.512.062.670 / 195.489.503.107
                  = 1,746958569...
                  ≈ 1,75 lần
```

## Source trace

Các bảng đều là raw HTML trong báo cáo hợp nhất 2023; ordinal là thứ tự bảng
trong đúng file nguồn.

| Entity | Balance-sheet tables | Income-statement table |
|---|---|---|
| HPG | ordinal 1 (TSNH, tồn kho), 3 (nợ ngắn hạn) | ordinal 5 |
| HSG | ordinal 2 (TSNH, tồn kho), 4 (nợ ngắn hạn) | ordinal 5 |
| MSR | ordinal 6 (TSNH, tồn kho), 8 (nợ ngắn hạn) | ordinal 9 |
| NKG | ordinal 7 (TSNH, tồn kho), 9 (nợ ngắn hạn) | ordinal 10 |

Source files:

- `data/ViFinQA/financial_statements/HPG/2023/HPG_financial_statements_2023_consolidated/HPG_financial_statements_2023_consolidated_extracted.txt`
- `data/ViFinQA/financial_statements/HSG/2023/HSG_financial_statements_2023_consolidated/HSG_financial_statements_2023_consolidated_extracted.txt`
- `data/ViFinQA/financial_statements/MSR/2023/MSR_financial_statements_2023_consolidated/MSR_financial_statements_2023_consolidated_extracted.txt`
- `data/ViFinQA/financial_statements/NKG/2023/NKG_financial_statements_2023_consolidated/NKG_financial_statements_2023_consolidated_extracted.txt`

## Retrieval diagnosis

Coverage của bundle hiện tại sau khi khóa đúng table function:

```text
quick_ratio_filter:       0/12 operands
gross_margin_rank:        1/16 operands
interest_coverage_lookup: 2/8 operands
overall:                  partial
```

Điều này phân biệt hai kết luận không được trộn lẫn:

1. Raw source đủ để audit phép tính và answer.
2. Top-K bundle chưa đủ để tạo retrieval gold complete.
