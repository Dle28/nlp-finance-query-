# AI GURU architecture

AI GURU có một product pipeline cho ViFinQA. Hợp đồng vận hành chi tiết nằm ở
[docs/PIPELINE.md](docs/PIPELINE.md); sơ đồ trực quan canonical nằm ở
[docs/diagrams/aiguru_pipeline_canonical_resolve_e2e_compile_v2.html](docs/diagrams/aiguru_pipeline_canonical_resolve_e2e_compile_v2.html).

    data/question
      → intake → source closure → typed question
      → retrieval + rerank → bilingual context compiler
      → proposal model → deterministic resolver
      → independent E2E verification → submission compiler
      → submission package → blocked feedback / next-version experiment

The one prediction path is `propose → resolve → verify → compile → deliver`.
The model and retrieval stages may propose claims and navigation metadata; the
resolver owns exact-cell hydration and Decimal execution; E2E produces proof;
the compiler only applies delivery policy and serializes the package.

build-submission là proposal/coverage role. run-e2e là authority role:
deterministic, hash-bound và không dùng model để sinh một answer thay thế.
Khi dùng --verification-config, hai role này được chạy tuần tự trong cùng
workflow, nên không tạo ra hai nguồn answer cạnh tranh.

Feedback sau submission chỉ tạo candidate cho prompt/RAG/AST experiment kế
tiếp; nó không tự cập nhật weight hoặc cấp quyền promote.

## Authority boundary

1. Raw source và source closure là nền tảng numeric truth.
2. Typed plan, retrieval, research packet, reranker, validity model và model
   output chỉ là candidate hoặc claim.
3. Numeric value phải được hydrate từ bảng V2 hiện hành; unit, period, scope,
   entity role và formula phải có binding/semantic receipt.
4. Decimal chỉ thực thi AST hữu hạn, allow-listed và hash-bound với plan.
5. Thiếu, stale hoặc xung đột semantics thì ABSTAIN; không đoán để cấp
   authority.
6. VERIFIED chỉ xuất hiện khi complete canonical Answer Certificate khớp
   cùng question và answer. Full release còn cần audit/ledger population-wide.

ABSTAIN không nhất thiết làm mất prediction coverage: một candidate sống sót
có thể được phát ở kênh PREDICTED_CANDIDATE, nhưng
answer_authorized=false, training_eligible=false và
promotion_allowed=false.

## Boundary của sidecar

src/finance_query/research/, scripts/research/ và các index/reranker chỉ
giúp discovery, ranking hoặc phân tích lỗi. Chúng không được tạo certificate,
release decision, training record hoặc promotion eligibility. E2E canonical
không import LLM/research authority.
