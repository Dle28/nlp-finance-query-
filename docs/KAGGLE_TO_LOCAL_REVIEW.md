# Kaggle -> Local Review: hướng dẫn chạy chuẩn

Tài liệu này chỉ trả lời 3 câu hỏi:

1. Trên Kaggle chạy file nào?
2. Output nằm ở đâu và tải file nào?
3. Tải về local xong thì chạy gì tiếp?

---

## 1. File Kaggle duy nhất cần chạy

Dùng:

```text
kaggle/export_review_bundle.py
```

Đây là entrypoint duy nhất dành cho Kaggle review export.

Nó sẽ:

```text
validate artifacts
    ↓
(nếu explicit --build-missing thì mới build phần thiếu)
    ↓
run retrieval Top-K
    ↓
resolve exact table rows/context
    ↓
create one-line table summary + direct evidence row
    ↓
create review bundle
    ↓
copy archive ra /kaggle/working
    ↓
hiện link tải xuống
```

Không review ở Kaggle. Kaggle chỉ tạo dữ liệu để review local.

---

## 2. Trường hợp bình thường: artifacts đã build xong

Trong Kaggle notebook, chạy:

```python
%cd /kaggle/working/AI_guru

!git pull --ff-only origin main

%run kaggle/export_review_bundle.py \
    --top-k 20 \
    --force
```

`--force` chỉ ghi đè review bundle cũ. Nó KHÔNG tự build lại dense index.

Nếu artifacts hiện tại hợp lệ, script chỉ:

```text
validate
→ retrieve
→ export
→ package
→ download link
```

---

## 3. Chỉ khi artifacts thiếu hoặc stale

Khi script báo kiểu:

```text
Artifacts are not ready
```

và bạn thực sự muốn Kaggle chạy phần nặng, mới dùng:

```python
%cd /kaggle/working/AI_guru

%run kaggle/export_review_bundle.py \
    --top-k 20 \
    --build-missing \
    --force
```

`--build-missing` có thể chạy:

```text
build-assets
build-lexical
build-dense
```

Dense là phần tốn GPU.

Không thêm `--build-missing` nếu bạn chỉ muốn xuất bundle.

---

## 4. Các gate lỗi bắt buộc

Script phải dừng nếu:

```text
asset_count < 100000
asset_count != lexical_count
asset_count != dense_uid_count
asset_count != FAISS.ntotal
Q13 oracle bị thiếu
retrieved UID không resolve được sang table
bundle không tạo được archive
```

Nếu fail một gate thì không được mang output đó về local review.

---

## 5. Output nằm ở đâu?

Sau khi thành công, bạn sẽ thấy:

```text
EXPORT SUCCESS

Bundle directory : /kaggle/working/vifinqa_review_export/vifinqa_review_bundle
DOWNLOAD FILE    : /kaggle/working/vifinqa_review_bundle.tar.gz
SHA256 FILE      : /kaggle/working/vifinqa_review_bundle.tar.gz.sha256
HANDOFF INFO     : /kaggle/working/vifinqa_review_handoff.json
```

File quan trọng nhất là:

```text
/kaggle/working/vifinqa_review_bundle.tar.gz
```

Đây là file bạn tải về local.

Nên tải thêm:

```text
/kaggle/working/vifinqa_review_bundle.tar.gz.sha256
```

để kiểm tra file không bị lỗi khi tải.

`vifinqa_review_handoff.json` chỉ là metadata + hướng dẫn bước tiếp theo.

---

## 6. Tải xuống từ Kaggle

Cuối script sẽ cố hiển thị link:

```text
Download bundle: vifinqa_review_bundle.tar.gz
Download SHA256: vifinqa_review_bundle.tar.gz.sha256
Download handoff info: vifinqa_review_handoff.json
```

Bạn click trực tiếp vào `Download bundle`.

Nếu Kaggle không render link, dùng Files/Output sidebar và tìm:

```text
/kaggle/working/vifinqa_review_bundle.tar.gz
```

rồi bấm Download.

---

## 7. Bên trong review bundle có gì?

Sau khi giải nén sẽ có các file chính:

```text
manifest.json
review_items.jsonl
tables.jsonl
errors.jsonl
SHA256SUMS
```

### `review_items.jsonl`

Mỗi question chứa:

```text
question
question_plan
Top-K candidates
rank / lexical rank / dense rank / RRF
table_topic
one_line_summary
direct_evidence
best_row_index
evidence_features
metadata match
```

Ví dụ:

```text
Chủ đề gợi ý từ bảng: Bất động sản đầu tư / Giá trị còn lại.
Dòng gần query: Giá trị còn lại | 154.320.000.000 | 166.110.000.000
```

`direct_evidence` được ghép trực tiếp từ cell thật của bảng, không phải LLM tự viết lại.

### `tables.jsonl`

Chứa raw table payload theo UID để local UI có thể mở bảng đầy đủ khi cần.

### `manifest.json`

Khóa version:

```text
git commit
config/questions hash
asset count
top-k
timestamp
```

---

# PHẦN LOCAL

## 8. Copy file về local

Ví dụ file tải về nằm ở:

```text
~/Downloads/vifinqa_review_bundle.tar.gz
```

Tạo folder riêng:

```bash
mkdir -p ~/ViFinQA_review/run_001
```

Giải nén:

```bash
tar -xzf ~/Downloads/vifinqa_review_bundle.tar.gz \
    -C ~/ViFinQA_review/run_001
```

---

## 9. Verify file trước khi review

Nếu bạn tải cả file `.sha256`:

```bash
cd ~/Downloads
sha256sum -c vifinqa_review_bundle.tar.gz.sha256
```

Phải ra:

```text
vifinqa_review_bundle.tar.gz: OK
```

Nếu không phải `OK`: dừng, tải lại.

Sau khi extract, có thể kiểm tra checksum bên trong bundle:

```bash
cd ~/ViFinQA_review/run_001
sha256sum -c SHA256SUMS
```

Nếu fail: không review bundle đó.

---

## 10. Pull code local mới nhất

Trong repo local:

```bash
cd ~/Documents/AI_guru

git pull --ff-only origin main

python -m pip install -e .
```

Nếu repo local ở path khác thì thay path tương ứng.

---

## 11. Bước local đầu tiên: machine review baseline

Chạy:

```bash
python local/run_local_review_stage.py baseline \
    --bundle-dir ~/ViFinQA_review/run_001 \
    --seed-size 12
```

Máy sẽ review toàn bộ bundle trước.

Output chính:

```text
data/labels/machine_reviews_60.jsonl
data/labels/human_seed_queue_12.jsonl
```

Bạn KHÔNG review 60 câu.

Bạn chỉ review queue seed nhỏ trước.

---

## 12. Human seed review

Script baseline sẽ in command mở widget.

Nếu muốn chạy trực tiếp trong Jupyter local:

```python
%run local/review_bundle_widget.py \
    --bundle-dir ~/ViFinQA_review/run_001 \
    --machine-reviews data/labels/machine_reviews_60.jsonl \
    --queue data/labels/human_seed_queue_12.jsonl \
    --output data/labels/retriever_verified_60.jsonl
```

UI ưu tiên hiển thị:

```text
Question
Machine consensus
One-line table summary
Direct evidence row
Candidate đối thủ nếu reviewer bất đồng
Full aligned table chỉ khi bạn mở
```

Mục tiêu của 12 câu này là dạy/calibrate hệ thống review, không phải hoàn thành dataset.

---

## 13. Sau khi review seed: train calibrator

Chạy:

```bash
python local/run_local_review_stage.py calibrate \
    --bundle-dir ~/ViFinQA_review/run_001
```

Output:

```text
artifacts/review_calibrator.joblib
data/labels/machine_reviews_60_calibrated.jsonl
data/labels/needs_human_after_calibration.jsonl
```

Sau bước này, phần machine review còn lại được rerun bằng calibrator học từ seed human của bạn.

---

## 14. Bạn review gì tiếp?

Không review lại toàn bộ 60.

Chỉ review:

```text
data/labels/needs_human_after_calibration.jsonl
```

Đây là các case:

```text
agent disagreement
low confidence
verifier unsupported/uncertain
complex multi-table evidence
```

Các case máy đủ chắc sẽ giữ provenance là:

```text
machine_calibrated
```

không giả thành human gold.

---

## 15. Xuất label cuối

Sau khi xử lý uncertainty:

```bash
python local/run_local_review_stage.py final \
    --bundle-dir ~/ViFinQA_review/run_001
```

Output cuối:

```text
data/labels/retriever_labels_v2.jsonl
```

Label giữ nguồn rõ:

```text
human_verified
machine_calibrated
machine_provisional
```

`machine_provisional` mặc định không được coi là gold.

---

# Tóm tắt cực ngắn

## Kaggle

Chạy một file:

```python
%run kaggle/export_review_bundle.py --top-k 20 --force
```

Tải:

```text
/kaggle/working/vifinqa_review_bundle.tar.gz
```

## Local

```bash
python local/run_local_review_stage.py baseline --bundle-dir <bundle> --seed-size 12
```

Review seed nhỏ.

Sau đó:

```bash
python local/run_local_review_stage.py calibrate --bundle-dir <bundle>
```

Review chỉ `needs_human`.

Cuối cùng:

```bash
python local/run_local_review_stage.py final --bundle-dir <bundle>
```

Kết quả:

```text
data/labels/retriever_labels_v2.jsonl
```

---

## Quy tắc quan trọng

```text
Kaggle = build/retrieve/export
Local  = machine review + human seed + calibration
```

Không review trực tiếp trên live Kaggle index nữa.
