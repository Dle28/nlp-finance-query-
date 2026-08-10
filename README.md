# ViFinQA — grounded financial QA

ViFinQA là pipeline hỏi đáp tài chính tiếng Việt theo nguyên tắc:

```text
ML tìm ứng viên
→ rule chứng minh exact source
→ provenance quyết định eligibility
→ executor mới tính toán
```

Hệ thống không lấy bảng gần nhất rồi đoán đáp án. Một giá trị chỉ hợp lệ khi
truy ngược được đúng công ty, năm, scope, bảng, dòng, cột, ô và đơn vị.

Tài liệu kiến trúc chuẩn: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Trạng thái hiện tại

| Thành phần | Trạng thái |
| --- | --- |
| Kaggle corpus, FTS, FAISS và Review Bundle V3 | Hoàn thành cho snapshot hiện tại |
| Bảng nguồn tái dựng V2 và ngữ cảnh evidence V3 | Hoàn thành |
| OCR Quality Profile và Semantic Catalog | Hoàn thành, metadata-only |
| Direct/Formula EvidenceSet | Đã vận hành; formula coverage còn thấp |
| Reviewer V4 và provenance ledger | Đã vận hành |
| Evaluation dashboard | Hoàn thành |
| Query fingerprint census | Hoàn thành: 1.012 câu, 185 fingerprint |
| Direct exact-source replay | Hoàn thành shadow: 63 ready, 49 ambiguous, 246 blocked |
| Training production | Chưa mở: 58 replay-gated silver, gate là 200 |
| Generic operator execution | Grounded registry hoàn thành shadow; multi-stage chưa production |

Snapshot review hiện tại:

```text
1.012 questions
machine_calibrated     62
machine_provisional   364
needs_human           586
```

`needs_human` ở đây là trạng thái abstain/quarantine; hệ thống không bắt buộc
con người phải review toàn bộ các câu đó.

## Cài đặt local

```bash
cd ~/Documents/AI_guru
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Chạy regression:

```bash
python -m unittest discover -s tests -v
```

## Kiến trúc ngắn

```text
1. Raw reports → Corpus / TableAsset
2. Question → QuestionPlan / QueryProgram
3. FTS + dense → candidate Top-K
4. Raw table → V2 Structure → V3 Context
5. Planner + V2/V3 → exact EvidenceSet
6. EvidenceSet → reviewer / critic / provenance
7. Provenance → evaluation / training gate
8. Exact bindings → Decimal executor / submission
```

Chi tiết input, output, quyền hạn và trạng thái từng mô-đun nằm trong
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Artifact contract

Các artifact chính:

```text
Review Bundle V3
  ├─ tables.jsonl
  ├─ review_items.jsonl
  └─ manifest.json

Local derived sidecars
  ├─ tables_structured_v2.jsonl
  ├─ tables_evidence_context_v3.jsonl
  ├─ report_segments_v1.jsonl
  ├─ report_entity_aliases_v1.jsonl
  ├─ ocr_quality_profiles_v1.jsonl
  ├─ semantic_catalog_v1.jsonl
  ├─ direct_evidence_sets_*.jsonl
  └─ formula_evidence_sets_*.jsonl

Evaluation
  ├─ machine_reviews_*.jsonl
  ├─ query_fingerprint_census_v1.jsonl
  ├─ direct_evidence_replay_v1.jsonl
  ├─ machine_silver_labels_replay_gated_v1.jsonl
  ├─ query_program_shadow_v1.jsonl
  └─ grounding_health_dashboard_v1.json
```

ArtifactRegistry bind logical name với schema, SHA và dependency SHA. Không
chọn artifact chỉ dựa trên tên file `v2`, `v3`, `v31` hoặc `v4`.

Validate registry hiện tại:

```bash
python scripts/build_artifact_registry.py \
  --workspace-root ~/ViFinQA_review \
  --validate-only
```

Materialize census/replay mà không rebuild retrieval:

```bash
python scripts/build_query_fingerprint_census.py \
  --bundle-dir "$BUNDLE" \
  --formula-evidence "$BUNDLE/formula_evidence_sets_context_v3_entity_titles_v1.jsonl" \
  --output "$EVAL/query_fingerprint_census_v1.jsonl"

python scripts/build_direct_evidence_replay.py \
  --bundle-dir "$BUNDLE" \
  --machine-reviews "$EVAL/machine_reviews_1012_hierarchy_v3.jsonl" \
  --output "$EVAL/direct_evidence_replay_v1.jsonl"

python scripts/export_review_labels.py \
  --machine-reviews "$EVAL/machine_reviews_1012_hierarchy_v3.jsonl" \
  --direct-replay "$EVAL/direct_evidence_replay_v1.jsonl" \
  --output "$EVAL/machine_silver_labels_replay_gated_v1.jsonl"
```

`BUNDLE` và `EVAL` là biến shell do người chạy đặt; script không đọc số từ
summary và không thay đổi status trong machine review.

## Kaggle và GPU

Kaggle chịu workload nặng:

```text
raw corpus
→ TableAsset
→ lexical index
→ dense index
→ hybrid retrieval
→ immutable Review Bundle V3
```

Không rebuild Kaggle/FTS/FAISS/dense khi chỉ thay:

- UI review;
- V2/V3 sidecar;
- evidence rule;
- provenance gate;
- QueryProgram/executor;
- evaluation dashboard.

GPU phù hợp cho embedding, semantic planner proposal, reranker và training sau
khi đủ labels. GPU không thay exact row/cell/scope/unit validation.

Hướng dẫn vận hành Kaggle: [`docs/KAGGLE_TO_LOCAL_REVIEW.md`](docs/KAGGLE_TO_LOCAL_REVIEW.md).

## Local preprocessing

Với một bundle đã giải nén:

```bash
python local/run_local_review_stage.py repair-tables \
  --bundle-dir ~/ViFinQA_review/run_002

python local/run_local_review_stage.py preprocess \
  --bundle-dir ~/ViFinQA_review/run_002

python local/run_local_review_stage.py formula-evidence \
  --bundle-dir ~/ViFinQA_review/run_002
```

Các bước này không sửa immutable bundle hoặc rebuild index.

## Autonomous review

Chạy source-grounded reviewer:

```bash
python local/run_local_review_stage.py autonomous \
  --bundle-dir ~/ViFinQA_review/run_002
```

Reviewer chỉ tạo `machine_calibrated` khi exact-source gates hoàn tất. Các câu
khác giữ `machine_provisional` hoặc `needs_human`.

Machine silver còn phải vượt Direct Evidence Replay độc lập. Snapshot hiện tại
có 62 status `machine_calibrated`, nhưng chỉ 58 record đi vào training input;
provenance lịch sử không bị đổi, bốn record không vượt replay bị consumer chặn.

Không được đổi:

```text
machine_provisional → machine_calibrated
needs_human         → machine_calibrated
```

chỉ vì model confidence cao hoặc nhiều reviewer đồng ý.

Human/Jupyter widget vẫn được giữ làm công cụ audit tùy chọn, không còn là
điều kiện để hệ thống chạy hết corpus. `%run` chỉ dùng trong Jupyter, không dùng
trong Bash.

## Formula và QueryProgram

Formula EvidenceSet tách câu hỏi thành các operand, mỗi operand phải bind vào
exact raw V2 cell. QueryProgram chỉ được chạy khi:

```text
formula defined
operand coverage complete
selected binding exact
entity/year/scope coherent
unit compatible
operator allow-listed
```

Hai canary hiện tại:

- Q369 compile nhưng blocked vì không có coherent bindings/global scope;
- Q551 shadow-complete từ 15 exact cells, nhưng không đủ điều kiện submission
  hoặc provenance promotion.

Xem [`docs/QUERY_PROGRAM.md`](docs/QUERY_PROGRAM.md) và
[`docs/COMPLEX_QUERY_CANARY.md`](docs/COMPLEX_QUERY_CANARY.md).

## Training gate

Training input hợp lệ:

```text
human_verified      → được dùng, trọng số cao
machine_calibrated  → được dùng sau gate
machine_provisional → audit only
needs_human         → quarantine
```

Autotrain chỉ chạy khi đủ số pair đã grounded:

```bash
python local/run_local_review_stage.py autotrain \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --autonomous-min-pairs 200
```

Nếu chưa đủ gate, command phải dừng; không được hạ gate để ép training.

## Không review thủ công 1.012 câu

Hướng hiện tại là nhóm câu theo fingerprint:

```text
operator DAG
operand roles
entity/year cardinality
scope policy
table functions
unit contract
```

Những câu cùng fingerprint dùng chung contract và executor. Tất cả 1.012 câu
vẫn qua census tự động; chỉ một mẫu phân tầng của Green pool cần audit độc lập.
Unknown hoặc ambiguous fingerprint tự abstain.

Kế hoạch MVP 12 giờ và Definition of Done được mô tả trong
[`ARCHITECTURE.md`](ARCHITECTURE.md). Checklist issue và gate thực thi nằm tại
[`docs/ARCHITECTURE_COMPLETION_CHECKLIST.md`](docs/ARCHITECTURE_COMPLETION_CHECKLIST.md).

## Tài liệu chuyên sâu

| Chủ đề | Tài liệu |
| --- | --- |
| Artifact DAG và SHA | [`docs/ARTIFACT_REGISTRY.md`](docs/ARTIFACT_REGISTRY.md) |
| Checklist chốt kiến trúc | [`docs/ARCHITECTURE_COMPLETION_CHECKLIST.md`](docs/ARCHITECTURE_COMPLETION_CHECKLIST.md) |
| V2 structure | [`docs/TABLE_STRUCTURE_V2.md`](docs/TABLE_STRUCTURE_V2.md) |
| Formula EvidenceSet | [`docs/FORMULA_EVIDENCE_SETS.md`](docs/FORMULA_EVIDENCE_SETS.md) |
| QueryProgram | [`docs/QUERY_PROGRAM.md`](docs/QUERY_PROGRAM.md) |
| OCR diagnostics | [`docs/OCR_QUALITY_PROFILE.md`](docs/OCR_QUALITY_PROFILE.md) |
| Semantic table metadata | [`docs/SEMANTIC_CATALOG.md`](docs/SEMANTIC_CATALOG.md) |
| Grounding dashboard | [`docs/GROUNDING_HEALTH_DASHBOARD.md`](docs/GROUNDING_HEALTH_DASHBOARD.md) |
| Autonomous review | [`docs/AUTONOMOUS_RAW_REVIEW.md`](docs/AUTONOMOUS_RAW_REVIEW.md) |
| Submission boundary | [`docs/SUBMISSION_EXECUTION_PIPELINE.md`](docs/SUBMISSION_EXECUTION_PIPELINE.md) |

## Repository map

```text
src/finance_query/   core contracts and runtime
scripts/             materialization, audit, training and submission commands
local/               local orchestration and optional review UI
kaggle/              Kaggle build/export helpers
configs/             retrieval/training configurations
tests/               regression and provenance gates
docs/                focused technical contracts
```

Generated labels, bundles, indexes and local notebooks are workspace data. Chỉ
xóa chúng khi đã xác nhận provenance và có bản sao; không coi chúng là source
code dư thừa.
