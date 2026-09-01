# Nghiên cứu cải thiện ViFinQA RAG + fine-tune (v2)

Ngày 28/08/2026

## Kết luận ngắn

Nút thắt lớn nhất hiện không nằm ở việc chọn một LLM lớn hơn. Hệ thống đang
chọn ô từ một shortlist hẹp, đồng thời có một số bảng đặt đơn vị ở dòng tiêu
đề nằm ngoài cửa sổ bằng chứng. Vì vậy mô hình có thể tìm đúng tài liệu nhưng
chọn sai bảng/ô hoặc đổi sai triệu–tỷ.

Hai thay đổi đã được triển khai trong
`scripts/e2e/build_competition_submission_v1.py`:

1. Hydrate header và các dòng đầu của bảng từ `tables_structured_v2` trước khi
   chọn cột/kỳ/đơn vị. Giá trị số không được lấy thêm từ dense index.
2. Cho phép hợp nhất shortlist cũ với full-corpus dense index đã kiểm hash.
   Dense chỉ mở rộng điều hướng; bảng/ô vẫn phải được hydrate từ dữ liệu cấu
   trúc và replay như trước.

## Bằng chứng chẩn đoán

- Full dense index có 146.246 bảng; shortlist review cũ thường có 40 bảng.
- Trên 65 candidate nguồn độc lập, dense top-50 giữ được 58/65 bảng đích
  (89,2%). Dense top-10 chỉ giữ 33/65 (50,8%). Đây là bằng chứng rằng mở rộng
  candidate có khả năng sửa lỗi recall, nhưng chưa phải accuracy leaderboard.
- Với 46 candidate có đúng một nguồn và tọa độ rõ ràng, phép tính từ bảng
  nguồn tăng từ 38/46 (82,6%) lên 43/46 (93,5%) sau khi áp dụng header-unit,
  metric-core và dense expansion.
- Cảnh báo Dashboard `gold=506 pred=1012` vẫn là lệch tập chấm; không dùng nó
  để kết luận model tốt/xấu.

## Các lỗi đã sửa

### Đơn vị nằm ngoài cửa sổ candidate

Ví dụ bảng FTS ghi `(Triệu đồng)` ở dòng tiêu đề, còn `evidence_window` bắt đầu
ở dòng `Lợi nhuận trước thuế`. Trước đây hệ thống coi số 444.918 là VND và
trả `0.000444918` tỷ đồng; sau khi đọc header cấu trúc, kết quả là `444.918`
tỷ đồng.

`requested_divisor` cũng phân biệt `tỷ`, `trăm tỷ` và `nghìn tỷ` theo đúng đơn
vị câu hỏi.

### Entity làm lấn át tên chỉ tiêu

Metric trong question plan thường nối thêm tên công ty/ngân hàng. So khớp token
đơn giản có thể chọn một dòng dài chứa tên tổ chức thay vì dòng tài chính cần
tìm. Bộ chấm điểm mới tách phần metric cốt lõi trước tên entity và ưu tiên
cụm từ liên tiếp của chỉ tiêu.

### Candidate recall bị khóa ở shortlist cũ

`expand_review_items_with_dense` truy vấn dense theo ticker–năm–scope, hydrate
những UID có trong bảng cấu trúc, rồi hợp nhất bằng rank tốt nhất. Các candidate
không có bảng cấu trúc bị loại, nên dense không thể tự tạo nguồn hay đáp án.

## Cách chạy thử nghiệm cục bộ

```bash
PYTHONPATH=src .venv/bin/python scripts/e2e/build_competition_submission_v1.py \
  --questions data/ViFinQA/questions/questions.jsonl \
  --bundle artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle \
  --replay artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/grounded_execution_replay_v2.jsonl \
  --source-line-map artifacts/kaggle_upload/vifinqa_submission_runtime_v1/source_line_map.json \
  --dense-index-dir artifacts/kaggle_runs/vifinqa_full_dense_index_gpu_v1_20260826/vifinqa_full_dense_v1 \
  --dense-candidate-limit 50 \
  --output /tmp/vifinqa_dense_expanded_v3
```

Artifact thử nghiệm v3 đã replay đủ 1.012/1.012 query và validator pass; đây là
artifact nghiên cứu, chưa được coi là điểm leaderboard vì chưa chạy scorer cùng
một gold set 1.012 câu.

## Việc cần chạy trên Kaggle

1. Chuyển `FAST_DEV_RUN=False`; smoke run hiện chỉ dùng một phần nhỏ curriculum
   và đặt `promotion_allowed=false`.
2. Attach full dense index (hoặc một index 29.509 bảng đã lọc đúng hash) cùng
   review bundle.
3. Gọi builder với dense expansion và `--model-candidate-limit 40–50` để
   cross-encoder thấy candidate mới; không cắt còn 12 trước rerank.
4. Ghi riêng baseline và candidate trên cùng 506 hoặc 1.012 câu gold. Không
   trộn hai cardinality trong một bảng so sánh.

Chỉ promote model khi replay nguồn, unit/period/scope và metric held-out đều
   đạt; dense và reranker vẫn là navigation-only, không có quyền tự ghi số.

## Bản triển khai V18 đã chuẩn bị và chạy Kaggle

- Notebook full GPU: `artifacts/kaggle_upload/vifinqa_rag_finetune_gpu_v1_v18/vifinqa-rag-finetune-gpu-v1-20260828-v18.ipynb`.
- Metadata kernel: `artifacts/kaggle_upload/vifinqa_rag_finetune_gpu_v1_v18/kernel-metadata.json`.
- Runtime builder mới + dense module: `artifacts/kaggle_upload/vifinqa_rag_finetune_gpu_v1_v18/vifinqa_submission_runtime_v3/`.
- Dense input có thể attach từ `artifacts/kaggle_upload/vifinqa_full_dense_index_v1/`; notebook tự phát hiện `dense_embeddings_v1.npy` và chỉ dùng index cho navigation.

Kaggle API đã upload dense dataset và khởi chạy kernel
`dungle2810/vifinqa-rag-finetune-gpu-v1-20260828-v18-full` (version 1). Tại thời
điểm cập nhật, kernel còn `RUNNING`; chưa được gọi là submission cho đến khi
`metrics.json`, `build_report.json` và `zip_path` xuất hiện và được kiểm tra.
