# Agent 4 — báo cáo tích hợp E2E V1

Thời điểm xác nhận: `2026-08-27T12:02:45Z` (19:02:45 ICT). Đây là lần kiểm tra độc lập và tích hợp cuối; mọi kết luận dưới đây đều giữ ở lane research-only.

## Quyết định tích hợp

- Git vẫn ở branch `main`, HEAD `6be0c82f7a3bc01eb6b98b7825a4a451ffae2be6`, không ahead/behind `origin/main`. Worktree vốn đã dirty; không `reset`, không checkout, không xóa thay đổi hiện có.
- Đã đọc lại E2E v8 (`configs/e2e/explicit_ticker_candidate_replay_v8.yaml`, SHA-256 `12bf8c81936859eab87173ae8c98f9746eba23e8fbed57709831b9d7ea896cda`) và các manifest V2/V3 trước khi replay.
- Chỉ tích hợp ba candidate có kiểm tra tọa độ/hash V2/V3 độc lập: Q156, Q263, Q340. Q70 giữ nguyên baseline sau replay.
- Q98, Q104, Q185, Q242, Q357 bị giữ trong quarantine. Không candidate nào được nâng thành evidence, answer, release, training hoặc submission.
- `human_verified_count = 0`, `semantic_approval_count = 0`, reviewer inputs rỗng.

## Kiểm tra độc lập artifact Agent 1–3

| Agent | Artifact/manifest đã kiểm tra | Kết quả validator hiện tại | Quyết định |
| --- | --- | --- | --- |
| Agent 1 | `/tmp/source_period_recheck_agent1_v1_20260827-pre-continuation-test-fix/source_period_recheck_agent1.manifest.json`, SHA-256 `6b2d0d698949fe8378c9f2eae915ba0bc76b27f4f35e9704a11c263a04c16dcf` | Không đóng được strict replay: manifest còn trỏ receipt r8 lịch sử `artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-r8/grounded_e2e_run_v1.json` với hash không còn trong workspace. | Q70 kiểm tra lại và giữ nguyên; Q185/Q357 quarantine theo mismatch V2/V3. Không dùng trạng thái ready trong báo cáo Agent 1 làm bằng chứng. |
| Agent 2 | `/tmp/parent_child_duplicate_research_v1_20260827_r1_validated_build/manifest.json`, SHA-256 `dc371f67d5d338eff85201d9f36bceb1110f3ec9cd84dcad9277a9ca4822f2f0` | `PASS`; 21 candidate rows, không failure. | Q340 tích hợp candidate-only sau coordinate/hash và parent-child replay; Q98/Q104 quarantine. |
| Agent 3 | `artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/manifest.json`, SHA-256 `f0b0db97b7b1f861594d0a0d2631b7a5a6113e324087c8ac6a39980f0fabdd89` | `PASS`; Q156/Q263 candidate, Q242 quarantine, `answer_eligible=false`, `submission_eligible=false`. | Q156/Q263 tích hợp candidate-only; Q242 giữ quarantine. |

## Bảng baseline / candidate mới / replay / conflict / lý do

`Baseline` là packet từ `artifacts/main_inputs/period_packets_v1`; `replay` là kết quả exact binding + Decimal execution của r9, không phải quyền trả lời.

| question_id | baseline | candidate mới | replay | conflict | lý do |
| ---: | --- | --- | --- | --- | --- |
| 70 | `unique`, 1 candidate | Không thay đổi; V2 UID `1c24680c0c45dad1428f158ee98c083c30501712e94f0a96546da5c06f9f215c`, row 3 / col 1 | `binding_ready`; `execution_replay_ready` | Không | Regression replay giữ nguyên candidate baseline; không mở thêm quyền. |
| 98 | `packet_blocked`, 0 | Agent 2 blocked, không có cell được chấp nhận | `binding_conflict`; không execution | Có | `BLOCKED_DOCUMENT_PROVENANCE`; thiếu chứng cứ provenance tài liệu để bind. |
| 104 | `packet_blocked`, 0 | Agent 2 blocked, không có cell được chấp nhận | `binding_conflict`; không execution | Có | `BLOCKED_DUPLICATE_PROVENANCE`; duplicate lineage chưa được giải quyết. |
| 156 | `packet_blocked`, 0 | `agent3-q156-v2-v3-replay`, UID `62afef781056139513680d7a69f30ad6faf8c14e1bf3e704e8727d788a4f4ab2`, row 8 / col 4, BVH 2018 | `binding_ready`; `execution_replay_ready` | Không ở exact; evidence vẫn `BLOCKED` | Coordinate/hash/header V2/V3 replay pass. Đây chỉ là candidate; semantic variable/entity và period authorization chưa pass. |
| 185 | `packet_blocked`, 0 | Agent 1 candidate UID `f75f9583841fbe84c999c3c9999d7455ba72f25fa745a02cfeb921dc87c5d4b3`, row 6 / col 2, VIC 2016 | `binding_conflict`; không execution | Có | `V2_V3_HEADER_ANCHORS_MISMATCH`, `EXPECTED_TABLE_TYPE_MISMATCH`; candidate quay về quarantine. |
| 242 | `packet_blocked`, 0 | Agent 3 candidate UID `5e38d87334bf01bef907cad4499fe0c243f2880660fc6d0486016680d6f3e9e3`, row 15 / col 3 | `binding_conflict`; không execution | Có | `HEADER_PROVENANCE_NOT_RECONCILED`, `V3_CANONICAL_HEADER_CONFLICT`; không chọn cột bằng suy đoán. |
| 263 | `packet_blocked`, 0 | `agent3-q263-v2-v3-replay`, UID `fb79b103686c9b347e3672bc740b0c3057bdb9e9f6830f0a00d80474de15fa94`, row 26 / col 2, SSI 2019 | `binding_ready`; `execution_replay_ready` | Không ở exact; evidence vẫn `BLOCKED` | Coordinate/hash/header V2/V3 replay pass. Candidate vẫn thiếu semantic approval và variable/entity evidence. |
| 340 | `packet_blocked`, 0 | `q340-PVT-financial-statements-2019-separate-b76619dd2d23-r12`, UID `b76619dd2d2318bb10f8baf03c4afd2a798b052276e3d1d5ed1c1b6491066521`, row 12 / col 4, PVT 2019 | `binding_ready`; `execution_replay_ready` | Không ở exact; evidence vẫn `BLOCKED` | V2/V3 coordinate/hash và parent-child check pass. Authorization vẫn gặp `BALANCE_SHEET_AS_OF_DATE_NOT_UNIQUE`. |
| 357 | `packet_blocked`, 0 | Agent 1 candidate UID `cfd490b201921eb2fb4bf54c082b1b0b50043fc1046eaf2ebe7c0ed76242217c`, row 20, đề xuất closing col 3 / opening col 4 | `binding_conflict`; không execution | Có | `V2_V3_HEADER_ANCHORS_MISMATCH`, `INHERITED_HEADER_NOT_REFLECTED_IN_CURRENT_V3`; không chấp nhận inherited header chưa có trong V3 hiện hành. |

## Union packet và hash lineage

Union Agent 4: `artifacts/research/agent4_candidate_union_v1_20260827_r2/`.

- packet JSONL SHA-256: `8bd461a50d7a589b674050a4b5b81ee97949f363904d89e42f658dd111c2dd9e`.
- manifest SHA-256: `7856584e49dbde1f8a6f303eb192abbf1fa6b437492aae66fa0899d59c16d046`.
- `integration_before_after_v1.jsonl` SHA-256: `586c8dab943bff16e3986fd487feed9a6a0f352da9b4456a8cb4de5f41907703`.
- `quarantine_ledger_v1.jsonl` SHA-256: `b088b73f190ee4ad714cf56ceff61c1fa93965757e27bf76924ea9661ba7eb5f`.
- Baseline 1,012 câu: `unique=65`, `packet_blocked=931`, `ambiguous=9`, `no_period=7`.
- Sau union: `unique=68`, `packet_blocked=928`, `ambiguous=9`, `no_period=7`; thay thế candidate ở Q156/Q263/Q340; quarantine Q98/Q104/Q185/Q242/Q357.

Input hash chính đã kiểm tra:

- V2 `tables_structured_v2.jsonl`: `583e18bbeac02b484d666eb45f45170b45b92d127ccc4934c213d16c7db4c749`.
- V3 `tables_evidence_context_v3.jsonl`: `e7b69c5ee3f4a7ffd8b1c81034f50021a379372da464f80ad809884be444d472`; manifest: `a9dff3d3aed8423971b0eca591e7d98c6f8984b78de72cd4a7d198c80889bdf1`.
- Base period packets/manifest: `998eaa68d22d432ef414a07313edcfa2e56ff8b7969ed75496f983becb7a86dc` / `dcf759876927475eb85c514e0287050e0684c071c123d6c8850d40183493dcc2`.
- Route overlay/manifest: `9275b1c39c83f19587de139fea00b364e823b176c8b2832b9b3a2e4901445821` / `3d974c9ed2cb73369089d0979faf9937838e136976ab9da20d717f39857dddaa`.

## E2E receipt mới — r9

Receipt: `artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/grounded_e2e_run_v1.json`, SHA-256 `55c38e960ec54867466545fce9c1e18a87c0a4bd7d1459ca3780f78ceec3d4d5`.

- Config r9 SHA-256: `e5e070d292ab8721357697a8bda70b4da1d241cf78c2c1145f62a87bc5634297`.
- `run_id`: `ad8bdec7173b17afe99b89460220944f6204ab05e265700bfcad92bd54306fce`.
- `run_status`: `complete_research_only`.
- Exact bindings JSONL / manifest: `8c4ee6e09547e0d5f747c85ac125446c53032b73f61e1a4dc40dad9e9b53f4d3` / `50cd201525a1a5886ac6294850749a3e50740961e5e8db88fe190726323a46b7`.
- Grounded execution JSONL / manifest: `ccb61012779ea64c96a31e07dc875615fdc04973e4918edee35907a3090112372` / `1e083a239137ea5420bef885c3cd4870269368e66369de485b3e9068672ac408`.
- Exact binding counts: `binding_ready=58`, `binding_blocked=494`, `route_incomplete=460`.
- Execution counts: `execution_replay_ready=58`, `binding_conflict=5`, `route_incomplete=949`.
- CPU Decimal/Pandas replay count: `58`; đây là technical readiness, không phải answer acceptance.

Lưu ý: hai hash trong dòng exact/execution ở trên là hash đầy đủ của artifact được ghi trong receipt; receipt và manifest là nguồn chuẩn nếu cần đối chiếu lại. (Các hash có thể được lấy lại trực tiếp bằng `sha256sum` trên các path nêu trên.)

## Authorization, evidence và release

`authorization_readiness_v1.json` SHA-256 `f32854d1aa594fadd6a075304d8d90e64c7b2ecbe5dc4ca45c543357d415f184` cho kết quả:

- `authorization_status=blocked`.
- `evidence_binding_count=884`, tất cả `BLOCKED`.
- `answer_certificate_status_counts={ABSTAIN: 1012}`.
- `machine_semantic_binding_count=0`, `reviewer_inputs_used=[]`.
- `release_authorized=false`, `submission_compilation_allowed=false`, `independent_audit_required=true`.
- Field status: period `PASS=31`, unresolved `853`; source integrity `PASS=58`, unresolved `826`; unit `PASS=58`, unresolved `826`; scope `PASS=51`, unresolved `833`; entity và variable chưa pass cho evidence.

Semantic hand-off rỗng: `artifacts/research/agent4_semantic_queue_v1_20260827_r1/`.

- queue JSONL SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- blank human decisions JSONL SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- manifest SHA-256: `0d79c7443a521eb68d883f00e9a17264f76386eae68147e7314fed735b582957`.
- `review_item_count=0`, `human_decision_count=0`, `human_verified_count=0`, `eligible_for_authorization=false`.

## Nguyên nhân blocker còn lại

| Blocker | Bằng chứng r9 | Vì sao chưa thể khắc phục bằng dữ liệu hiện tại |
| --- | --- | --- |
| Semantic/authorization gate | 0 human decision; 884 evidence bindings blocked; 1,012 answer certificates abstain | V2/V3 và replay số không thay thế được quyết định semantic độc lập cho variable, entity, scope và kỳ. Không được tự điền `human_verified`. |
| Route chưa hoàn chỉnh | 460 packet `route_incomplete`; 949 execution `route_incomplete` | Route overlay hiện có chưa materialize đủ stage/operand; retrieval score không tạo được exact source contract. |
| Period chưa authorizing | 853 field period unresolved; Q156/Q263 `V3_TABLE_FUNCTION_NOT_PERIOD_AUTHORIZING`; Q340 `BALANCE_SHEET_AS_OF_DATE_NOT_UNIQUE` | Header/cell có thể replay kỹ thuật nhưng chưa đủ tính duy nhất và policy period để authorize evidence. |
| Provenance/lineage | Q98/Q104; r9 có 382 lineage invalid ở các blocker downstream | Tài liệu/duplicate lineage hiện không cung cấp nguồn độc lập hợp lệ; không dùng arithmetic success để bù. |
| Header/table conflict | Q185, Q242, Q357 đều `binding_conflict` | Candidate không khớp canonical V3 header/table type hoặc inherited header chưa có provenance hiện hành. |
| Entity/variable | Evidence field status entity/variable chưa pass | Alias ticker và metric label không phải evidence; cần nguồn văn bản/semantic review đúng thẩm quyền. |

## Kết luận

- **Pipeline đã hoàn thiện:** Có, ở mức deterministic research E2E: union → exact V2 cell binding → Decimal/Pandas execution → authorization receipt đã chạy end-to-end và hash-bound. Chưa hoàn thiện ở mức answer/release/submission.
- **Số câu có thể replay:** `58/1,012` có exact-cell và execution replay kỹ thuật trong r9. Trong đó `0/1,012` được authorization thành answer/evidence hợp lệ.
- **Số câu vẫn phải abstain:** `1,012/1,012` answer certificates là `ABSTAIN`; 5 candidate lỗi vẫn quarantine, các route/semantic blocker khác vẫn fail-closed.
- **Điều kiện trước submission:** phải có exact V2/V3/source lineage ổn định; route và period/scope/unit/entity/variable pass; semantic decisions được người có thẩm quyền kiểm tra và hash-bound; evidence binding độc lập pass; authorization/release gate và submission compilation pass. Không nộp Dashboard và không dùng Kaggle/dense index trong lần recheck CPU này.
