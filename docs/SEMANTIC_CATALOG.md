# Semantic Catalog V1

## Vai trò

Semantic Catalog là view metadata nhỏ gọn cho mỗi bảng, dùng để reviewer đọc
nhanh, filter candidate và phân rã evaluation. Nó được dựng song song từ V2,
V3 và Report Segment V1:

```text
V2 structure + V3 canonical context + Report Segment
  → Semantic Catalog V1
```

Nó **không** là lớp truth nằm giữa V3 và evidence. Contract cố định:

```json
{
  "metadata_only": true,
  "evidence_eligible": false,
  "training_eligible": false,
  "may_repair_ocr": false,
  "may_change_candidate_rank": false
}
```

Evidence vẫn phải bind từ V3 về exact raw V2 row/cell/header. Catalog không
được phép chọn cell, kết luận metric, suy luận scope, sửa OCR hay promote
`machine_calibrated`.

## Trường metadata

- `document_role`: `primary_financial_statement`, `financial_note`,
  `supporting_schedule`, `governance_or_source_table`, hoặc `unclassified`.
  Primary chỉ đến từ function V2/V3 đã phân loại là báo cáo chính; generic
  `financial_data_schedule` chỉ thành financial note khi có
  `numbered_heading` nguồn.
- `statement_family`: chỉ hiện với primary statement (`balance_sheet`,
  `income_statement`, `cash_flow_statement`, `equity_change_statement`).
- `note_hierarchy`: đường dẫn như `6 → 6.2`, chỉ tách từ `source_heading`
  được Report Segment nhận diện là numbered heading. Không có heading nguồn thì
  để rỗng.
- `layout`: độ sâu header, số cột, số dòng data, số cột numeric bindable và
  số cột có kỳ. Đây là count cấu trúc, không mang numeric values.
- `derivation_basis`: function/heading-kind quan sát được để audit.

`unclassified` là kết quả chủ động fail-open cho navigation: nó bảo toàn bảng
nhưng không bịa vai trò nghiệp vụ. Nó không phải một dấu hiệu rằng dữ liệu sai.

## Build và validation

```bash
python scripts/build_semantic_catalog.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1
```

Builder validate hash V2, V3 và Report Segment trước khi ghi atomically
`semantic_catalog_v1.jsonl` cùng manifest. Không có Kaggle rebuild, FTS/FAISS,
OCR correction, đổi rank hoặc đổi label.

## Snapshot hiện tại

Trên 29.509 bảng:

| Document role | Bảng |
| --- | ---: |
| `primary_financial_statement` | 5.400 |
| `financial_note` | 14.359 |
| `supporting_schedule` | 6.056 |
| `governance_or_source_table` | 1.016 |
| `unclassified` | 2.678 |

Trong primary statements: Balance Sheet 2.028, Cash Flow 1.785, Income
Statement 1.363 và Equity Change 224. Các con số là coverage metadata, không
phải gold labels hay retrieval metric.

## Dùng tiếp theo

UI có thể hiển thị role/function/layout trước bảng; evaluator có thể phân nhóm
recall/grounding theo role. Bất kỳ filter hoặc penalty retrieval tương lai nào
phải được canary-evaluate tách biệt và vẫn không được biến catalog thành
evidence truth.
