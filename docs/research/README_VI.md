# Research sidecars

Research là lớp discovery/diagnostic của pipeline, không phải một answer
pipeline song song. Sidecar có thể tạo candidate, ranking, queue hoặc phân
tích lỗi; quyền authority vẫn tập trung ở E2E canonical.

    research/model output
      → coordinate hint hoặc staged candidate
      → hydrate/replay từ tables_structured_v2
      → build-submission proposal
      → independent run-e2e verification

Các nhánh research được phép ảnh hưởng tới build-submission sau khi adapter
kiểm UID/document/table/row/column và hydrate ô từ V2. Dense/reranker/validity
chỉ navigation hoặc ranking; không được tự chép numeric truth.

run-e2e không import LLM/research authority. Reviewer decision, queue và
research score không được dùng để tạo certificate, training record, promotion,
submission authority hay release.

Mã/protocol nằm ở src/finance_query/research/,
scripts/research/, configs/research/ và tests/research/. Mỗi iteration ghi
output vào thư mục riêng; không sửa artifact lịch sử.

Xem [pipeline canonical](../PIPELINE.md) và
[artifact registry](../ARTIFACTS.md).

Xem [research integration log V1](RESEARCH_INTEGRATION_LOG_V1.md) để biết
family nào đã được bật trong candidate pipeline, family nào chỉ giữ
navigation/provenance và các route nào vẫn bị chặn bởi A/B/authority gate.

Xem [unified pipeline audit V1](UNIFIED_PIPELINE_AUDIT_V1.md) để xem command
canonical, cross-stage integrity gate, artifact/hash của run full-population
và các blocker strict còn lại.

Iteration answer-score mới nhất: [source-first direct lookup V1](ANSWER_SCORE_ABLATION_V1.md).

Các ablation route-composition gần nhất: [temporal source-first V1](TEMPORAL_SOURCE_FIRST_ABLATION_V1.md)
và [cross-entity source-first V2](CROSS_ENTITY_SOURCE_FIRST_ABLATION_V2.md).
