# V3 bundle validation + bước tiếp theo

Bundle V3 được export từ commit `d04c39d05c748823702f9d85a54799f1b931a336` với:

- schema_version: 3
- 60 review questions
- 1,211 unique table payloads
- Top-K retrieval: 20
- max review candidates: 40
- 42 adjacent candidates recovered
- 146,246 assets / lexical rows / dense UIDs / FAISS vectors
- 0 retrieval errors

## Canary Q13 — PASS

Candidate recovered từ bảng đứng trước có evidence:

```text
TABLE: ... 4. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN
COLUMNS: Số cuối năm | ... | Số đầu năm
VALUE: TỔNG CỘNG | 1.880.612.291.229 | 6.406.079.584.088
```

Boundary recovery hoạt động: bảng đúng được đưa lại vào review candidate set.

## Canary Q53 — evidence PASS, ranking guard cần thêm

Bảng đúng đã có evidence hoàn chỉnh:

```text
TABLE: 10. Bất động sản đầu tư
COLUMNS: Nguyên giá | Hao mòn lũy kế | Giá trị còn lại
VALUE: Số cuối năm | 417.860.288.970 | 39.303.347.137 | 378.556.941.833
```

Tuy nhiên một adjacent-recovered candidate không đúng có thể được V3 projection xếp trên bảng đúng vì nó chứa các token chung như `giá trị` + `bất động sản`.

## Fix V3.1

`scripts/auto_review_bundle_v31.py` dùng reviewer V3 nhưng thêm source-aware grounding guard:

```text
adjacent_previous_due_context
        ↓
compare effective_metric với direct_evidence của CHÍNH candidate
        ↓
required token coverage >= 0.85
AND bigram coverage >= 0.45
        ↓
fail => candidate không được vote
```

Kết quả canary:

- Q13 recovered candidate: PASS guard; metric coverage = 1.0.
- Q53 false recovered candidate: REJECT; thiếu `còn`, `đầu`, `tư`.
- Q53 direct candidate: PASS; metric coverage = 1.0 và evidence chứa `378.556.941.833`.

V3.1 cũng làm verifier family-aware:

- `direct_lookup`: strict source grounding + numeric evidence.
- `temporal_change` / `ratio_or_derived`: single candidate chỉ được coi là PARTIAL vì còn operand khác.
- `cross_entity_comparison` / aggregation / conditional: PARTIAL hoặc UNCERTAIN, không ép toàn bộ sang human review chỉ vì một table chưa chứng minh cả phép toán.

`local/run_local_review_stage.py` tự chọn V3.1 khi `manifest.schema_version >= 3`.

# Chạy local

## 1. Pull code

```bash
cd ~/Documents/AI_guru
git pull --ff-only origin main
python -m pip install -e .
python -m unittest discover -s tests -v
```

## 2. Verify archive

```bash
cd ~/Downloads
sha256sum -c vifinqa_review_bundle_v3.tar.gz.sha256
```

## 3. Extract

```bash
mkdir -p ~/ViFinQA_review/run_002

tar -xzf ~/Downloads/vifinqa_review_bundle_v3.tar.gz \
  -C ~/ViFinQA_review/run_002
```

`manifest.json` phải nằm trực tiếp trong `~/ViFinQA_review/run_002/`.

## 4. Baseline agents

```bash
cd ~/Documents/AI_guru
python local/run_local_review_stage.py baseline \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --seed-size 12
```

V3.1 sẽ tạo:

```text
data/labels/machine_reviews_60.jsonl
data/labels/human_seed_queue_12.jsonl
```

## 5. Human chỉ review seed

Runner sẽ in command `%run local/review_bundle_widget.py ...`.

Review 12 seed cases để tạo:

```text
data/labels/retriever_verified_60.jsonl
```

## 6. Calibration

```bash
python local/run_local_review_stage.py calibrate \
  --bundle-dir ~/ViFinQA_review/run_002
```

Sau đó chỉ review:

```text
data/labels/needs_human_after_calibration.jsonl
```

Không review lại toàn bộ 60 câu.
