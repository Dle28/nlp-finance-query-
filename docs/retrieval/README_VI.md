# Vận hành retrieval toàn kho trên CPU và Kaggle

Phần này đã được triển khai sau Vòng nghiên cứu tài liệu 2A/2B. Lexical và
dense đều trả về ứng viên định tuyến; khi chạy `build-submission`, các ứng viên
đó được hợp nhất với review/research pool rồi hydrate lại từ bảng V2 trước khi
chọn ô và tạo dự đoán. Retrieval không được tự chép số hoặc tự cấp quyền cho ô
dữ liệu, answer, training record hay submission.

## Contract dữ liệu đầy đủ

Builder chỉ chạy khi đồng thời thỏa mãn:

- source closure: 1.973 báo cáo, gồm 8 báo cáo không có bảng;
- table assets: 146.246 bảng thuộc 1.965 báo cáo có bảng và 100 ticker;
- không trùng UID;
- SHA-256 của asset và source closure khớp config khóa tại
  `configs/retrieval/full_corpus_v1.json`.

Chỉ có 1.965 document ID trong asset JSONL là đúng vì 8/1.973 báo cáo không có
bảng. Không được đổi gate source report từ 1.973 xuống 1.965.

## Lexical: baseline mặc định trên CPU

Index chỉ dùng header và row path của bảng, không đưa context trước bảng vào
nội dung tìm kiếm. Ticker và năm là hai bộ lọc bắt buộc; scope chỉ được lọc khi
câu hỏi nói rõ vì nghiên cứu tìm thấy 34 tài liệu nghi vấn scope.

```bash
.venv/bin/python scripts/e2e/build_full_corpus_lexical_v1.py \
  --config configs/retrieval/full_corpus_v1.json \
  --assets /path/to/full_table_assets_v1.jsonl \
  --source-closure /path/to/source_closure_v1.jsonl \
  --output-dir artifacts/retrieval/full_corpus_lexical_v1_RUN

.venv/bin/python scripts/e2e/validate_full_corpus_retrieval_v1.py \
  --kind lexical \
  --artifact-dir artifacts/retrieval/full_corpus_lexical_v1_RUN

.venv/bin/python scripts/e2e/search_full_corpus_lexical_v1.py \
  --index artifacts/retrieval/full_corpus_lexical_v1_RUN/table_only_metadata_lexical_v1.sqlite \
  --query "Doanh thu thuần là bao nhiêu?" \
  --ticker VNM \
  --year 2023 \
  --limit 10
```

Artifact đã dựng và kiểm tra tại
`artifacts/retrieval/full_corpus_lexical_v1_20260826_r1`: 146.246 mục,
SQLite integrity `ok`, thời gian build 13,8 giây.

## Dense trên CPU

Cài runtime retrieval nếu môi trường hiện tại chưa có:

```bash
.venv/bin/python -m pip install -e ".[retrieval]"
```

CPU dùng đúng builder và output như CUDA. Builder checkpoint định kỳ; chạy lại
cùng command để tiếp tục. Không đổi model, device hoặc batch size khi resume.
File `.partial` không được search và không được validator chấp nhận.

```bash
.venv/bin/python scripts/research/build_full_corpus_dense_v1.py \
  --config configs/retrieval/full_corpus_v1.json \
  --assets /path/to/full_table_assets_v1.jsonl \
  --source-closure /path/to/source_closure_v1.jsonl \
  --output-dir artifacts/retrieval/full_corpus_dense_cpu_v1_RUN \
  --device cpu \
  --batch-size 16
```

Phép đo hiện tại dự báo khoảng 249 phút cho 146.246 bảng. CPU vẫn được giữ làm
fallback có thể resume; không nên chờ full build CPU trong vòng phát triển ngắn.
Smoke bằng model thật đã chạy đủ 8/8 bảng, dimension 384 tại
`artifacts/retrieval/dense_cpu_real_model_smoke_v1_20260826_r1`.

## Dense trên Kaggle GPU

Tạo code bundle:

```bash
.venv/bin/python scripts/research/package_full_dense_kaggle_v1.py \
  --output-dir artifacts/kaggle_upload/vifinqa_full_dense_code_v1_RUN
```

Trên Kaggle:

1. Tạo một Dataset chứa `full_table_assets_v1.jsonl` và
   `source_closure_v1.jsonl`.
2. Tạo Dataset thứ hai chứa `vifinqa_full_dense_code_v1.zip`.
3. Mở `notebooks/vifinqa_full_dense_index_kaggle_v1.ipynb`.
4. Attach hai Dataset, bật GPU và Internet; hoặc attach thêm model
   `intfloat/multilingual-e5-small` rồi điền `MODEL_PATH_OVERRIDE`.
5. Run All. Notebook chỉ tạo ZIP khi receipt đủ 146.246 vector, source closure
   đủ 1.973 báo cáo, device là CUDA và không còn checkpoint dở dang.
6. Tải `/kaggle/working/vifinqa_full_dense_index_v1.zip` về và chạy validator:

```bash
.venv/bin/python scripts/e2e/validate_full_corpus_retrieval_v1.py \
  --kind dense \
  --artifact-dir /path/to/unzipped_dense_index
```

Notebook không điều khiển tài khoản Kaggle và không tự upload Dataset. Người
vận hành thực hiện hai thao tác đó trong giao diện Kaggle.

## Nối vào primary submission

```bash
PYTHONPATH=.:src .venv/bin/python -m finance_query.cli build-submission \
  --dense-index-dir artifacts/kaggle_runs/vifinqa_full_dense_index_gpu_v1_20260826/vifinqa_full_dense_v1 \
  --output submissions/vifinqa_primary_dense_YYYYMMDD_rN
```

Builder chỉ lấy UID/document từ dense hit, đọc row/header/giá trị từ
`tables_structured_v2`, rồi ghi tier và nguồn vào `diagnostics.jsonl`.

## Những phần vẫn tắt

- không tự nối bảng nhiều trang;
- không tự suy đơn vị hoặc kỳ báo cáo;
- không coi scope từ tên file là bằng chứng duy nhất;
- không thay index sản xuất cũ chỉ vì build mới hoàn tất;
- không dùng retrieval candidate trực tiếp làm evidence hoặc answer; primary
  phải hydrate và chọn lại từ bảng V2.

## Candidate cho toàn bộ 1.012 câu hỏi

Luồng candidate đọc kế hoạch operand đã khóa, chỉ truy hồi 642 câu có trạng
thái `complete`; 370 câu còn lại vẫn có dòng trạng thái nhưng không được đoán
ticker, năm hoặc chỉ tiêu. Mỗi operand chạy hai lane theo thứ tự:

1. cụm chỉ tiêu trước tên doanh nghiệp, yêu cầu tất cả từ cùng xuất hiện;
2. truy vấn `OR` chỉ để bù recall tới top-k và được gắn nhãn riêng.

Nếu câu hỏi nêu scope, kết quả đúng scope được xếp trước. Kết quả không lọc
scope chỉ là `unscoped_supplement`, không được ngầm coi là bằng chứng đúng
scope.

```bash
.venv/bin/python scripts/research/build_full_corpus_candidate_retrieval_v1.py \
  --config configs/research/full_corpus_candidate_retrieval_v1.json \
  --plans artifacts/research/question_compiler_shadow_20260825/typed_operand_plans.jsonl \
  --lexical-index artifacts/retrieval/full_corpus_lexical_v1_20260826_r1/table_only_metadata_lexical_v1.sqlite \
  --assets artifacts/research/document_corpus_round2_assets_v1_20260826_r2/full_table_assets_v1.jsonl \
  --output-dir artifacts/research/full_corpus_candidate_retrieval_v1_RUN

.venv/bin/python scripts/research/validate_full_corpus_candidate_retrieval_v1.py \
  --artifact-dir artifacts/research/full_corpus_candidate_retrieval_v1_RUN
```

Artifact r2 đã kiểm tra tại
`artifacts/research/full_corpus_candidate_retrieval_v1_20260826_r2`:

- đủ 1.012 trạng thái câu hỏi;
- 12.320 table candidate, thuộc 11.044 bảng khác nhau;
- 31.743 packet dòng để kiểm tra;
- 3.480 candidate đến từ lane cụm chỉ tiêu đầy đủ và 8.840 candidate từ
  lane bù recall;
- không chứa raw numeric value; `human_verified_count = 0`;
- `answer_eligible = false`, `submission_eligible = false`.

Packet dòng chỉ chứa nhãn dòng, tọa độ nguồn, chỉ số cột có dạng số và SHA-256
của ô. Cột/kỳ/đơn vị vẫn chưa được xác nhận. Vì vậy artifact này là đầu vào
cho bước kiểm tra chính xác ô, chưa phải file submission.

## Probe: kiểm tra Top-K có chứa bảng mục tiêu không

Trước khi dùng một thay đổi retrieval cho analysis hoặc submission, có thể chạy
probe trên một tập nhỏ các bảng mục tiêu đã được xác minh độc lập. Probe chỉ
ghi UID bảng, rank và trạng thái `DIRECT_TARGET_RETRIEVED` hoặc
`DIRECT_TARGET_MISSED`; không ghi query gốc, source text, điểm score hay giá
trị số. Nó đo recall điều hướng, **không** đo semantic accuracy và không thể
cấp quyền cho evidence, answer, training hay submission.

Mỗi dòng JSONL probe có đúng các trường sau:

```json
{"probe_id":"example-vnm-2023","query":"Doanh thu thuần","ticker":"VNM","report_year":2023,"scope":"consolidated","expected_table_uids":["<uid-da-xac-minh>"]}
```

Ví dụ với lexical index:

```bash
PYTHONPATH=src .venv/bin/python scripts/e2e/probe_full_corpus_retrieval_v1.py \
  --kind lexical \
  --index artifacts/retrieval/full_corpus_lexical_v1_RUN/table_only_metadata_lexical_v1.sqlite \
  --probes /path/to/independently_adjudicated_probes.jsonl \
  --top-k 10 \
  --output-dir artifacts/retrieval/navigation_probe_lexical_RUN
```

Xác minh artifact trước khi so sánh baseline/candidate:

```bash
PYTHONPATH=src .venv/bin/python scripts/e2e/validate_retrieval_probe_v1.py \
  --artifact-dir artifacts/retrieval/navigation_probe_lexical_RUN
```

Một `DIRECT_TARGET_MISSED` là tín hiệu cần mở rộng retrieval hoặc xem lại query;
nó không cho phép chọn một bảng gần đúng thay thế. Chỉ tạo probe từ target UID
đã có cơ sở nguồn độc lập, không suy ra UID từ chính Top-K đang được đánh giá.

## Phân tích hybrid lexical + dense

Dense index Kaggle đã được nối vào vòng phân tích toàn bộ 1.232 tuyến
operand–năm. Artifact đã kiểm tra tại
`artifacts/research/hybrid_retrieval_analysis_v1_20260826_r4`.

- 380 tuyến có cùng top-1 giữa lexical và dense;
- 1.194 tuyến có ít nhất một bảng trùng trong top 10;
- 38 tuyến không có bảng trùng;
- hàng đợi review gồm 222 tuyến đồng thuận cao, 737 tuyến thông thường và 273
  tuyến khó;
- validator trả `PASS`, raw numeric value không được đưa vào artifact;
- mọi candidate vẫn là navigation-only và `submission_eligible = false`.

Báo cáo và diễn giải giả thuyết nằm tại
`docs/research/HYBRID_RETRIEVAL_ANALYSIS_V1.md`.

## Nghiên cứu exact-row, period, unit và scope

Artifact tiếp theo đã chạy tại
`artifacts/research/exact_cell_research_v1_20260826_r4` trên ba candidate bảng
mỗi route. Kết quả:

- 3.603/3.696 bảng có candidate dòng;
- 959 bảng có một cột ghi rõ năm; 2.148 bảng không ghi năm rõ;
- 2.392 bảng có unit từ header, nhưng chỉ là candidate;
- 833 candidate cấu trúc dùng explicit year và 698 candidate fallback nghiên
  cứu dạng `Năm nay/Số cuối năm`;
- 150 gói review cân bằng đã tạo, không chứa số tiền;
- validator trả `PASS`; tất cả vẫn research-only và không thể tạo answer.

Chi tiết và checklist review nằm tại
`docs/research/EXACT_CELL_RESEARCH_V1.md`.
