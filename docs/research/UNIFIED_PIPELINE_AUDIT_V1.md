# Unified pipeline audit V1

Ngày: 2026-08-31  
Phạm vi: nối toàn bộ candidate proposal, deterministic resolver, independent
E2E, submission compiler và blocked-feedback thành một flow có lineage/audit.

## Kết luận

Đã có một public flow duy nhất cho vận hành:

```text
research/candidate sidecars
        ↓  coordinate/navigation only; hydrate numeric từ structured tables
proposal AST + best-effort submission
        ↓
deterministic resolver → ResolvedPrediction
        ↓
independent E2E observer → E2EReceipt
        ↓
submission compiler → ledger + submission.json + ZIP
        ↓
blocked feedback → model-lifecycle gate
        ↓
pipeline_integrity_audit_v1.json
```

CLI canonical:

```bash
PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-submission-flow \
  --output artifacts/runs/unified_submission_flow_v1_20260831_r3 \
  --structured-tables artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl \
  --structured-table-filter candidate_uids \
  --selective-source-first-route-hydration \
  --source-line-map artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json \
  --require-source-line-map \
  --expected-question-count 1012 \
  --require-full-population \
  --enable-answer-level-selector \
  --enable-formula-evidence-bridge \
  --formula-evidence artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/formula_evidence_sets_typed_v1.jsonl \
  --verification-config configs/e2e/deterministic_replay_v1_local_source_semantic_v7.yaml \
  --flow-release-policy best_effort
```

Artifact run này chạy đủ population `1.012/1.012`. Cross-stage integrity
audit là `PASS`, `errors=[]`, và cùng một question-ID digest
`c909824380e17db86ca7df57f960cf6388fce2f4c56e31fcb9ffeb1459ae39b1` được giữ
qua proposal, resolver, E2E receipt, compiler và feedback.

## Các gate đã kiểm tra

| Stage | Kết quả thực tế | Ý nghĩa |
|---|---:|---|
| Proposal compatibility builder | `1.012` câu, `970` candidate, replay `1.012`, `errors=[]` | Candidate/proposal, chưa phải answer authority |
| Answer-level selector | `305` selected, `305` baseline fallback, `707` abstain | Selector chỉ chọn candidate plan; không cấp strict authority |
| Formula evidence bridge | `11` candidate được build, `stored_sidecar_answers_used=false` | Formula sidecar chỉ đi qua current-table/Decimal replay |
| Resolver | `57` `RESOLVED_CANDIDATE`, `955` `BLOCKED`; canonical replay `57 PASS`, `718 BLOCKED`, `237 REJECTED` | Hash-bound handoff, không tự authorize |
| Independent E2E observer | `1.012` receipt, `1.012 REJECTED`; certificate content hash hợp lệ `1.012/1.012` | Receipt độc lập được tạo, nhưng không có strict verification |
| Submission compiler | `1.012` `BEST_EFFORT_CANDIDATE`, strict-ready `0` | ZIP hợp lệ, chỉ là delivery artifact |
| Blocked feedback | `1.012` packets, `1.012` feedback records, model `MODEL_NOT_RUN` | Feedback/plan, không cấp training/promotion/release |
| Pipeline integrity | `PASS`, `0` lỗi | Lineage/ID/hash/ZIP closure đã kiểm tra |

ZIP cuối của compiler có `1.013` entries: `submission.json` và `1.012`
evidence CSV. SHA-256 của ZIP là
`cef84cbdd9760e5f1e597dd4a72f5cf6c3d49a1e04c0d19053ea5300d6fee3fa`.

## Trạng thái authority — phải đọc riêng khỏi trạng thái kỹ thuật

```text
ANSWER_ACCURACY: NOT_MEASURED
EXECUTION_ACCURACY: NOT_MEASURED
Scorer/gold: NOT RUN / chưa có independent gold hoặc official scorer trong run này
Strict answer authorized: 0
Release authorized: false
Promotion allowed: false
Authority status: CANDIDATE_ONLY
```

`E2E observer = PASS` ở đây chỉ có nghĩa là observer đã kiểm tra được contract
và phát ra receipt cho đủ 1.012 dòng. Nó không có nghĩa là 1.012 đáp án đúng:
thực tế tất cả receipt đang là `REJECTED`, strict-ready là `0`. Không suy diễn
accuracy từ answer diff, Decimal replay, candidate count, ZIP integrity hay
certificate content hash.

Các reason-code E2E nổi bật được đếm từ receipt (các event có thể chồng lấp,
không phải số câu sai) là `E2E_SOURCE_OR_SEMANTIC_PROOF_MISSING=1.012`,
`CANONICAL_OPERATION_AST_HASH_MISMATCH=1.012`,
`E2E_SOURCE_COORDINATE_MISMATCH=914` và
`CANONICAL_REEXECUTION_NOT_PROVEN=955`. Vì vậy nút thắt kế tiếp là source
closure/semantic certificate và handoff AST, không phải nới compiler hay thêm
heuristic theo Question ID.

## Research nào đã đi qua unified flow

- Exact/source-first lookup, current structured-table hydration, row/column/
  period/unit guard và Decimal replay đi vào proposal compatibility builder;
  selector và typed formula bridge được bật trong run r3.
- Independent E2E không nhận numeric authority từ model, RAG, dense index,
  reranker, reviewer hoặc research score. Nó nhận `ResolvedPrediction` làm
  input rõ ràng và phát receipt riêng.
- RAG/hybrid retrieval, dense/reranker, candidate-validity, model-candidate
  và các diagnostic multi-column/duplicate/provenance vẫn là navigation,
  ranking, hydration hint hoặc review queue. Chúng không thể bật
  `VERIFIED`, promotion hay release.
- Các route overlay q72–q75 và materializer dùng explicit Question-ID sets
  vẫn bị giữ ngoài main answer flow. Question ID chỉ là tracking metadata;
  không được dùng làm rule prediction/routing/answer selection.
- Các artifact Kaggle/external, run đang chạy, snapshot race, stale receipt
  hoặc A/B thiếu cùng fingerprint không được nhập ngầm vào run này.

## Những gì đã thay đổi trong code

- `src/finance_query/pipeline/flow_audit.py`: gate read-only kiểm tra artifact
  tồn tại, protocol, file hash, question-ID closure, manifest input/output,
  resolver/E2E receipt ID, compiler ledger, ZIP contents và feedback authority.
- `src/finance_query/pipeline/submission_flow.py`: một orchestrator public cho
  Proposal → Resolve → E2E → Compile → Feedback → model-lifecycle → audit;
  best-effort mặc định bắt buộc `--verification-config`, còn bỏ E2E phải ghi
  rõ `--skip-e2e` và bị giới hạn candidate-only.
- `src/finance_query/cli.py` và `src/finance_query/pipeline/__init__.py`:
  public entrypoint/export rõ ràng.
- `tests/pipeline/test_flow_audit.py`,
  `tests/pipeline/test_submission_flow_cli.py`,
  `tests/pipeline/test_submission_flow_guards.py`: kiểm tra gate, hash-bound
  E2E handoff và guard CLI.
- `docs/PIPELINE.md`, `README.md`: ghi một command canonical và phân biệt
  component commands với unified flow.

## Regression và artifact proof

```text
PYTHONPATH=.:src .venv/bin/pytest -q
721 passed, 1 skipped in 8.12s

.venv/bin/python -m compileall -q src/finance_query/pipeline tests/pipeline
git diff --check
PASS
```

Manifest/audit chính:

- `artifacts/runs/unified_submission_flow_v1_20260831_r3/submission_flow.manifest.json`
  SHA-256 `1b40477a67b7f5c9b26d83c773c8ebbfa1dfc7dff4478aeb06f2eba9d1d81e73`;
- `artifacts/runs/unified_submission_flow_v1_20260831_r3/pipeline_integrity_audit_v1.json`
  SHA-256 `520a233005888029b11aa1e8d355f842adaf7ffac77e83d850db55aef3971963`;
- resolver output:
  `artifacts/runs/unified_submission_flow_v1_20260831_r3/resolver/resolved_predictions_v1.jsonl`
  SHA-256 `0b2ffa538963367b7cb18c0132e06d382d0c3f4e29b59d592f354954254ffd03`;
- E2E receipts:
  `artifacts/runs/unified_submission_flow_v1_20260831_r3/e2e_observer/e2e_receipts_v1.jsonl`
  SHA-256 `bbea21b1a078b9b61f5d738e5b5e23bebe95546d7061be55a735f1bda2a2ae9f`;
- final compiler ZIP:
  `artifacts/runs/unified_submission_flow_v1_20260831_r3/compiled_submission/submission.zip`.

`artifacts/runs/unified_submission_flow_v1_20260831_r3.zip` ở thư mục cha là
ZIP của compatibility builder. Bản nên dùng để kiểm tra delivery của unified
flow là ZIP nằm trong `compiled_submission/`, vì nó được tạo sau resolver và
E2E handoff.

## Migration còn lại

Unified orchestration đã sạch và có kiểm chứng, nhưng backend chưa phải một
implementation duy nhất hoàn toàn:

- `duplicate_execution_legacy=true`;
- resolver backend hiện là `legacy_submission_builder_adapter`;
- canonical Decimal re-execution là `PARTIAL` (`294` câu có re-execution,
  trong đó resolver ghi nhận `57 PASS`, phần còn lại bị block/reject);
- bước refactor đúng tiếp theo là chuyển formula hydration và Decimal
  execution vào resolver, rồi bỏ duplicate execution trong compatibility
  builder sau khi có A/B population-level.

Đây là migration debt được ghi rõ trong manifest, không phải lý do để tắt gate
hoặc nhập candidate thành VERIFIED.

## Quyết định

```text
Decision: KEEP — unified flow/integrity layer
Research accuracy decision: INVESTIGATE_FURTHER
Authority: CANDIDATE_ONLY
Release: BLOCKED (đúng chính sách)
```

Giữ flow mới làm cửa vận hành chung. Việc tiếp theo nên tập trung vào source
closure/semantic certificate để giảm `REJECTED`/`BLOCKED`, sau đó chạy một A/B
đủ 1.012 câu với scorer/gold độc lập. Không nới gate, không dùng research
metadata làm đáp án, và không thêm ngoại lệ theo Question ID.
