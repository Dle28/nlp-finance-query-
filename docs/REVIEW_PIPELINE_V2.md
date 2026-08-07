# ViFinQA Review Pipeline V2

## Mục tiêu

Tách hẳn hai loại workload:

- **Kaggle**: build corpus/index, dense retrieval, chạy retrieval Top-K và đóng gói review bundle.
- **Local**: đọc bundle tĩnh, multi-agent review, human review một tập nhỏ để calibration, sau đó máy tự review phần còn lại với provenance rõ ràng.

Không dùng machine review như gold vô điều kiện. Mọi nhãn đều phải ghi rõ `human_verified`, `machine_calibrated` hoặc `machine_provisional`.

---

## Kiến trúc

```text
KAGGLE HEAVY SIDE
raw corpus
  -> corpus gate
  -> table_assets
  -> lexical index
  -> dense index
  -> integrity gate
  -> retrieve Top-K
  -> extract exact table rows/context
  -> one-line faithful table summary + direct evidence row
  -> immutable review bundle

LOCAL REVIEW SIDE
review bundle
  -> checksum/schema gate
  -> independent reviewer agents
       * lexical-rank reviewer
       * dense-rank reviewer
       * metadata reviewer
       * evidence-row reviewer
       * challenger reviewer
       * verifier
  -> consensus
  -> small human seed review
  -> train review calibrator
  -> rerun multi-agent review
  -> machine_calibrated / needs_human
  -> human audits uncertainty only

KAGGLE NEXT ITERATION
verified labels
  -> Recall@K / MRR / failure analysis
  -> retrain / rerank / BGE experiments
```

---

## 0. Nguyên tắc dữ liệu

Một candidate phải có cả hai lớp thông tin:

1. **Raw evidence**: `rows`, `context_before`, metadata, UID.
2. **Review projection**: một dòng tóm tắt trung thực được sinh từ chính bảng + một `direct_evidence` trích nguyên hàng tốt nhất.

Ví dụ UI phải hiện kiểu:

```text
Chủ đề gợi ý từ bảng: Bất động sản đầu tư / Giá trị còn lại.
Dòng gần query: Giá trị còn lại | 154.320.000.000 | 166.110.000.000
```

`direct_evidence` luôn là text ghép trực tiếp từ cell của bảng, không phải LLM viết lại.

---

## 1. Kaggle — build / validate / export

### 1.1 Cài repo

```bash
git pull --ff-only origin main
pip install -e .
```

### 1.2 Chạy pipeline Kaggle chuẩn

Nếu artifacts chưa tồn tại hoặc mismatch:

```bash
python kaggle/run_kaggle_retrieval_export.py \
  --questions data/labels/annotation_questions_60.jsonl \
  --config configs/annotation_baseline.yaml \
  --output-dir /kaggle/working/vifinqa_review_bundle \
  --top-k 20 \
  --build-missing
```

Nếu artifacts đã hợp lệ và chỉ muốn export lại bundle:

```bash
python kaggle/run_kaggle_retrieval_export.py \
  --questions data/labels/annotation_questions_60.jsonl \
  --config configs/annotation_baseline.yaml \
  --output-dir /kaggle/working/vifinqa_review_bundle \
  --top-k 20
```

### 1.3 Hard gates trước khi export

Pipeline phải dừng nếu một trong các điều kiện sau sai:

```text
asset_count >= 100000
asset_count == lexical_count
asset_count == dense_uid_count
asset_count == FAISS.ntotal
Q13 oracle tồn tại trong table_assets
mọi retrieved UID resolve được sang table payload
0 retrieval error (trừ khi chạy explicit --allow-errors)
```

Không được âm thầm rebuild hoặc âm thầm bỏ candidate lỗi.

### 1.4 Bundle output

```text
vifinqa_review_bundle/
  manifest.json
  review_items.jsonl
  tables.jsonl
  errors.jsonl
  SHA256SUMS
  vifinqa_review_bundle.tar.gz
```

`review_items.jsonl` chứa question plan, Top-K metadata, summary một dòng, direct evidence row và evidence features.

`tables.jsonl` chứa raw rows/context theo UID, deduplicate giữa các question.

`manifest.json` khóa version: git commit, config hash, questions hash, asset count, top-k, timestamp.

---

## 2. Local — verify bundle

Copy/download `vifinqa_review_bundle.tar.gz` về local rồi giải nén.

Trước mọi review phải verify checksum:

```bash
sha256sum -c SHA256SUMS
```

Nếu checksum hoặc schema sai: **STOP**.

---

## 3. Local — multi-agent first pass

Chạy:

```bash
python scripts/auto_review_bundle.py \
  --bundle-dir /path/to/vifinqa_review_bundle \
  --output data/labels/machine_reviews_60.jsonl \
  --seed-queue data/labels/human_seed_queue_12.jsonl \
  --seed-size 12
```

Các reviewer độc lập:

- `lexical_agent`: ưu tiên BM25 rank nhưng không được quyết định một mình.
- `dense_agent`: ưu tiên dense rank.
- `metadata_agent`: ticker / year / scope.
- `evidence_agent`: metric overlap + numeric evidence trong row thực tế.
- `challenger_agent`: cố tìm candidate khác tốt hơn candidate đang dẫn đầu.
- `verifier`: kiểm tra candidate consensus có đủ evidence support hay không.

Output mỗi question có:

```text
machine_candidate_uid
agent_votes
agreement
verifier
consensus_status
machine_confidence
```

Các trạng thái:

- `machine_high_confidence`
- `machine_provisional`
- `needs_human`
- `retrieval_failure`

Không tự gắn `NO_GOLD_IN_CORPUS`. Chỉ được gắn `NO_CANDIDATE_IN_TOPK` nếu Top-K không có support tốt.

---

## 4. Human seed review — chỉ review một phần

Mở local widget:

```python
%run local/review_bundle_widget.py \
  --bundle-dir /path/to/vifinqa_review_bundle \
  --machine-reviews data/labels/machine_reviews_60.jsonl \
  --queue data/labels/human_seed_queue_12.jsonl \
  --output data/labels/retriever_verified_60.jsonl
```

Human seed mặc định 12 câu, phân tầng theo family + confidence. Mục tiêu không phải hoàn thành 60 câu mà là tạo calibration examples.

UI phải ưu tiên hiển thị:

1. question;
2. one-line table summary;
3. exact direct evidence row;
4. agent consensus / disagreement;
5. aligned table chỉ mở khi cần.

---

## 5. Train review calibrator từ human seed

Sau khi đã review seed:

```bash
python scripts/train_review_calibrator.py \
  --bundle-dir /path/to/vifinqa_review_bundle \
  --human-labels data/labels/retriever_verified_60.jsonl \
  --output artifacts/review_calibrator.joblib
```

Calibrator học candidate correctness từ feature do các reviewer tạo, không học trực tiếp answer cuối.

Nếu số human-reviewed question quá ít hoặc không có cả positive/negative examples, script phải dừng và yêu cầu thêm seed.

---

## 6. Rerun machine review sau calibration

```bash
python scripts/auto_review_bundle.py \
  --bundle-dir /path/to/vifinqa_review_bundle \
  --calibrator artifacts/review_calibrator.joblib \
  --output data/labels/machine_reviews_60_calibrated.jsonl \
  --needs-human-queue data/labels/needs_human_after_calibration.jsonl
```

Sau bước này:

- high agreement + calibrated probability cao -> `machine_calibrated`;
- disagreement / low margin -> `needs_human`;
- complex multi-table family vẫn giữ provenance và conservative threshold.

Human chỉ review queue `needs_human_after_calibration.jsonl`, không review lại toàn bộ 60.

---

## 7. Label provenance

Không trộn ba loại label:

```text
human_verified
machine_calibrated
machine_provisional
```

Khi train/evaluate có thể:

- dùng `human_verified` weight = 1.0;
- dùng `machine_calibrated` weight thấp hơn hoặc chỉ dùng pseudo-label experiment;
- không coi `machine_provisional` là gold.

---

## 8. Failure controls

### Corpus / index

Mismatch count -> stop.

### Bundle

UID không resolve -> stop.
Checksum sai -> stop.
Question thiếu Top-K do exception -> ghi `errors.jsonl`; mặc định stop.

### Auto-review

Reviewer disagreement -> abstain (`needs_human`).
Verifier unsupported -> abstain.
Không auto-accept `retrieval_failure`.

### Calibrator

Không đủ human seed -> stop.
Không có cả positive và negative candidate -> stop.
Calibration probability không thay thế evidence gate.

---

## 9. Chu kỳ làm việc

```text
Kaggle: build/index/export bundle
        ↓
Local: auto-review baseline
        ↓
Local: human review 12 seed cases
        ↓
Local: train calibrator
        ↓
Local: auto-review calibrated
        ↓
Local: human resolve uncertainty only
        ↓
Git: commit verified labels
        ↓
Kaggle: evaluate + retrain
```

Đây là pipeline mặc định cho vòng review tiếp theo.