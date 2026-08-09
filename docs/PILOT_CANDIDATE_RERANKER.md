# Pilot candidate reranker (shadow-only)

Pilot này học cách xếp lại **các candidate đã có trong Top-K của immutable
review bundle**. Nó không phải dense-retriever training và không tìm thêm bảng
ngoài candidate set hiện tại.

## Data boundary

Input bắt buộc:

```text
data/labels/retriever_labels_v2.jsonl  final labels đã qua export gate
data/labels/review_ledger_60.jsonl     audit/provenance đầy đủ
<bundle>/review_items.jsonl            immutable Top-K candidates
```

Chỉ hai nguồn được phép vào pilot:

| Nguồn | Positive | Negative | Trọng số |
|---|---|---|---:|
| `human_verified` có V2 complete | các bảng được chọn | candidate Top-K không được chọn | `1.0` |
| `machine_calibrated` / `machine_high_confidence` có exact V2 row | bảng được chọn | **unknown, loại khỏi negative set** | weight trong final label |

Do đó `machine_provisional`, `needs_human`, `retrieval_failure`,
`human_verified_partial`, và `verified_no_candidate` không thể lọt vào train
set. Machine label cũng không bao giờ được đổi tên thành human gold.

## Chạy

```bash
python local/run_local_review_stage.py pilot \
  --bundle-dir ~/ViFinQA_review/run_002
```

Hoặc chạy trực tiếp:

```bash
python scripts/train_pilot_candidate_reranker.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --labels data/labels/retriever_labels_v2.jsonl \
  --ledger data/labels/review_ledger_60.jsonl \
  --output artifacts/pilot_candidate_reranker.joblib
```

Output:

```text
artifacts/pilot_candidate_reranker.joblib
artifacts/pilot_candidate_reranker.joblib.json
artifacts/pilot_candidate_reranker.joblib.shadow.jsonl
```

File metadata ghi hash input, policy provenance, grouped folds và metric. File
`shadow.jsonl` chỉ là ranking để audit: giữ cả `original_rank`,
`shadow_rank`, `reranker_probability`; nó không sửa bundle hoặc UI.

## Evaluation boundary

Metric là MRR, recall@K, hit-rate@K với GroupKFold theo question ID. Vì split
theo question, candidate của cùng một câu không thể đồng thời nằm ở train và
test. Report tách:

```text
human_verified_only  <- thước đo đáng tin chính
all_final_labels     <- có machine pseudo-positive, chỉ tham khảo
```

Metric này chỉ đo khả năng xếp lại candidate **đã vào Top-K**. Nó không phải
full-corpus retrieval recall và không chứng minh dense index tốt hơn.

Artifact mặc định là `hold` cho đến khi có ít nhất 30 question nhóm
`human_verified`, OOF human-only MRR và recall@1/@3/@5 không giảm so với immutable rank, và một
holdout audit riêng xác nhận kết quả. Script không tự động promotion hay thay
đổi corpus/index trong bất kỳ trường hợp nào.
