# ViFinQA — deterministic financial evidence pipeline

ViFinQA chỉ cho phép một số đi tới certificate khi hệ thống chứng minh được:

```text
entity → report → scope → period → table → row → column → unit → Decimal operation
```

Thiếu hoặc mâu thuẫn bất kỳ mắt xích nào sẽ trả `ABSTAIN`.

## Bắt đầu ở đây

- [Pipeline canonical và trạng thái](docs/PIPELINE.md)
- [Hướng dẫn chạy E2E](docs/e2e/README_VI.md)
- [Research sidecars](docs/research/README_VI.md)

```bash
.venv/bin/python -m finance_query.cli run-e2e \
  --config configs/e2e/deterministic_replay_v1_locked.yaml \
  --output-dir artifacts/runs/e2e_YYYYMMDD
```

Đây là public CLI duy nhất. LLM/Kaggle/V13 chỉ chạy qua `scripts/research/`;
output máy không thể tự trở thành answer, training record, promotion,
submission hay release.

## Layout

```text
src/finance_query/e2e/                 canonical pipeline + core primitives
src/finance_query/research/proof_policy/ V13 proof-policy/active learning
src/finance_query/research/llm/         Qwen3-8B diagnostic/review triage
scripts/e2e/                            operator utilities for E2E
scripts/research/                       non-authorizing research commands
configs/e2e/                            one locked operational profile
configs/research/                       sidecar controls only
tests/e2e/                              canonical contract suite
tests/research/                         isolated research suite
```

Raw reports and run artefacts remain immutable; never overwrite a completed
output directory. See [docs/PIPELINE.md](docs/PIPELINE.md) for gates and
current release state.
