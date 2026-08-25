# Script E2E

Folder này chỉ chứa entrypoint vận hành cho luồng kiểm chứng đầu-cuối xác định.
Nó không chứa LLM, Kaggle, fine-tuning hoặc thao tác release.

| File | Chức năng |
| --- | --- |
| `run_deterministic_replay.py` | Wrapper không bắt buộc cho `finance-query run-e2e`. |
| `build_replay_blocker_queues.py` | Tạo queue first-blocker không materialize answer. |
| `build_binding_conflict_queue.py` | Tạo workbench exact-cell conflict. |
| `build_operation_graph_review_queue.py` | Tạo queue operation graph theo whole question. |
| `build_direct_lookup_candidate_queue.py` | Tạo candidate queue cho direct lookup; không là proof. |
| `build_ratio_formula_candidate_queue.py` | Tạo candidate queue formula ratio; không thực thi công thức. |

Chạy bằng:

```bash
.venv/bin/python scripts/e2e/run_deterministic_replay.py \
  --config configs/e2e/deterministic_replay_v1_locked.yaml \
  --output-dir artifacts/runs/e2e-deterministic-verification_YYYYMMDD
```

`finance-query run-e2e` là entrypoint E2E công khai duy nhất. Các script còn
lại chỉ tạo queue review hash-bound và không thể chứng nhận, phát hành hay tạo
đáp án.
