# Canary cho câu hỏi nhiều bước

Tài liệu này ghi lại kết quả triển khai QueryProgram shadow V1 trên workspace
`run_full_metadata_support_v1`. Đây là artifact kiểm thử grounding/execution,
không phải đáp án, review label hay dữ liệu train.

## Contract chung

Một program chỉ được `shadow_ready` nếu Formula EvidenceSet đã chứng minh:

```text
formula definition = defined
mọi required operand đã được selected
mọi selected operand = exact raw V2 cell_bound + parsed value
ticker và report year đúng với operand đã khai báo
một reporting scope chung, không rỗng
với Net Margin: tử số và mẫu số cùng source unit
coverage = complete
reason code còn lại chỉ là stage-binding hand-off
```

Sau đó executor Decimal chỉ đọc `binding.parsed_value` của selected matches.
Nó không search bảng, không đọc OCR text, không điền ô thiếu, không đổi scope,
không đổi provenance và luôn trả `submission_eligible=false`.

## Canary A — Q369 bị chặn đúng

Q369 có chuỗi Quick Ratio 2022 → lọc dưới median → thay đổi Gross Margin
2022–2023 → Interest Coverage 2023. Đây là program có 36 operand.

| Kết quả | Số lượng | Ý nghĩa |
| --- | ---: | --- |
| `raw_table_already_in_bundle_but_unbound` | 35 | Không thiếu table; evidence binder chưa có coherent selection. |
| `raw_metric_or_statement_not_found` | 1 | Không được bù bằng suy diễn hay table gần giống. |
| Global scope chung | 0 | HPG, HSG, MSR, NKG không có một scope dùng được cho mọi operand. |

Do đó Q369 giữ `shadow_blocked`. Chạy source completion ở đây sẽ không giải
quyết 35 binding chưa coherent và không được tự chọn scope để làm phép tính.

## Canary B — Q551 chạy shadow có kiểm soát

Q551 có chuỗi CFO dương 2022–2024 → lọc entity → argmax
`lợi nhuận sau thuế / doanh thu thuần` năm 2024. EvidenceSet có 3 entity ×
(3 CFO + 2 operand NPM) = 15 operand.

| Gate | Kết quả |
| --- | --- |
| Formula definition | `defined` |
| Operand coverage | `complete` |
| Exact bindings | 15/15 `cell_bound` |
| Scope được EvidenceSet chọn | `consolidated` cho toàn bộ operand |
| Reason code còn lại | chỉ hai stage-binding hand-off allow-list |
| Executor | `shadow_complete`, không submission/provenance promotion |

Artifact `query_program_shadow_v1.jsonl` lưu winner, giá trị và stage trace để
audit. Consumer không được dùng các field đó làm final answer hoặc train label.
Muốn mở rộng ngoài shadow cần fixture independent cho từng stage và một policy
promotion riêng, không chỉ dựa vào việc arithmetic hoàn tất.

## Vận hành

```bash
python scripts/build_query_program_shadow.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1 \
  --formula-evidence ~/ViFinQA_review/run_full_metadata_support_v1/formula_evidence_sets_context_v3_entity_titles_v1.jsonl \
  --output ~/ViFinQA_review/evaluation_metadata_support_v1/query_program_shadow_v1.jsonl
```

Sau mỗi lần materialize lại sidecar, rebuild ArtifactRegistry rồi validate DAG.
Không rebuild Kaggle corpus, FTS hay FAISS cho bước này.
