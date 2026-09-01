# AI GURU / ViFinQA — pipeline canonical

Đây là hợp đồng duy nhất cho pipeline sản phẩm. Kiến trúc không còn được mô tả
như hai luồng trả lời song song. Có một đường dữ liệu duy nhất từ
`data/question` đến `submission`; feedback được tạo sau khi đã có receipt của
run và chỉ nuôi phiên bản kế tiếp:

    ProposalAST
      → Deterministic Resolver
      → ResolvedPrediction
      → independent E2E verification
      → Submission Compiler
      → submission package
      → blocked feedback / next-version experiment

`run-submission-flow` là entrypoint canonical cho chuỗi trên. Trong giai đoạn
migration, `build-submission` cũ được chạy như compatibility producer để tạo
proposal artifacts; resolver/observer/compiler mới quyết định artifact nào là
đầu ra delivery. Vì vậy manifest sẽ ghi rõ phần legacy còn duplicate
execution, không tuyên bố đã loại bỏ nó trước khi backend được refactor hoàn
toàn.

## 1. Mười stage của một product path duy nhất

| Stage | Input → output | Được phép làm | Không được phép làm |
| --- | --- | --- | --- |
| 01. Intake | data/question → immutable request | Khai báo câu hỏi, id và input manifest | Suy truth từ tên file |
| 02. Source Closure | request → source universe | Khóa document/table path, hash và phạm vi nguồn | Chọn source “mới nhất” hoặc stale ngoài closure |
| 03. Typed Question | question → TypedQuestion | Chuẩn hóa entity, metric, period, reporting scope, unit, operation và operands | Xem typed plan/model label như proof |
| 04. Retrieval + Rerank | TypedQuestion → candidate pool | Lexical/dense retrieval, rerank, dedupe và top-k navigation | Biến score/rank/probability thành evidence |
| 05. Context Compiler | candidate pool → bilingual ContextPacket | Giữ tiếng Việt, canonical aliases, source scope, reporting scope và failure context; loại numeric cells | Đưa raw numeric value vào prompt model |
| 06. Proposal Model | ContextPacket → ProposalAST | Đề xuất route, claims, operation và coordinate hints; có thể dùng RAG/model/fine-tune | Cấp VERIFIED hoặc evidence authority |
| 07. Deterministic Resolver | ProposalAST → ResolvedPrediction | Resolve exact cell, hydrate source, kiểm formula/operand và Decimal execution | Dùng ranking/model confidence làm proof |
| 08. E2E Verification | ResolvedPrediction + source hashes → E2EReceipt | Independent semantic/provenance/binding/replay checks, operand-level receipt và certificate | Tìm answer mới hoặc sửa proposal thiếu evidence |
| 09. Submission Compiler | prediction + receipt + policy → SubmissionLedger | Áp dụng best-effort/strict policy, coverage accounting và serialize JSON/CSV/ZIP | Re-resolve hoặc re-execute lần hai |
| 10. Delivery | SubmissionLedger → submission package | Gate phát hành, record audit và đưa blocked rows sang feedback | Gọi candidate là strict release nếu chưa qua gate |

Đây là một đường duy nhất: `propose → resolve → verify → compile → deliver`.
Authority boundary nằm sau resolver/E2E; handoff dữ liệu không phải handoff
quyền cấp phép. Feedback chạy sau delivery và chỉ tạo input cho phiên bản kế
tiếp, không quay ngược để tự cấp authority cho submission hiện tại.

Trạng thái triển khai hiện tại là migration-safe: resolver nhận proposal/audit
từ builder legacy và giữ lại answer đã replay như một candidate, còn E2E chạy
engine canonical độc lập. Vì vậy manifest vẫn đánh dấu
`duplicate_execution_legacy=true`; resolver có bật canonical Decimal path nhưng
manifest ghi rõ `canonical_decimal_reexecution_status=PARTIAL`. Đây là TODO
backend để chuyển hydrate/formula/Decimal hoàn toàn vào resolver và bỏ duplicate
execution, không phải lý do để gọi candidate là VERIFIED.

## 2. Hai role, một workflow

### Proposal / coverage role — build-submission

Lệnh này đọc câu hỏi, review bundle, candidate/research inputs và optional
dense/reranker. Nó có thể tạo prediction số cho mọi câu để đo coverage. Các
route exact, staged model, raw recovery, program-aware, semantic heuristic và
fallback được ghi trong diagnostics; fallback vẫn chỉ là prediction.

Output chính:

- submission.json: file prediction;
- data/*.csv và query replay: artifact kiểm tra submission;
- best_surviving_candidates_v1.jsonl: candidate ledger;
- prediction_audit_ledger_v1.jsonl: class/reason/proposal lineage;
- diagnostics.jsonl, build_report.json, submission.zip.

Candidate validity, dense retrieval, reranker và research fusion chỉ sắp xếp
hoặc tăng recall. Giá trị dùng trong proposal phải được hydrate/replay từ
tables_structured_v2; các input này không tự cấp authority.

### Authority / verification role — run-e2e

Lệnh này nhận một config có đường dẫn explicit, hash-bound. Nó chạy theo thứ
tự:

    declared input closure
      → exact cell binding
      → numeric-token registry/view
      → allow-listed Decimal execution
      → evidence binding + semantic authorization
      → Answer Certificate / ABSTAIN
      → reproducibility receipt

E2E không thay proposal bằng câu trả lời do model sinh. Nếu config có
best_candidate_predictions, certificate có thể giữ một candidate hữu hạn ở
kênh PREDICTED_CANDIDATE khi authorization chưa đủ; top-level vẫn là
ABSTAIN, answer_authorized=false và promotion_allowed=false.

## 3. Quy tắc authority

VERIFIED chỉ hợp lệ khi đồng thời có:

- cùng question_id và answer với proposal;
- exact document/table/row/column binding từ source closure hiện hành;
- entity, role, period, scope, metric và unit semantics đạt hoặc được chứng
  minh NOT_APPLICABLE đúng contract;
- formula/operand compatibility receipt đầy đủ;
- operation AST allow-listed, hash khớp và Decimal replay đạt;
- complete canonical Answer Certificate và audit không còn blocker.

Một trong các dấu hiệu sau chỉ là diagnostic, không phải authority:
retrieval rank, model confidence, candidate validity probability, số hiển thị
ở đúng ô, pandas/Decimal chạy được, ready label, tên file hoặc human/research
metadata không có lineage đầy đủ.

### Policy classes

| Class | Ý nghĩa | Competition | Strict / release |
| --- | --- | --- | --- |
| VERIFIED | Proposal khớp complete canonical certificate | Dùng ở trust cao nhất | Chỉ đi tiếp nếu full-population gate đạt |
| PARTIAL | Có một phần binding/replay nhưng thiếu proof hoàn chỉnh | Giữ best-effort và ghi reason | Không authorize |
| UNRESOLVED | Thiếu hoặc mâu thuẫn source/semantic/formula | Giữ coverage nếu còn candidate; ưu tiên repair | Fail-closed |
| REJECTED | Stale, sai coordinate/value hoặc vi phạm contract | Bỏ proposal, thử candidate khác/fallback | Không release |

## 4. Lệnh vận hành

Prediction:

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli build-submission \
      --output submissions/vifinqa_primary_integrated_YYYYMMDD_rN

Verification:

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-e2e \
      --config configs/e2e/deterministic_replay_v1_locked.yaml \
      --output-dir artifacts/runs/e2e_YYYYMMDD

Tuần tự proposal → verification → policy trong một command:

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-submission-flow \
      --output artifacts/runs/submission_flow_YYYYMMDD_rN \
      --verification-config configs/e2e/deterministic_replay_v1_locked.yaml \
      --expected-question-count 1012 \
      --require-full-population \
      --flow-release-policy best_effort

Hoặc dùng receipt đã chạy:

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli build-submission \
      --verification-certificate artifacts/runs/e2e_YYYYMMDD/answer_certificates_v1.jsonl \
      --output submissions/vifinqa_primary_verified_YYYYMMDD_rN

--verification-config và --verification-certificate loại trừ lẫn nhau.
Output directory đã tồn tại sẽ bị từ chối để không ghi đè artifact.

`run-submission-flow` yêu cầu `--verification-config` mặc định. Muốn chỉ tạo
candidate để debug hoặc chuẩn bị fixture phải ghi rõ `--skip-e2e`; flow đó sẽ
được đánh dấu `e2e_skipped_explicitly` và không thể được gọi là verified.

## 5. Source và research boundary

Source of truth numeric là source closure/bảng V2 hiện hành. V3/OCR/context
chỉ bổ sung provenance/semantic context theo contract. Không chọn source bằng
tên file “mới nhất”; mọi input E2E phải nằm trong config explicit và được hash
vào receipt.

Research sidecars gồm retrieval, period/column packets, multicol packets,
dense/reranker, validity model, proof-policy và LLM diagnostics. Chúng được
phép:

- tạo coordinate hint hoặc staged candidate;
- tạo queue, ranking, coverage và lỗi phân tích;
- cung cấp input cho build-submission sau khi builder hydrate/replay V2.

Chúng không được:

- đọc/gán numeric truth trực tiếp từ artifact candidate;
- tạo Answer Certificate hoặc chuyển ABSTAIN thành VERIFIED;
- tạo training record, promotion, release hoặc submission authority.

## 6. Artifact contract

### E2E input

Profile vận hành là
configs/e2e/deterministic_replay_v1_locked.yaml. Config phải khai báo
period_packets, route_overlay, structured_tables, evidence_context,
metric_registry và manifest tương ứng bằng đường dẫn explicit. Candidate
ledger là input tùy chọn cho kênh prediction, không phải proof.

### E2E output

Run mới thường có:

- exact_cell_unit_binding_candidates_v2.jsonl + manifest;
- numeric_cell_token_registry_v1.jsonl và public view;
- grounded_execution_replay_v2.jsonl + telemetry/manifest;
- evidence_bindings_v1.jsonl;
- answer_certificates_v1.jsonl;
- authorization_readiness_v1.json;
- grounded_e2e_run_v1.json.

### Submission output

Submission phải giữ prediction, diagnostics, candidate ledger, proposal audit
ledger, CSV/Pandas replay và ZIP. Không được xem ZIP là release certificate.

## 7. Release gate

Technical replay hoàn tất không đồng nghĩa release. Release cần full
population ledger, source lineage, independent audit và policy quyết định
explicit. Khi còn thiếu proof, ABSTAIN/UNRESOLVED được giữ nguyên; không được
xoá record chỉ để làm tỷ lệ pass đẹp hơn. Trạng thái release hiện tại chỉ được
công bố khi có receipt tương ứng; không suy luận từ component score.

## 8. Những gì đã thống nhất sau phản biện

1. Bỏ mô hình “hai luồng cùng trả lời”; chỉ còn proposal → verifier → policy.
2. Tách coverage khỏi authority: competition có thể giữ best-effort, release
   vẫn fail-closed.
3. Đưa toàn bộ model/research/ranking về đúng discovery boundary.
4. Chuẩn hóa một taxonomy kết quả và một đường artifact audit.
5. Giữ human_verified, exact source-row grounding và hash lineage; không dùng
   score hoặc replay đơn lẻ để bypass semantic gate.

## 9. Canonical implementation: Proposal → Resolve → E2E → Compile

Lệnh canonical:

```text
PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-submission-flow \
  --output artifacts/runs/submission_flow_YYYYMMDD_rN \
  --verification-config configs/e2e/deterministic_replay_v1_locked.yaml \
  --flow-release-policy best_effort
```

Các module có mục đích riêng:

- `pipeline/resolver.py`: biến proposal/audit legacy thành
  `resolved_predictions_v1.jsonl`; ghi operand binding, formula contract và
  execution receipt ở mức candidate. Không cấp authority.
- `pipeline/flow_audit.py`: kiểm tra read-only toàn bộ hand-off, population
  IDs, content hashes, ZIP và feedback flags sau khi các stage hoàn tất; gate
  này chỉ xác nhận integrity, không xác nhận semantic accuracy.
- `pipeline/e2e_observer.py`: nhận đúng resolved ledger, gọi grounded E2E
  độc lập và tạo `e2e_receipts_v1.jsonl`; chỉ certificate khớp mới có thể là
  `VERIFIED`, E2E không sinh câu trả lời mới.
- `pipeline/submission_compiler.py`: ghép prediction với resolver/E2E receipt,
  áp dụng `best_effort` hoặc `strict`, rồi tạo `submission.json`,
  `submission_ledger_v1.jsonl` và `submission.zip`.
- `pipeline/context/compiler.py`: lấy blocked rows sau compiler, giữ câu hỏi
  và nhãn tiếng Việt, canonical aliases, top-k labels/coordinates, source
  scope, reporting scope, formula/operand failure metadata; numeric cells vẫn
  bị loại khỏi prompt feedback.
- `pipeline/feedback/blocked.py`: model critic đánh giá mọi row
  `PARTIAL`/`UNRESOLVED`/`REJECTED`/`ABSTAIN`; kết quả là feedback candidate,
  không phải answer/certificate/training label/promotion.

Output canonical nằm trong thư mục đã truyền ở `--output`:

```text
<output>/
  resolver/resolved_predictions_v1.jsonl
  e2e_observer/e2e_receipts_v1.jsonl              # nếu có --verification-config
  compiled_submission/submission.json
  compiled_submission/submission_ledger_v1.jsonl
  compiled_submission/submission.zip
  blocked_feedback/                                # sau compiler
<output>.proposal_compatibility/                  # producer legacy, không phải release
```

`best_effort` giữ coverage để tạo candidate submission. `strict` chỉ đánh dấu
row `STRICT_READY` khi resolver là `RESOLVED_CANDIDATE` và E2E là `VERIFIED`; cả
hai mode vẫn để `release_authorized=false` cho đến release gate độc lập.

## 10. Submission-first feedback implementation

The canonical flow then compiles all strict-blocked rows (`PARTIAL`,
`UNRESOLVED`, `REJECTED`, `ABSTAIN`) into numeric-free ContextPackets. An
optional Qwen-compatible model evaluates those packets using the contract in
`configs/pipeline/submission_feedback_v1.json`.

```text
proposal → resolver → E2E receipt → submission compiler
  → blocked ContextPacket compiler
  → model feedback critic (optional)
  → feedback_records + improvement_records
  → next prompt/RAG/AST experiment
```

The model feedback output is intentionally non-authorizing. It may classify a
failure as retrieval, reranking, context, semantic, temporal/scope, unit,
formula/operand, provenance, AST or replay-related and recommend the next
experiment. It cannot emit a numeric answer, source value, certificate,
training label, promotion decision or release decision. Numeric source cells
are redacted from the packet; coordinates, labels, question text, hashes and
the deterministic receipt remain available for diagnosis.

Prepare packets without loading a model:

```bash
PYTHONPATH=.:src .venv/bin/python -m finance_query.cli evaluate-blocked \
  --submission-dir artifacts/kaggle_runs/<run>/submission \
  --output-dir artifacts/feedback/<run> \
  --feedback-prepare-only
```

Run the model feedback lane:

```bash
PYTHONPATH=.:src .venv/bin/python -m finance_query.cli evaluate-blocked \
  --submission-dir artifacts/kaggle_runs/<run>/submission \
  --output-dir artifacts/feedback/<run> \
  --feedback-model-name Qwen/Qwen2.5-Coder-1.5B-Instruct \
  --feedback-device cuda \
  --feedback-prompt-profile en_system_vi_context_compact_v2
```

Run the primary submission and feedback as one operator flow:

```bash
PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-submission-flow \
  --output submissions/vifinqa_primary_YYYYMMDD_rN \
  --verification-config configs/e2e/deterministic_replay_v1_locked.yaml \
  --feedback-model-name Qwen/Qwen2.5-Coder-1.5B-Instruct \
  --feedback-device cuda
```

If `--feedback-model-name` is omitted, the command still produces the
ContextPackets and explicit `MODEL_NOT_RUN` feedback records. The feedback
artifact is a versioned input to later prompt ablations, retrieval hard
negatives, reranker/AST training and source-review queues. It is not itself a
weight update. A human/source-verified label is required before a record can
become training data or affect promotion.

The feedback run emits `blocked_question_context_packets_v1.jsonl`,
`blocked_question_feedback_v1.jsonl`,
`improvement_feedback_records_v1.jsonl`, `improvement_plan_v1.json`,
`feedback_summary.json` and `blocked_feedback_run.manifest.json`. The plan is
an aggregate of proposed experiments grouped by action/target; every item is
`PROPOSED_NOT_RUN` until it is evaluated against a held-out set.

### Real-model Kaggle worker

The repository contains a portable Kaggle worker for this model-feedback
lane:

- `scripts/research/package_blocked_feedback_kaggle_v1.py` creates an immutable
  numeric-free input dataset and records packet, prompt and model hashes.
- `scripts/research/run_blocked_feedback_kaggle_v1.py` loads the pinned real
  Hugging Face `Qwen/Qwen2.5-Coder-1.5B-Instruct` checkpoint on a Kaggle GPU,
  uses the Qwen chat template, validates every response against the feedback
  contract and writes a resumable checkpoint.
- Runtime or contract errors become explicit `ABSTAIN` feedback records. They
  remain model-run telemetry and are never promoted to gold labels.
- `model_run_manifest.json` must report `real_model_invoked=true`, the pinned
  revision, complete row count and `errors=[]` before the run is considered
  complete. `RUNNING` or a partial checkpoint is not completion evidence.

Operator commands for a new immutable Kaggle input/kernel version are:

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/research/package_blocked_feedback_kaggle_v1.py \
  --source-dir artifacts/runs/<canonical-run>/blocked_feedback_prepare \
  --output-dir artifacts/kaggle_upload/<feedback-input-version> \
  --prompt-profile en_system_vi_context_compact_v2 \
  --dataset-id <owner>/<feedback-input-dataset>

.venv/bin/kaggle datasets create \
  -p artifacts/kaggle_upload/<feedback-input-version>

# Set dataset_sources in kernel-metadata.json to the newly created dataset,
# then copy the worker and push the new GPU version.
cp scripts/research/run_blocked_feedback_kaggle_v1.py \
  artifacts/kaggle_upload/<feedback-kernel-version>/run_blocked_feedback_kaggle_v1.py
.venv/bin/kaggle kernels push \
  -p artifacts/kaggle_upload/<feedback-kernel-version>
```

If Kaggle reports the GPU session limit, do not delete the kernel or assume a
queued push ran. Check the latest kernel status and wait for a terminal status;
the local input dataset remains immutable and can be pushed again afterward.

The `en_system_vi_context_compact_v2` profile keeps the Vietnamese question,
source/reporting scope, candidate navigation metadata and operand/E2E failure
states while removing repeated audit payloads and numeric cells from the model
context. English is used for the system instruction for model reliability;
Vietnamese labels and question text remain unchanged. Feedback is aggregated
into proposed prompt, RAG, reranker, AST or source-review experiments, but no
automatic weight update or promotion occurs.
