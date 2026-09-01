# OCR corpus coordinate và full-corpus retrieval ablation v1

Ngày chạy: 2026-08-29

## Kết luận

Có hai vấn đề khác nhau:

1. `relevant_tables` cần tọa độ dòng bắt đầu của bảng trong OCR report.
2. Retriever cần nhìn đủ các bảng để không bỏ sót bảng đúng trước khi hydrate
   và bind exact cell.

Vấn đề (1) đã được sửa đầy đủ. Tọa độ hiện tại không còn là điểm cần tối ưu
thêm: `source_line_map.json` của V2 khớp chính xác với inventory OCR gốc trên
toàn bộ 29.509 bảng V2. Submission hiện tại có 3.736 reference, tất cả đều
khớp một dòng bảng trong inventory gốc và không còn local-ordinal fallback.

Vấn đề (2) còn dư địa thực sự. Full OCR corpus chứa 146.246 bảng, trong khi
V2 chỉ chứa 29.509 bảng. Full asset là một superset provenance-đúng của V2,
thêm 116.737 bảng. Candidate ablation cho thấy full corpus làm thay đổi tập
candidate đáng kể, nhưng chưa thể gọi là tăng accuracy/leaderboard vì bộ dữ
liệu công khai không có gold relevant-table labels.

## Input và contract

- OCR inventory gốc:
  `data/process/audit_output/table_inventory.csv`
- V2 structured tables:
  `artifacts/kaggle_upload/chocomica_vifinqa_primary_runtime_v1_20260829/tables_structured_v2.jsonl`
- Full asset build:
  `artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl`
- Full lexical navigation index:
  `artifacts/retrieval/full_corpus_lexical_v1_20260829_r1/table_only_metadata_lexical_v1.sqlite`
- Full candidate ablation:
  `artifacts/research/full_corpus_candidate_retrieval_v1_20260829_r1/`
- V2 candidate ablation:
  `artifacts/research/v2_corpus_candidate_retrieval_v1_20260829_r1/`

Các artifact retrieval trong báo cáo này đều có contract
`navigation_metadata_only=true`, `may_authorize_answer=false` và
`submission_eligible=false`. Chúng chỉ mở rộng navigation; exact source,
numeric cell, Decimal replay và release gate vẫn nằm ở authority path.

## 1. Audit tọa độ OCR

| Population | Số bảng | Kết quả |
|---|---:|---|
| Inventory OCR gốc | 146.246 | toàn corpus |
| Structured V2 | 29.509 | 20,1776% inventory |
| Full asset | 146.246 | 100% inventory |
| V2 UID có mặt trong full asset | 29.509/29.509 | exact |
| V2 `(source_path, char_start)` khớp inventory | 29.509/29.509 | exact |
| Full `(source_path, char_start)` khớp inventory | 146.246/146.246 | exact |
| Bảng chỉ có trong full asset | 116.737 | coverage mở rộng |

Submission hiện tại:

- 1.012 records.
- 3.736 `relevant_tables` references, 3.315 references duy nhất.
- 3.736/3.736 reference tồn tại trong inventory OCR gốc.
- 0 malformed reference.
- `local_ordinal_fallback=0`.

Đây là bằng chứng rằng việc đổi `local_ordinal + 1` sang canonical OCR
table-start line đã xử lý đúng nguyên nhân làm table score trước đó bằng 0.
Không nên tiếp tục sửa tọa độ bằng heuristic khác.

## 2. Full corpus có thể giúp ở đâu?

Full asset được build lại từ cùng source parser và deterministic replay pass:

- 1.973 source reports.
- 1.965 documents có bảng.
- 146.246 bảng.
- 0 build failure.
- deterministic replay match: `true`.
- asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`.
- source closure SHA-256:
  `59f27c643bd1c4a640e7670dd94789e3222c27a6022be3f7319947b65b6dd9fd`.

Full corpus không chủ yếu cứu việc thiếu report. Phân tích typed question plan
cho thấy coverage document khả dĩ của V2 là khoảng 99,76%; toàn bộ 313 câu
direct one-ticker/year đều có document đích trong V2. Dư địa chính là nhiều
bảng trong một document đã có mặt nhưng bị loại khỏi V2, cộng thêm một số
document/scope còn thiếu.

## 3. Candidate ablation cùng protocol

Cả hai nhánh dùng cùng 1.012 typed plans, cùng scope/ticker/year filters,
`match_mode=any`, `top_k_tables_per_operand_year=10` và cùng row-review logic.
So sánh bằng khóa vật lý `(document_id, char_start)`, không so bằng UID đơn
thuần.

| Chỉ số | V2-only | Full OCR | Chênh lệch / ý nghĩa |
|---|---:|---:|---|
| Table-candidate rows | 10.442 | 10.650 | +208 |
| Physical candidate tables duy nhất | 8.794 | 9.707 | +913 |
| Physical candidate chỉ full chọn được | - | 4.770 | candidate mới hoặc được thay thế |
| Physical candidate chỉ V2 chọn được | 3.857 | - | full thay đổi ranking/pool |
| Route groups | 1.062 | 1.065 | full thêm 3 group |
| Group đổi tập physical candidates | - | 1.029/1.065 | thay đổi lớn |
| Top-1 physical đổi | - | 511/1.065 | ranking bị tác động mạnh |
| Top-10 physical đổi | - | 1.061/1.065 | gần như toàn bộ route bị tác động |
| Row review packets | 26.002 | 27.309 | +1.307 |

Full corpus cũng cải thiện trạng thái routing:

- Full: 599 `ROUTED`.
- V2-only: 596 `ROUTED`, 2 `PARTIAL`, 1 `NO_CANDIDATE`.

Các con số này chứng minh full corpus có khả năng cải thiện candidate recall,
nhưng không chứng minh candidate mới là bảng đúng. Việc mở rộng top-k cũng có
thể đưa thêm nhiễu và thay top-1 đúng bằng bảng gần nghĩa.

## 4. Luồng được phép dùng cho submission

```text
question + typed/context plan
        -> full-corpus lexical/dense navigation
        -> candidate UID + source provenance
        -> structured-table hydration
        -> model rerank (ranking only)
        -> exact row/column/period/unit binding
        -> Decimal execution + replay
        -> canonical OCR line mapping
        -> submission.json -> submission.zip
```

Full corpus không được dùng để copy giá trị số trực tiếp từ index. Candidate
UID phải được hydrate từ structured asset, exact cell phải qua source/structure
contract, và `relevant_tables` phải lấy từ source-line map theo UID.

## 5. Quyết định cho Kaggle

Đã có các artifact nền để chạy controlled full-hydration ablation. Job mới cần
giữ nguyên model, prompt, typed plans, downstream verifier và output schema;
chỉ thay population hydrate từ 29.509 V2 tables sang full 146.246 tables.
Kết quả được chấp nhận để so score khi:

1. Kaggle job ở trạng thái `COMPLETE`.
2. `build_report.json` có 1.012 records, 1.012 queries replayed và `errors=[]`.
3. `source_line_map` phủ đủ population, `local_ordinal_fallback=0`.
4. ZIP được tải về, kiểm tra `unzip -l`, record count và hash.
5. ZIP full-hydration được scorer chạy cùng split với ZIP coordinate-repair
   hiện tại.

Không dùng retrieval proxy, model score, `RUNNING`, hay artifact metadata cũ
để kết luận full corpus đã tăng leaderboard.
