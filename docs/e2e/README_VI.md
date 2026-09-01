# Vận hành E2E canonical

E2E là authority role trong [pipeline canonical](../PIPELINE.md). Nó không
phải một answer engine thứ hai và không dùng LLM để thay proposal.

## Chạy độc lập

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-e2e \
      --config configs/e2e/deterministic_replay_v1_locked.yaml \
      --output-dir artifacts/runs/e2e_YYYYMMDD

Config khai báo source closure và manifest bằng path explicit. Lệnh tạo
binding, numeric-token registry/view, Decimal replay, evidence binding,
authorization, Answer Certificate và run receipt. Output directory phải là
thư mục mới.

## Chạy nối tiếp với proposal

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli build-submission \
      --verification-config configs/e2e/deterministic_replay_v1_locked.yaml \
      --output submissions/vifinqa_primary_verified_YYYYMMDD_rN

Builder tạo candidate ledger trước; E2E dùng ledger đó chỉ để giữ một
PREDICTED_CANDIDATE khi certificate ABSTAIN. Chỉ complete certificate khớp
question/answer mới cho phép proposal class VERIFIED.

## Output semantics

- answer_status=ANSWER và complete certificate: answer đã được authorize ở
  kênh E2E, nhưng release population vẫn là gate riêng.
- status=ABSTAIN + answer_status=PREDICTED_CANDIDATE: số còn uncertain,
  không được xem là verified, training label hoặc promotion signal.
- status=ABSTAIN + answer_status=ABSTAIN: không có candidate số hữu hạn
  sống sót; giữ reason codes và next gate.

Xem [docs/PIPELINE.md](../PIPELINE.md) để biết authority taxonomy và
[docs/ARTIFACTS.md](../ARTIFACTS.md) để biết output lineage.
