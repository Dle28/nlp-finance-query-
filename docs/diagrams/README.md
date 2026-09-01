# Pipeline diagrams

## Canonical

[AI GURU pipeline — resolve → E2E → compile](aiguru_pipeline_canonical_resolve_e2e_compile_v2.html)

Đây là sơ đồ duy nhất mô tả product path hiện hành: một trục solid đi từ
`data/question` qua `ProposalAST`, `ResolvedPrediction`, `E2EReceipt`,
`SubmissionLedger` đến package; rail dashed là feedback cho version kế tiếp.
`aiguru_pipeline_after_verification_v1.html` được giữ để đối chiếu lịch sử.

## Legacy

[vifinqa_predictive_pipeline.html](vifinqa_predictive_pipeline.html) là bản
target/exploration cũ về repair loop. Nó được giữ để tham khảo lịch sử nhưng
không phải operating contract, không dùng để suy ra code, release policy hoặc
roadmap hiện tại. Khi sơ đồ và tài liệu khác nhau, ưu tiên
[docs/PIPELINE.md](../PIPELINE.md).
