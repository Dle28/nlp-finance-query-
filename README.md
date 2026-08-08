# ViFinQA — Retrieval, Evidence Grounding và Human-in-the-loop Review

Repository này xây dựng pipeline QA tài chính tiếng Việt trên ViFinQA theo hướng:

> **retrieve đúng bảng → trích đúng bằng chứng trong bảng → review có kiểm chứng → học từ một phần human label → mới tự động hóa phần còn lại.**

Mục tiêu hiện tại chưa phải sinh answer cuối bằng LLM. Mục tiêu trước mắt là tạo được **retrieval labels đáng tin cậy** để huấn luyện/evaluate retriever, reranker và các bước reasoning phía sau.

---

## 1. Kiến trúc hiện tại

```text
                    KAGGLE / GPU
                        │
                        ▼
                raw financial reports
                        │
                        ▼
                 table_assets.jsonl
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
        lexical index          dense index
          SQLite FTS             FAISS
              └─────────┬─────────┘
                        ▼
                 hybrid retrieval
                        │
                        ▼
                Top-K table candidates
                        │
                        ▼
           V3 evidence projection / repair
              - effective_metric
              - adjacent-table recovery
              - period-aware value row
              - exact direct_evidence
                        │
                        ▼
             vifinqa_review_bundle_v3
                        │
                    DOWNLOAD
                        │
                        ▼
                      LOCAL
                        │
                        ▼
                  diagnostic gate
                        │
                        ▼
             source-aware multi-agent review
                        │
                 human seed ~12 câu
                        │
                        ▼
                 review calibrator
                        │
                        ▼
             rerun machine review toàn bộ
                        │
            ┌───────────┴───────────┐
            ▼                       ▼
     machine_calibrated         needs_human
            │                       │
            └───────────┬───────────┘
                        ▼
                final retrieval labels
```

Chi tiết kiến trúc nằm trong [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 2. Data contract

Public ViFinQA hiện có:

- `1,012` câu hỏi;
- OCR financial reports;
- `code_stock.csv`;
- question records chủ yếu gồm `id` và `question`.

Public question file không cung cấp đầy đủ gold retrieval fields như:

```text
answer
relevant_docs
relevant_tables
evidence
pandas_query
```

Do đó repository này phải tự xây **verified retrieval labels** thay vì giả định public data đã có gold.

---

## 3. Các question family đang dùng

Đây là weak/research labels, không phải official gold labels.

| ID | Family | Count |
|---:|---|---:|
| `1–361` | direct lookup | 361 |
| `362–577` | conditional / analytical | 216 |
| `578–655` | temporal change | 78 |
| `656–732` | ratio / derived | 77 |
| `733–812` | cross-entity comparison | 80 |
| `813–1012` | multi-entity / multi-period aggregation | 200 |

---

# 4. Cài đặt local

```bash
cd ~/Documents/AI_guru

python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e .
```

Pull code mới:

```bash
git pull --ff-only origin main
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

---

# 5. Kaggle: build/index/export

Kaggle chịu phần nặng:

```text
raw reports
→ table assets
→ lexical index
→ dense index
→ hybrid retrieval
→ V3 review bundle
```

Nếu session mới hoàn toàn và chưa có artifacts, dùng notebook/script build-from-scratch.

Nếu artifacts đã tồn tại, export V3 bằng:

```python
%cd /kaggle/working/AI_guru

%run kaggle/export_review_bundle_v3.py \
    --top-k 20 \
    --max-review-candidates 40 \
    --neighbor-radius 1 \
    --force
```

Output:

```text
/kaggle/working/vifinqa_review_bundle_v3.tar.gz
/kaggle/working/vifinqa_review_bundle_v3.tar.gz.sha256
/kaggle/working/vifinqa_review_handoff_v3.json
```

Tải cả 3 file về local.

Xem thêm:

- [`docs/KAGGLE_TO_LOCAL_REVIEW.md`](docs/KAGGLE_TO_LOCAL_REVIEW.md)
- [`docs/REVIEW_FIX_V3.md`](docs/REVIEW_FIX_V3.md)

---

# 6. Local: chuẩn bị bundle

Ví dụ:

```bash
mkdir -p ~/ViFinQA_review/run_002

tar -xzf ~/Downloads/vifinqa_review_bundle_v3.tar.gz \
    -C ~/ViFinQA_review/run_002
```

Thư mục sau extract phải có:

```text
manifest.json
review_items.jsonl
tables.jsonl
errors.jsonl
SHA256SUMS
```

Verify archive trước khi dùng:

```bash
cd ~/Downloads
sha256sum -c vifinqa_review_bundle_v3.tar.gz.sha256
```

---

# 7. Diagnostic trước khi review

Diagnostic không tạo gold label.

```bash
cd ~/Documents/AI_guru

python local/run_local_review_stage.py diagnose \
    --bundle-archive ~/Downloads/vifinqa_review_bundle_v3.tar.gz \
    --diagnostic-output data/diagnostics/run_002 \
    --audit-size 12
```

Output:

```text
data/diagnostics/run_002/
├── diagnostic_summary.json
├── review_bundle_summary.csv
├── review_bundle_diagnostics.jsonl
└── manual_audit_queue.jsonl
```

Các lỗi chính:

```text
ADJACENT_CONTEXT_HIT
RETRIEVAL_RISK
EVIDENCE_RISK
PLANNER_RISK
AMBIGUOUS_TOPK
COMPLEX_FAMILY_REVIEW
```

Không coi `NO_CANDIDATE_IN_TOPK` là `NO_GOLD_IN_CORPUS`.

---

# 8. Machine review baseline

Sau khi diagnostic chấp nhận được:

```bash
cd ~/Documents/AI_guru

python local/run_local_review_stage.py baseline \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --seed-size 12
```

Output:

```text
data/labels/machine_reviews_60.jsonl
data/labels/human_seed_queue_12.jsonl
```

Với bundle schema V3, runner tự dùng reviewer mới:

```text
scripts/auto_review_bundle_v31.py
```

Reviewer này có grounding guard để recovered adjacent table không được thắng chỉ vì context leak.

---

# 9. Human review: lưu ý rất quan trọng

`local/review_bundle_widget.py` là **Jupyter/IPython widget**.

Do đó lệnh:

```python
%run local/review_bundle_widget.py ...
```

**chỉ chạy trong Jupyter Notebook/JupyterLab/IPython notebook cell.**

Không chạy `%run` trực tiếp trong Bash terminal.

Nếu chạy trong terminal như:

```bash
%run local/review_bundle_widget.py
```

Bash sẽ báo:

```text
bash: fg: %run: no such job
```

## Cách đúng

Từ terminal:

```bash
cd ~/Documents/AI_guru
source .venv/bin/activate
jupyter lab
```

Hoặc:

```bash
jupyter notebook
```

Sau đó tạo/open một notebook trong repo và chạy cell:

```python
%run local/review_bundle_widget.py \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --machine-reviews data/labels/machine_reviews_60.jsonl \
    --queue data/labels/human_seed_queue_12.jsonl \
    --output data/labels/retriever_verified_60.jsonl
```

Bạn chỉ review seed queue, không review toàn bộ 60 câu.

UI ưu tiên hiển thị:

```text
Question
Machine recommendation
One-line table summary
Exact direct evidence
Agent votes
Aligned source rows khi cần
```

---

# 10. Calibration

Sau khi review đủ seed:

```bash
python local/run_local_review_stage.py calibrate \
    --bundle-dir ~/ViFinQA_review/run_002
```

Pipeline:

```text
human seed
→ candidate features
→ LogisticRegression calibrator
→ rerun source-aware reviewers
→ calibrated probabilities
```

Output:

```text
artifacts/review_calibrator.joblib
data/labels/machine_reviews_60_calibrated.jsonl
data/labels/needs_human_after_calibration.jsonl
```

Bạn chỉ review `needs_human_after_calibration.jsonl` nếu còn case bất định.

---

# 11. Final labels

```bash
python local/run_local_review_stage.py final \
    --bundle-dir ~/ViFinQA_review/run_002
```

Output:

```text
data/labels/retriever_labels_v2.jsonl
```

Provenance được giữ riêng:

```text
human_verified
machine_calibrated
machine_high_confidence
machine_provisional
needs_human
retrieval_failure
```

Machine label không được giả mạo thành human gold.

---

# 12. V3 evidence projection

V3 không chỉ lấy một row có text gần query.

Nó cố tạo evidence dạng:

```text
TABLE: 10. Bất động sản đầu tư
||
COLUMNS: Nguyên giá | Hao mòn lũy kế | Giá trị còn lại
||
VALUE: Số cuối năm | 417.860.288.970 | 39.303.347.137 | 378.556.941.833
```

Các field chính:

```text
effective_metric
context_heading
table_topic
direct_evidence
anchor_row_index
value_row_index
period_intent
period_match
evidence_features
```

`direct_evidence` phải được tạo từ source table/context, không phải LLM tưởng tượng.

---

# 13. Source-aware multi-agent review

Hiện tại các reviewer là deterministic Python reviewers + optional learned calibrator, không phải nhiều ChatGPT độc lập.

Các view chính:

```text
lexical_agent
 dense_agent
 metadata_agent
 evidence_agent
 challenger_agent
 grounding_agent
 calibrator_agent  # chỉ có sau human calibration
 verifier
```

Consensus chỉ mạnh khi candidate vừa có retrieval support vừa có grounded evidence.

Recovered adjacent candidate phải qua strict grounding guard trước khi được vote.

---

# 14. Repository structure hiện tại

```text
src/finance_query/
├── config.py
├── corpus.py
├── questions.py
├── retrieval.py
├── binding.py
├── execution.py
├── pipeline.py
├── schemas.py
└── cli.py

kaggle/
├── export_review_bundle.py
├── export_review_bundle_v3.py
└── run_kaggle_retrieval_export.py

scripts/
├── build_review_bundle.py
├── build_review_bundle_v3.py
├── auto_review_bundle.py
├── auto_review_bundle_v3.py
├── auto_review_bundle_v31.py
├── train_review_calibrator.py
├── export_review_labels.py
├── train_question_router.py
├── train_dense_retriever.py
└── train_reranker.py

local/
├── diagnose_review_bundle.py
├── review_bundle_widget.py
└── run_local_review_stage.py

docs/
├── KAGGLE_TO_LOCAL_REVIEW.md
├── REVIEW_PIPELINE_V2.md
├── REVIEW_FIX_V3.md
├── LOCAL_DIAGNOSTIC.md
└── V3_BUNDLE_VALIDATION_AND_NEXT.md
```

---

# 15. Current development priority

Thứ tự hiện tại:

```text
1. reliable table retrieval
2. source-grounded evidence projection
3. verified retrieval labels
4. calibrate multi-agent reviewer
5. retriever/reranker evaluation
6. improve table/row/cell binding
7. operand-level reasoning
8. deterministic execution
9. final answer/submission generation
```

Không tối ưu answer generation trước khi retrieval/evidence labels đủ đáng tin.