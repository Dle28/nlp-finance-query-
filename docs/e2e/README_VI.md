# Vận hành E2E canonical

`finance-query run-e2e` là lệnh vận hành duy nhất. Nó replay một input closure
đã hash-bound; không tự suy ra đáp án bằng LLM.

```bash
.venv/bin/python -m finance_query.cli run-e2e \
  --config configs/e2e/deterministic_replay_v1_locked.yaml \
  --output-dir artifacts/runs/e2e_YYYYMMDD
```

Kiểm tra nhanh contract:

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q -m e2e
```

Output là directory mới chứa binding, numeric-token view, Decimal execution,
authorization và receipt. `complete_research_only` chỉ nói replay tái lập;
answer certificate không PASS thì kết quả vẫn là `ABSTAIN`. Release luôn tách
khỏi replay.

Xem thứ tự stage, ownership và trạng thái tại [../PIPELINE.md](../PIPELINE.md).
