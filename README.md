# AI GURU — ViFinQA financial QA pipeline

AI GURU là tên dự án; ViFinQA là bài toán/corpus mà pipeline xử lý. Kiến trúc
sản phẩm chỉ có **một đường đi**. Hai lệnh công khai là hai vai trò nối tiếp,
không phải hai hệ thống cùng trả lời:

    data/question
      → intake → source closure → typed question
      → retrieval + rerank → bilingual context compiler
      → proposal model → deterministic resolver
      → independent E2E verification → submission compiler
      → submission package → blocked feedback / next-version experiment

Discovery, retrieval và model chỉ được đề xuất đường đi. Exact binding,
semantic checks, allow-listed Decimal replay và Answer Certificate mới quyết
định một proposal có được gọi là VERIFIED hay không.

## Chạy pipeline

Từ một virtualenv Python 3.11 trở lên, cài dependency của repository một lần:

    .venv/bin/python -m pip install -e ".[dev]"

Nếu bật dense retrieval hoặc reranker fine-tuned, cài thêm nhóm tùy chọn:

    .venv/bin/python -m pip install -e ".[retrieval]"

Tạo prediction best-effort cho toàn bộ câu hỏi:

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli build-submission \
      --output submissions/vifinqa_primary_integrated_YYYYMMDD_rN

Chạy kiểm chứng canonical, hash-bound:

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-e2e \
      --config configs/e2e/deterministic_replay_v1_locked.yaml \
      --output-dir artifacts/runs/e2e_YYYYMMDD

Muốn chạy đúng product path một lần, dùng `run-submission-flow`. Trong giai
đoạn migration, builder cũ chỉ là compatibility producer; hand-off canonical
là `ProposalAST → ResolvedPrediction → E2EReceipt → SubmissionLedger`. Chỉ
certificate hoàn chỉnh, khớp cùng question và answer, mới nâng proposal thành
VERIFIED.

Ví dụ chạy một flow đầy đủ có kiểm chứng và integrity audit:

    PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-submission-flow \
      --output artifacts/runs/submission_flow_YYYYMMDD_rN \
      --verification-config configs/e2e/deterministic_replay_v1_locked.yaml \
      --expected-question-count 1012 \
      --require-full-population \
      --flow-release-policy best_effort

Flow này tạo `pipeline_integrity_audit_v1.json` và
`submission_flow.manifest.json`. Nếu chỉ muốn chuẩn bị candidate mà chưa chạy
E2E, phải dùng rõ `--skip-e2e`; kết quả đó chỉ là `CANDIDATE_ONLY`.

## Cách đọc kết quả

- VERIFIED: có complete canonical E2E certificate khớp proposal.
- PARTIAL hoặc UNRESOLVED: có prediction/candidate nhưng proof còn thiếu
  hoặc mâu thuẫn; có thể giữ cho competition coverage, không được release.
- REJECTED: proposal bị loại; chọn candidate khác hoặc fallback policy.
- E2E status=ABSTAIN có thể vẫn mang answer_status=PREDICTED_CANDIDATE.
  Đây là số dự đoán chưa được authorize, không phải verified answer.

Release luôn cần full-population audit và ledger độc lập. Không suy ra release
readiness từ score, routing metadata, tên file, một tọa độ đúng hoặc phép tính
Decimal chạy thành công.

## Điểm vào tài liệu

- [Architecture overview](ARCHITECTURE.md)
- [Pipeline contract và runbook](docs/PIPELINE.md)
- [Artifact registry](docs/ARTIFACTS.md)
- [Vận hành E2E](docs/e2e/README_VI.md)
- [Research sidecars](docs/research/README_VI.md)
- [Canonical pipeline diagram](docs/diagrams/aiguru_pipeline_canonical_resolve_e2e_compile_v2.html)
- [Diagram index và legacy policy](docs/diagrams/README.md)

## Layout

    src/finance_query/e2e/       pipeline canonical và verification primitives
    src/finance_query/research/ sidecars discovery/diagnostic, non-authorizing
    scripts/e2e/                 operator utilities và submission adapter
    scripts/research/            research producers, không cấp authority
    configs/e2e/                 input closure/config vận hành hash-bound
    configs/research/            cấu hình thí nghiệm và candidate producers
    tests/e2e/                   contract và submission tests
    tests/research/              tests cách ly cho research

Mọi output run/submission hoàn tất phải ghi vào thư mục mới; không ghi đè
artifact lịch sử.
