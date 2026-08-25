# Research sidecars

Research nằm ngoài E2E canonical và không được import từ `finance_query.e2e`.

- [Proof-policy V13](proof_policy/README_VI.md): proof obligations,
  evidence-closure và active-learning queues.
- [Open-source LLM diagnostic](llm/README_VI.md): đánh giá trợ giúp review.

Mọi output research đều `non-authorizing`: không answer, certificate,
promotion, submission hay release. `machine_provisional` không thể được đổi
thành `human_verified` bằng conversion hay score; chỉ human adjudication có
lineage đầy đủ mới có thể tạo training record.
