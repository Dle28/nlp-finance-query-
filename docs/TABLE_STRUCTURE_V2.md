# Table Structure V2 — local repair without rebuilding indexes

## Mục đích

V3 bundle có thể mang theo `rows` được tạo bởi parser cũ. Parser đó bỏ ô
`<td></td>` và không mở rộng `rowspan`/`colspan`, nên một số giá trị nhìn như bị
lệch cột. Table Structure V2 dựng lại grid trực tiếp từ raw HTML của report.

Nó không sửa OCR, không thay số liệu, không gọi model và không rebuild Kaggle,
SQLite FTS hoặc FAISS.

## Data contract

Sidecar cục bộ có tên mặc định:

```text
<bundle-dir>/tables_structured_v2.jsonl
<bundle-dir>/table_structure_v2.manifest.json
```

Mỗi row của sidecar được khóa bằng `internal_table_uid` của bundle và chứa:

```text
rows                    # grid chữ nhật, giữ cả ô trống
column_labels           # header hiển thị, chỉ lấy từ raw grid
header_row_indices
cell_provenance         # source row/cell cho từng ô grid
table_function          # deterministic: balance_sheet, cash_flow_statement, ...
table_section           # asset, liability, equity, ... hoặc unknown
table_purpose           # cách dùng: period comparison, movement, roster, ...
context_trace           # source title/topic, kỳ, đơn vị; đều lấy từ source
structure_quality       # cờ cấu trúc; không phải OCR/human verification
source_provenance       # path, raw source hash, raw table hash, char offset
```

`context_trace.topic` ưu tiên tiêu đề mục/thuyết minh đánh số gần bảng.
`context_trace.source_title` giữ tiêu đề nguồn đầy đủ để audit nhưng mặc định
nằm trong phần thu gọn của UI. `table_function.specificity=broad` chỉ xác định
được bảng thuộc vùng thuyết minh; `generic` chỉ xác định được dạng bảng tổng
quát. Cả hai không phải một phân loại nghiệp vụ chắc chắn.

`specificity=structural` yêu cầu đồng thời nhiều dòng đặc trưng của một primary
statement. Ví dụ `doanh thu thuần` + `lợi nhuận gộp` + `kế toán trước thuế`
nhận diện báo cáo kết quả kinh doanh ngay cả khi title OCR bị hỏng. Với báo cáo
lưu chuyển tiền tệ, section được cố định là `cash_flow`; các dòng giao dịch có
từ “tài sản” không được phép đổi cả bảng thành section `asset`.

Mỗi table được kiểm tra lại bằng UID tính từ `document_id`, ordinal, char
offset và raw table hash. Khi source không khớp, tiến trình dừng mặc định thay
vì tạo một sidecar thiếu hoặc ghép sai nguồn.

Khi đọc sidecar, widget/reviewer/ledger kiểm tra lại checksum của
`tables.jsonl`, checksum sidecar, manifest row count và `error_count=0`.
Sidecar bị sửa, thuộc bundle khác hoặc chỉ repair một phần đều bị từ chối.

`structure_quality.status=reconstructed_from_raw_html` chỉ nói rằng vị trí ô
đã được dựng từ raw HTML. Nó không có nghĩa OCR hay số liệu đã được
`human_verified`.

## Chạy cho bundle V3 hiện tại

Sau khi extract archive, chạy trong terminal:

```bash
cd ~/Documents/AI_guru
source .venv/bin/activate

python local/run_local_review_stage.py repair-tables \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --repair-force
```

Hoặc chạy trực tiếp:

```bash
python scripts/repair_review_bundle_tables.py \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --reports-root data/ViFinQA/financial_statements \
    --force
```

`--repair-force`/`--force` cần dùng khi sidecar cũ đã tồn tại và cần cập nhật
context schema mới. Nó chỉ thay sidecar local, không thay bundle/index.

Lệnh chỉ ghi hai sidecar ở bundle local. Archive gốc và toàn bộ index giữ
nguyên.

Sau đó chạy widget trong Jupyter. Widget tự phát hiện sidecar:

```python
%cd ~/Documents/AI_guru

%run local/review_bundle_widget.py \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --machine-reviews data/labels/machine_reviews_60.jsonl \
    --queue data/labels/human_seed_queue_12.jsonl \
    --output data/labels/retriever_verified_60.jsonl
```

## UI safety rules

Màn hình chính chỉ hiển thị:

```text
chức năng bảng
chức năng sử dụng nhanh của bảng
tiêu đề mục gần nhất; tiêu đề dài nằm trong trace thu gọn
mức phù hợp với câu hỏi
preview tối đa 4 dòng dùng nhãn cột nguồn
tóm tắt một dòng grounded
```

Grid và raw rows nằm trong phần mở rộng. Nếu câu hỏi cần `liability` nhưng
bảng được phân loại `asset`, UI chặn nút `Accept machine`, ẩn evidence số liệu
khỏi màn hình nhanh và cho phép human override có chủ đích qua checkbox.

`unknown` không được diễn giải là phù hợp; nó chỉ yêu cầu kiểm tra thêm.
Tương tự, function có `specificity=broad|generic` chỉ hỗ trợ điều hướng UI.
Ngay cả `structural` cũng chỉ xác định chức năng bảng; số liệu vẫn phải map vào
exact row, đúng period column và đúng formula operand.

Với câu temporal/ratio/derived, UI hiện thêm formula context và `EvidenceSet`.
Mỗi operand được map vào candidate UID/rank, exact source row và column labels.
Candidate không map vào operand bị ẩn số liệu ở quick view. Multi-operand
formula không được `Accept machine`; nếu thiếu operand hoặc human chưa xác
nhận định nghĩa công thức thì chỉ lưu `human_verified_partial`. Chi tiết:
[`FORMULA_EVIDENCE_SETS.md`](FORMULA_EVIDENCE_SETS.md).

Nếu sidecar V2 chưa có, quick numeric view và `Accept machine` cũng bị khóa.
Mọi lựa chọn positive mới chỉ được lưu partial, tránh biến legacy grid lệch cột
thành training gold.

Positive label được tạo trước structure gate không bị xóa hay đổi provenance.
Widget/ledger đánh dấu nó cần V2 revalidation; calibrator và final training
export bỏ qua label đó cho đến khi human lưu lại bằng UI mới.

V3.1 machine reviewer cũng đọc sidecar trước khi vote. Projected `VALUE` hoặc
`ANCHOR` phải khớp nguyên văn với đúng `best_row_index` V2; nếu không, candidate
bị loại trước consensus. `machine_calibrated`/`machine_high_confidence` thiếu
exact-row validation không được training export.

## Khi nào cần rebuild index

Không cần rebuild index để sửa màn hình review của bundle hiện tại: sidecar
giữ nguyên candidate UID và chỉ sửa cấu trúc hiển thị/provenance.

Chỉ sau khi canary review xác nhận parser V2, nếu muốn retrieval dùng grid,
header và semantic metadata V2, hãy build `table_assets_v2` song song rồi
đánh giá lexical/dense trước khi thay artifact đang dùng. Không ghi đè
`table_assets.jsonl` hay index V3 hiện tại.

## Provenance nhãn

`human_verified`, `machine_calibrated`, `machine_provisional` và
`needs_human` là provenance review. Chúng độc lập với `structure_quality`.
Không có bước sửa cấu trúc/OCR nào được phép nâng nhãn machine thành nhãn human.
