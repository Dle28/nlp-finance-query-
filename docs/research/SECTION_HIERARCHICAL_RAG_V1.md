# RAG mục lớn → bảng cho ViFinQA (v1)

## Kết luận ngắn

Hệ thống hiện có ba tầng rõ ràng:

```text
Câu hỏi
  → mục lớn trong đúng báo cáo (semantic navigation)
  → bảng nguồn (table retrieval)
  → ô/dòng chính xác V2 (evidence và phép tính E2E)
```

Tầng mới chỉ làm nhiệm vụ tìm đường. Nó không có giá trị ô tài chính, không
tạo `answer`, không có `human_verified`, và không thể mở khóa evidence, E2E
hay submission.

## Vì sao cần thêm mục lớn

Một bảng đơn lẻ thường chỉ có tiêu đề ngắn, trong khi ý nghĩa của nó nằm ở
tiêu đề mục, ghi chú cha, loại báo cáo, đơn vị và các bảng kề nhau. Truy hồi
trực tiếp bảng vì vậy dễ nhầm các bảng có cùng tên dòng nhưng khác mục đích.

Giả thuyết được kiểm tra là: nếu trước hết tìm được mục lớn của báo cáo, tập
bảng đưa vào bước kế tiếp sẽ có ngữ cảnh tốt hơn. Điều này đặc biệt hữu ích
với thuyết minh nhiều bảng liên tiếp.

## Thiết kế chunk đã thử

| Phiên bản | Cách cắt | Số mục lớn | Nhận xét |
| --- | --- | ---: | --- |
| r1 | Bám sát từng heading OCR | 128.873 | Quá nhỏ, gần bằng tìm từng bảng |
| r2 | Nới heading một phần | 105.610 | Vẫn bị OCR chia vụn |
| r3 | Heading cha rõ ràng → ba báo cáo chính → dải thuyết minh liên tiếp | 28.924 | Dùng để đánh giá |

Ở r3, một chunk có tối đa tám bảng liên tiếp cùng ngữ cảnh. Toàn bộ 146.246
bảng nguồn được gán **đúng một lần** vào một trong 28.924 chunk; validator đã
kiểm tra tính phủ này cùng hash của dữ liệu nguồn. Chunk chỉ giữ nhãn ngữ
nghĩa và locator/hash của bảng con; những mảnh mang chữ số bị loại khỏi text
ngữ nghĩa.

## Mô hình và cách truy hồi

- Embedding: `intfloat/multilingual-e5-small`, open weight, 384 chiều.
- Mục lớn dùng tiền tố `passage:`; câu hỏi dùng `query:`; vector được chuẩn
  hóa trước khi so độ gần.
- Truy hồi vẫn khóa theo mã công ty và năm; scope hợp nhất/riêng được ưu tiên
  trước, sau đó mới bổ sung kết quả không scope.
- So sánh ba lane trên cùng 1.232 route đã đóng băng:
  - `table_only`: projection đã làm sạch của hybrid lexical+dense hiện hữu;
  - `section_only`: mở các bảng con từ năm mục lớn tốt nhất;
  - `hierarchical`: gộp hai lane bằng reciprocal-rank fusion rồi giữ mười
    bảng đầu.

Ngoài ba lane chính, một diagnostic riêng thử báo cáo năm kế tiếp cho các cột
so sánh. Các bảng con được lấy theo lượt (mỗi parent một bảng trước) để một
section rộng không chiếm hết top-k. Diagnostic này chỉ kiểm tra giả thuyết
period-routing, không thay đổi lane E2E hay quyền evidence.

Section chunk không thay bảng hay ô: mọi kết quả cuối cùng vẫn phải qua exact
V2 cell binding, kiểm tra header/kỳ/đơn vị/scope và executor Decimal của E2E.

## Đo lường có thể kết luận gì

Artifact evaluator xuất `coverage_report_v1.json` và route-level candidates.
Nó đo được:

- số candidate ở mỗi lane;
- khả năng giữ lại bảng V2 `execution_replay_ready` trong top-k, chỉ như một
  diagnostic navigation;
- sự thay đổi giữa table-only, section-only và hierarchical trên cùng route.

Nó **không** là accuracy của đáp án. Trường `accuracy_claim` bị cố định là
`NOT_AVAILABLE_WITHOUT_INDEPENDENT_SOURCE_ADJUDICATED_GOLD`. Muốn nói một
mô hình trả lời tốt hơn cần gold độc lập hoặc source review cho mục/bảng/dòng
và header.

## Cách chạy

CPU có thể tiếp tục/resume:

```bash
.venv/bin/python scripts/research/build_section_dense_index_v1.py \
  --config configs/research/section_hierarchical_rag_evaluation_v1.json \
  --section-artifact-dir artifacts/research/section_chunk_assets_v1_20260826_r3 \
  --output-dir artifacts/retrieval/section_dense_cpu_v1_20260827_r1 \
  --device cpu --batch-size 64
```

Trước khi chạy evaluation, tạo projection baseline không mang trường review:

```bash
.venv/bin/python scripts/research/build_section_rag_table_baseline_v1.py \
  --hybrid-artifact-dir artifacts/research/hybrid_retrieval_analysis_v1_20260826_r4 \
  --output-dir artifacts/research/section_rag_table_baseline_v1_20260827_r1
```

Notebook GPU tái lập nằm tại
`notebooks/vifinqa_section_hierarchical_rag_kaggle_v1.ipynb`; code bundle là
`artifacts/kaggle_packages/section_hierarchical_rag_v1_20260827_r1/`.
Notebook buộc GPU, hash của section assets và đúng 28.924 chunk trước khi
đánh giá. Nó tạo một ZIP nghiên cứu để tải về, không phải ZIP submission.

## Điều kiện để đi tiếp

Sau khi index hoàn thành, chạy evaluator trên 1.232 route, kiểm tra artifact,
và so lane theo diagnostic recall. Nếu hierarchical tốt hơn, nó chỉ được dùng
để ưu tiên navigation candidates. Không có kết quả nào được tự chuyển thành
binding, approval, answer certificate hoặc bài nộp.

## Kết quả diagnostic và vòng bổ sung route

GPU Kaggle đã tạo đủ 28.924 vector và remote validator đã pass cho 1.232
route. Artifact được tải về, kiểm hash output theo manifest, rồi chạy lại
diagnostic local với 14 câu có execution replay-ready và 15 bảng V2 đích.

| Lane | Câu có ít nhất một bảng đích trong top-10 | Tỷ lệ theo câu |
| --- | ---: | ---: |
| table-only | 12/14 | 85,71% |
| section-only | 12/14 | 85,71% |
| hierarchical | 12/14 | 85,71% |
| period-neighbor + parent-diverse | 13/14 | 92,86% |

Q292 là trường hợp báo cáo-năm khác năm-cột: câu hỏi theo kỳ 2019, còn bảng
V2 đích nằm trong báo cáo 2020 có cột so sánh 2019. Với filter chỉ đúng năm
2019, parent đích không thể được truy hồi. Khi thử năm báo cáo kế tiếp,
parent đó đứng hạng ba; chiến lược lấy lần lượt bảng con của từng parent đưa
bảng đích vào vị trí sáu.

Kết luận cũ về Q702 đã được sửa sau khi đối chiếu lại kho route. Q702 không
có trong **catalog hybrid đóng băng 1.232 route**, nhưng đã có hai candidate
route source-bound trong artifact full-corpus và audit materialization: một
route cho `other_income`, một route cho `other_expense`, cùng báo cáo CEO 2018
riêng. Baseline r2 chỉ thêm hai route navigation này (1.234 route tổng cộng),
kiểm hash manifest và loại toàn bộ giá trị, cờ reviewer, evidence hay quyền
tạo đáp án.

Trên baseline r2, table-only và section-only đều giữ đủ hai bảng Q702 qua hai
route operand; hierarchical cũng giữ được chúng. Vì Q702 không phải cột so
sánh, lane thử năm kế tiếp không giữ được hai bảng này. Điều này cho thấy lỗi
ở Q702 là **catalog coverage**, không phải embedding hay chunk semantic.

Vòng r6 thử một lane quota hai năm, cố định 4 bảng từ hierarchical đúng năm
và 6 bảng từ lane năm báo cáo kế tiếp. Đây là một cách rộng retrieval có giới
hạn rõ ràng, không phải thay đổi E2E. Kết quả trên cùng tập diagnostic:

| Lane | Câu có ít nhất một bảng đích | Câu có đủ tất cả bảng đích | Bảng đích giữ lại |
| --- | ---: | ---: | ---: |
| table-only | 13/14 | 13/14 | 14/15 |
| section-only | 13/14 | 13/14 | 14/15 |
| hierarchical | 13/14 | 13/14 | 14/15 |
| period-neighbor + parent-diverse | 13/14 | 13/14 | 13/15 |
| quota hai năm | 14/14 | 14/14 | 15/15 |

Q292 xuất hiện ở vị trí 9 của lane quota hai năm; Q702 được giữ bởi phần
đúng năm của quota. Thước đo “đủ tất cả bảng đích” là cần thiết cho câu có
nhiều operand: chỉ trúng một bảng không đủ để tính một chỉ số dẫn xuất.

Mẫu 14 câu quá nhỏ và là diagnostic từ V2 execution-ready, không phải gold
độc lập. Binding vẫn chưa là semantic authorization. Vì vậy kết quả chỉ cho
thấy một hướng period-routing và catalog coverage đáng giữ để nghiên cứu
tiếp, **không** chứng minh accuracy của mô hình và không được dùng để thay
binding/E2E/submission.
