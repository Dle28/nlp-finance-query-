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
       raw-HTML table-structure sidecar V2
       - preserve empty cells / expand spans
       - table function/purpose + context trace
       - accounting-side and formula-operand gates
                        │
                        ▼
                  diagnostic gate
                        │
                        ▼
             source-aware multi-agent review
                        │
               Codex-assisted review
                        │
                 human spot-check ~6 câu
                        │
                 Codex review lại
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
Sửa grid review cục bộ và UI an toàn được mô tả tại
[`docs/TABLE_STRUCTURE_V2.md`](docs/TABLE_STRUCTURE_V2.md); bước này không
rebuild Kaggle, lexical index hay dense index.
Formula-aware review và contract EvidenceSet nằm tại
[`docs/FORMULA_EVIDENCE_SETS.md`](docs/FORMULA_EVIDENCE_SETS.md).
Tóm tắt thay đổi và kết quả validation nằm tại
[`docs/CONTEXT_FORMULA_REVIEW_IMPLEMENTATION.md`](docs/CONTEXT_FORMULA_REVIEW_IMPLEMENTATION.md).
Canary lọc→xếp hạng→truy xuất Q369 và phép tính grounded nằm tại
[`docs/Q369_GROUNDED_REVIEW.md`](docs/Q369_GROUNDED_REVIEW.md).
Pilot xếp lại candidate Top-K, provenance policy và evaluation boundary nằm tại
[`docs/PILOT_CANDIDATE_RERANKER.md`](docs/PILOT_CANDIDATE_RERANKER.md).
Luồng tự động khi không có human reviewer—raw-table canonicalization, agent
cross-check và machine-silver training gate—nằm tại
[`docs/AUTONOMOUS_RAW_REVIEW.md`](docs/AUTONOMOUS_RAW_REVIEW.md).

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

Cài UI review local:

```bash
python -m pip install jupyterlab ipywidgets
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

### 6.1 Khôi phục cấu trúc bảng cho local review

Trước khi xác minh số liệu bằng widget, dựng sidecar V2 từ raw HTML local:

```bash
python local/run_local_review_stage.py repair-tables \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --repair-force
```

Lệnh tạo `tables_structured_v2.jsonl` và manifest ngay trong bundle local.
Archive V3, Kaggle corpus và dense/lexical index không bị sửa. Widget tự nhận
sidecar này; `--repair-force` chỉ thay sidecar cũ nếu đã có, không rebuild
index. Xem chi tiết tại [`docs/TABLE_STRUCTURE_V2.md`](docs/TABLE_STRUCTURE_V2.md).

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

# 9. Collaborative review: Codex đề xuất, human xác nhận

`local/review_bundle_widget.py` dùng `ipywidgets`, vì vậy UI phải chạy trong **Jupyter Notebook/JupyterLab**.

`%run` là **IPython/Jupyter magic command**, không phải Bash command.

Do đó lệnh này sai nếu gõ trực tiếp trong terminal:

```bash
%run local/review_bundle_widget.py ...
```

Bash sẽ báo:

```text
bash: fg: %run: no such job
```

## 9.1 Terminal: chỉ launch Jupyter

```bash
cd ~/Documents/AI_guru
source .venv/bin/activate

python -m pip install jupyterlab ipywidgets
jupyter lab
```

Nếu không muốn JupyterLab:

```bash
jupyter notebook
```

## 9.2 Tạo ledger và human check queue

Codex review không được ghi thành `human_verified`. Mỗi recommendation phải giữ candidate UID, rank, exact source rows, completeness, confidence và reason codes.

```bash
python local/run_local_review_stage.py collaborate \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --assistant-labels data/labels/codex_assisted_reviews.jsonl \
    --human-check-size 6
```

Output:

```text
data/labels/review_ledger_60.jsonl
data/labels/human_check_queue.jsonl
```

Ledger luôn giữ đủ 60 câu; queue chỉ chứa spot-check của vòng hiện tại.

## 9.3 Trong một Jupyter code cell: chạy widget

```python
%cd ~/Documents/AI_guru

%run local/review_bundle_widget.py \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --machine-reviews data/labels/machine_reviews_60.jsonl \
    --assistant-reviews data/labels/codex_assisted_reviews.jsonl \
    --queue data/labels/human_check_queue.jsonl \
    --output data/labels/retriever_verified_60.jsonl
```

Bạn có thể dừng sau vài câu. Output được ghi atomically; Codex đọc các xác nhận/bất đồng, review lại phần còn lại và tạo queue vòng sau.

UI ưu tiên hiển thị:

```text
Question
Machine/Codex recommendation đã rút gọn
Chức năng bảng và phần kế toán
Chức năng sử dụng nhanh + tiêu đề mục nguồn gần nhất
Mức phù hợp hoặc cảnh báo mismatch
Preview tối đa 4 exact rows; full grid chỉ mở khi cần
Formula + operand coverage cho câu hệ số/derived
One-line summary grounded
```

Nút `Accept Codex` là thao tác xác nhận của human. Chỉ sau thao tác này record mới thành `human_verified`; recommendation Codex ban đầu vẫn được giữ trong ledger để audit.

Với evidence set `partial`, nút đổi thành `Confirm partial` và lưu `human_verified_partial`. Trạng thái này xác nhận các bảng đã chọn là relevant nhưng không biến toàn câu thành training gold.

Khi grid V2 đã xác minh, calibrator có thể dùng các bảng được chọn từ partial
review như **positive-only** candidate evidence; các candidate không được chọn
vẫn là unknown, tuyệt đối không bị tạo thành negative label. Partial vẫn không
được export vào final retrieval training labels.

Với câu ratio/temporal/derived, `Save EvidenceSet` lưu riêng từng operand với
candidate UID, rank, exact source rows và column labels. Multi-table evidence
được phép và thường là bắt buộc. Nếu công thức thuộc loại `ambiguous` hoặc
`review_required`, checkbox xác nhận công thức của human là gate độc lập;
không có xác nhận thì không nâng thành complete.

Nếu chưa có grid V2 đã xác minh từ raw HTML, quick numeric view và
`Accept machine` bị khóa; lựa chọn positive mới chỉ có thể lưu partial.

## 9.4 Autonomous raw-source review (không cần human reviewer)

Khi không có người review, không dùng `Accept Codex`, không nâng machine label
thành `human_verified`, và không train từ `machine_provisional`. Thay vào đó:

```bash
python local/run_local_review_stage.py preprocess \
    --bundle-dir ~/ViFinQA_review/run_002

python local/run_local_review_stage.py autonomous \
    --bundle-dir ~/ViFinQA_review/run_002
```

`preprocess` tạo sidecar riêng từ V2 raw-HTML grid: khôi phục path tiêu đề
cha–con theo span provenance, bind period/unit vào cột nguồn và quarantine
bảng không đủ dữ kiện. `autonomous` cho retrieval/semantic/evidence/metadata/
challenger/critic views review chéo. Chỉ direct lookup qua toàn bộ source gate
mới là `machine_calibrated` silver; phần còn lại vẫn là `machine_provisional`
hoặc `needs_human` và bị loại khỏi train.

Với cụm từ giống phép tính nhưng thực tế là một **dòng đã được báo cáo sẵn**
(`Tổng cộng tài sản`, `Tỷ lệ sở hữu`, `Lỗ chênh lệch tỷ giá`), không sửa
`review_items.jsonl` gốc. Tạo sidecar hash-bound rồi dùng nó khi autonomous
review:

```bash
python scripts/build_question_plan_overrides.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --output ~/ViFinQA_review/run_002/question_plan_overrides_v1.jsonl

python local/run_local_review_stage.py autonomous \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --question-plan-overrides ~/ViFinQA_review/run_002/question_plan_overrides_v1.jsonl
```

Chỉ dạng một chủ thể/một kỳ, không có so sánh/tổng hợp/range, mới được
replan. Override mang hash câu hỏi + plan gốc và được ghi lại trong review;
nó không thay đổi raw table, không giả mạo `human_verified` và vẫn phải qua
exact-row/exact-column gate.

Với câu ratio/temporal có controlled formula, có thể tạo `EvidenceSet` exact
row riêng để biết operand nào đã đủ hoặc còn thiếu; lệnh này không tính answer
và không thay đổi label:

```bash
python scripts/build_formula_evidence_sets.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --output ~/ViFinQA_review/run_002/formula_evidence_sets_v1.jsonl
```

`complete` chỉ có nghĩa tất cả operand của formula đã có exact raw row/cell,
cùng entity/scope và không mơ hồ; câu có lọc nhóm, xếp hạng hoặc công thức
ambiguous vẫn giữ `partial` cho đến khi có executor theo stage.

Sau khi tích luỹ đủ 200 machine-silver pairs, chạy:

```bash
python local/run_local_review_stage.py autotrain \
    --bundle-dir ~/ViFinQA_review/run_002
```

Nếu chưa đủ, lệnh trả `deferred` và không ghi model. Không có bước nào rebuild
corpus, lexical index, dense embeddings hay FAISS. Chi tiết safety contract tại
[`docs/AUTONOMOUS_RAW_REVIEW.md`](docs/AUTONOMOUS_RAW_REVIEW.md).

Câu lọc/xếp hạng nhiều giai đoạn không thể bị hiểu nhầm thành một công thức đơn
lẻ theo keyword đầu tiên. Q369 có plan ba stage và EvidenceSet riêng theo
entity; các dạng chưa có controlled rule vẫn được gắn
`multi_stage_selection_unresolved`.

Positive labels đã tạo bằng UI cũ vẫn nằm nguyên trong audit file nhưng được
đưa lại vào review queue và bị loại khỏi calibration/training export cho đến
khi human xác minh lại trên grid V2.

Ở lần baseline/rerun kế tiếp, V3.1 reviewer cũng chỉ vote trên candidate có
`VALUE/ANCHOR` khớp đúng exact V2 row. Machine label không có gate này không
được final training export.

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

Resolution queue phải bao gồm cả `needs_human`, `retrieval_failure`, `machine_provisional` chưa đủ điều kiện training và complex evidence set còn thiếu operand. Provisional vẫn được giữ trong ledger ngay cả khi không được export để train.

---

# 11. Final labels

```bash
python local/run_local_review_stage.py final \
    --bundle-dir ~/ViFinQA_review/run_002
```

Output:

```text
data/labels/review_ledger_60.jsonl
data/labels/retriever_labels_v2.jsonl
```

`review_ledger_60.jsonl` là audit/provenance đầy đủ. `retriever_labels_v2.jsonl` chỉ là training subset đã qua eligibility gate.

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

## 11.1 Pilot reranker sau final labels (shadow-only)

Sau khi có final labels, có thể chạy pilot nhỏ mà không rebuild corpus hay
dense/FAISS index:

```bash
python local/run_local_review_stage.py pilot \
    --bundle-dir ~/ViFinQA_review/run_002
```

Pilot chỉ re-rank những table đã nằm trong `review_items.jsonl` Top-K. Complete
`human_verified` labels tạo positive và negative; `machine_calibrated` chỉ là
positive pseudo-label có weight, không suy diễn candidate không được chọn là
negative. Output là artifact, metadata có grouped OOF MRR/recall@K và shadow
ranking để audit; script không tự động thay đổi retriever/index. Xem contract
đầy đủ tại [`docs/PILOT_CANDIDATE_RERANKER.md`](docs/PILOT_CANDIDATE_RERANKER.md).

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
├── table_structure.py
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
├── repair_review_bundle_tables.py
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
├── V3_BUNDLE_VALIDATION_AND_NEXT.md
├── TABLE_STRUCTURE_V2.md
├── FORMULA_EVIDENCE_SETS.md
├── CONTEXT_FORMULA_REVIEW_IMPLEMENTATION.md
└── Q369_GROUNDED_REVIEW.md
```

---

# 15. Current development priority

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
