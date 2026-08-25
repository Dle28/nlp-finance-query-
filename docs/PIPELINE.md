# ViFinQA canonical pipeline

Đây là file theo dõi duy nhất cho pipeline. Không có V3–V13 nào là entrypoint
vận hành; các receipt lịch sử chỉ còn trong Git và artifact storage.

```text
immutable source closure
  → typed claim plan (candidate only)
  → exact cell binding
  → Decimal execution
  → semantic authorization
  → answer certificate / ABSTAIN
  → independent audit + full ledger
  → release gate → submission compiler
```

| Stage | Owner | Điều kiện để đi tiếp | Trạng thái hiện tại |
| --- | --- | --- | --- |
| Source | immutable input artifacts | source hash, table locator | Frozen input closure có sẵn |
| Compile | `src/finance_query/e2e/question_compiler.py` | typed plan hoặc `ABSTAIN` | Candidate-only |
| Bind | `src/finance_query/e2e/pipeline.py` | exact document/table/row/column/period/unit | Partial |
| Execute | `src/finance_query/e2e/decimal_executor.py` | declared Decimal AST, no generated code | Deterministic |
| Authorize | `src/finance_query/e2e/core/grounded_authorization.py` | từng semantic proposition có receipt | Incomplete ⇒ `ABSTAIN` |
| Release | external ledger + audit | 100% hash-bound lineage, independent audit | **BLOCKED** |

## Một lệnh vận hành

```bash
.venv/bin/python -m finance_query.cli run-e2e \
  --config configs/e2e/deterministic_replay_v1_locked.yaml \
  --output-dir artifacts/runs/e2e_YYYYMMDD
```

Config locked là config E2E duy nhất trong repo. Lệnh chỉ replay closure đã
pin hash, tạo output directory mới và không gọi LLM, fine-tune, tự sửa evidence
hay mở release.

## Hai nhánh cách ly

| Nhánh | Folder | Output được phép | Không được phép |
| --- | --- | --- | --- |
| Proof policy | `research/proof_policy/` | queue, receipt obligation, active-learning policy provisional | answer, certificate, release, tự tạo training record |
| Model diagnostic | `research/llm/` | Qwen3-8B proposal numeric-free, triage cho review | answer, evidence, promotion, release |

Model graph chỉ có **Qwen3-8B (8.2B, open-weight)**. ChatGPT ở ngoài model
graph, chỉ là reviewer có provenance `chatgpt_verified`; không inference,
ensemble hoặc fine-tune cho cuộc thi. Một proposal Qwen luôn cần independent
review trước khi có thể trở thành training data do human adjudicator duyệt.

## Quy tắc fail-closed

```text
candidate only                 → không phải evidence
exact binding + Decimal replay → execution receipt, chưa phải answer
thiếu semantic proof           → ABSTAIN
đủ proof + audit + ledger      → release-gate review
release ready                  → submission compiler
```

Không sidecar nào được import vào E2E hoặc nhảy qua các transition này.
