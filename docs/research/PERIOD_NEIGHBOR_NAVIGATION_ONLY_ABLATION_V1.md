# Period-neighbor navigation-only ablation V1

Ngày: 2026-08-30  
Phạm vi: ViFinQA full population, 1.012 câu; full structured corpus, 146.246 tables.

## Kết luận ngắn

Nhánh period-neighbor chỉ nên được giữ trong candidate submission lane để cải
thiện document/table retrieval coverage. Nhánh này không được phép tự thay
đổi answer selection, evidence query hoặc prediction tier.

Trên cặp A/B cùng implementation snapshot đã load vào process:

| Metric | Control | Period-neighbor navigation-only | Delta |
|---|---:|---:|---:|
| Records | 1.012 | 1.012 | 0 |
| Non-zero answers | 1.001 | 1.001 | 0 |
| Answer differences | - | 0/1.012 | invariant |
| Pandas query differences | - | 0/1.012 | invariant |
| Evidence differences | - | 0/1.012 | invariant |
| Prediction-tier differences | - | 0/1.012 | invariant |
| Average relevant documents/question | 2,152 | 2,652 | +0,500 |
| Average relevant tables/question | 3,687 | 4,465 | +0,779 |

Trên machine-review silver navigation proxy, tập 364 câu
machine_provisional cho kết quả:

| Proxy | Control | Variant | Delta |
|---|---:|---:|---:|
| Target document included | 85,44% | 94,51% | +9,07 điểm % |
| Target table included | 51,65% | 58,79% | +7,14 điểm % |
| Target table UID selected for answer | 38,74% | 38,74% | 0 |

Tập 62 câu machine_calibrated giữ nguyên 100% target document, target table và
target UID ở cả hai nhánh. Variant có 41 document gains/8 losses và 50 table
gains/24 losses trên tập machine_provisional; vì vậy đây là net coverage gain
có trade-off, không phải bằng chứng accuracy.

## Thiết kế và authority boundary

Flag thực nghiệm:

    --period-neighbor-offset 1
    --period-neighbor-navigation-only

Neighbor dense hits chỉ được dùng để mở rộng relevant document/table emission.
Answer pool được giữ ở pre-dense review bundle cộng research fusion không mang
giá trị answer. Dense retrieval, retrieval rank, source coordinate replay và
Decimal replay không tự cấp semantic authority.

## Artifact

- Control report: artifacts/runs/vifinqa_answer_optimization_20260830/stable_control_no_dense_calibrated_r1/submission/build_report.json
- Control ZIP: artifacts/runs/vifinqa_answer_optimization_20260830/stable_control_no_dense_calibrated_r1/submission.zip
- Variant report: artifacts/runs/vifinqa_answer_optimization_20260830/stable_period_neighbor_navigation_only_calibrated_r1/submission/build_report.json
- Variant ZIP: artifacts/runs/vifinqa_answer_optimization_20260830/stable_period_neighbor_navigation_only_calibrated_r1/submission.zip
- Builder implementation: scripts/e2e/build_competition_submission_v1.py
- Integration tests: tests/e2e/test_submission_integration.py

Cả hai report đều ghi question_count=1012, validation.valid=true,
queries_replayed=1012, errors=[] và verification class PARTIAL=1010,
UNRESOLVED=2, VERIFIED=0. Cả hai ZIP đều qua unzip -t.

Control được chạy với loaded builder SHA-256
63a7456ba70372eb2ed03574a25937e3082d3ff7743262394c38eec30210b6bf và loaded
source-first SHA-256
949268f06f271c67538a6ecbcd9f6cf0df6f7116a7590676d77511fabb6062c5.
Variant load cùng hai SHA này và do đó answer-diff comparison là hợp lệ cho
code đã được process import. File builder trên đĩa bị một tiến trình đồng thời
sửa sau khi variant bắt đầu; vì vậy variant report ghi changed_during_build=true.
Đây là lý do cần fingerprint guard và phải rerun một lần nữa dưới filesystem
immutable trước khi promotion/release.

## Score interpretation và bước tiếp theo

Workspace không có gold answers hoặc official local scorer cho 1.012 câu. Các
con số trên chỉ là retrieval/navigation proxy; không gọi chúng là Docs F2,
Tables F2, Answer Accuracy hay Execution Accuracy.

Candidate này đáng giữ để kiểm tra external scorer/human semantic review, với
điều kiện rerun immutable. Score-con tiếp theo cần ưu tiên controlled
cross-entity/temporal source-first cho composed execution và nhóm direct lookup
còn unresolved; không mở rộng neighbor để làm answer authority và không hạ
global semantic threshold.
