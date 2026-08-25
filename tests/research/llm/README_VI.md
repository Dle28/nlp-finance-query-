# Kiểm thử LLM research

Các test trong folder này xác minh sidecar LLM: packet contract, schema,
diagnostic và calibration. Chúng không phải tiêu chí pass/fail của E2E
deterministic và không cấp quyền cho answer, training hay release.

Chạy riêng:

```bash
PYTHONPATH=.:src .venv/bin/pytest -q tests/research/llm
```
