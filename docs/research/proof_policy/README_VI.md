# Proof-policy V13 research

V13 học/đánh giá **proof-policy pattern**, không học đáp án hay raw financial
value. Nó tạo receipt intake, candidate policy và probability audit riêng.
Một candidate chỉ có thể thành training record sau independent human
adjudication; mọi câu vẫn phải quay về E2E để bind, authorize và replay.

```bash
.venv/bin/python scripts/research/proof_policy/build_claim_requirement_coverage_v13.py \
  --config configs/research/proof_policy/claim_requirement_coverage_v13.json \
  --output-dir artifacts/research/v13_YYYYMMDD

.venv/bin/python scripts/research/proof_policy/build_v13_evidence_closure_workbench.py \
  --config configs/research/proof_policy/v13_evidence_closure_workbench_v1.json \
  --output-dir artifacts/research/v13_closure_YYYYMMDD
```

Các script active-learning/model job cùng ở `scripts/research/proof_policy/`.
Chúng chỉ được chạy sau khi config và input manifest cùng hash đã được pin.
