# Pipeline completion handoff V7

Ngày: 2026-08-31  
Phạm vi: hợp nhất remediation feedback theo family, build sạch toàn corpus,
audit value-free và canonical deterministic E2E.

## Kết luận điều hành

Pipeline kỹ thuật đã chạy đủ và các lỗi contract/telemetry đã được sửa. Build
đủ 1.012/1.012 câu, ZIP integrity gate pass, full regression pass, audit r9
pass và canonical E2E pass. Kết quả này xác nhận tính tái lập và fail-closed
của pipeline; chưa phải accuracy vì workspace không có gold/official scorer.

Strict release vẫn bị khóa đúng chính sách. Canonical E2E có 0 strict answer
được authorization, trong khi 978 candidate tồn tại chỉ ở lane
`BEST_EFFORT_CANDIDATE`; không được dùng `answer_available_count` như strict
answer count.

## Các remediation đã hợp nhất

- Period/column: ưu tiên exact requested year; không loại nhầm amount column
  không ghi năm khi cạnh đó có percentage column; phân biệt start/end; nhận
  ngày dạng slash và dạng chữ; ngày bất kỳ không tự suy diễn là period-end; bỏ
  header/navigation row khỏi semantic row context.
- Source-first metric guards: provision row/column, total row/column và
  hierarchy, direction cho vay-vay, cash/term-deposit, related-party
  qualifier, currency/unit và interest-threshold route.
- Authority boundary: candidate được materialize nếu được phép phục vụ
  best-effort, nhưng `ABSTAIN` luôn giữ `answer=null`,
  `answer_decimal=null`, `strict_answer_decimal=null`; candidate tách riêng
  ở `best_effort_candidate_decimal`. Model/reviewer/gold/research metadata
  không cấp authority.
- Receipt consistency: `technical_readiness.answer_authority` không còn
  hard-code `true`; nó phản ánh strict certificate thực tế. Receipt có thêm
  `strict_answer_authority`, `strict_answer_authorized_count` và
  `best_effort_candidate_authority=false`.
- Audit harness: bổ sung `defaultdict`, ghi `summary.json`, phân loại
  `GUARD_REJECTION`/`MISSING_EVIDENCE`/`UPSTREAM_FILTER`/`AUDIT_DIAGNOSTIC`,
  thống kê family/status/reason-class, bắt buộc abstain reason và deny-list
  value-bearing output. Question ID chỉ tracking, không có exception.
- Packaging: builder script được đưa vào wheel data-files; CLI adapter có
  fallback tìm builder trong source checkout hoặc installed wheel.

## Bằng chứng đã kiểm tra

### Regression

```text
605 passed, 1 skipped
132 passed: feedback + authority + period/column + source-first subset
```

Full suite chạy bằng `PYTHONPATH=.:src .venv/bin/pytest -q`; sau khi sửa
`technical_readiness` đã chạy lại và vẫn pass. Các file production liên quan
đã `py_compile` pass và `git diff --check` pass.

### Clean full-corpus build

Snapshot bất biến theo nội dung được dùng để build:

`artifacts/runs/vifinqa_answer_optimization_20260830/semantic_contract_v7_final_snapshot_8Z6WNf`

| Thành phần | SHA-256 |
|---|---|
| builder | `d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8` |
| source-first lookup | `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9` |
| grounded authorization | `190054f0411d40efa3246a726f195958d278624d4b9a33ddf0e7eba7c384c481` |
| canonical E2E pipeline (receipt v10) | `e03c57448057cb5e6c1fd5290ebf8994b2feb3ead6dbfce56217a2887c05d34c` |

Artifact build:

- [build_report.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v8/build_report.json)
- [completion_gate_v1.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v8/completion_gate_v1.json)
- [submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v8.zip)

Gate facts: `question_count=1012`, candidate ledger `978`, fallback `34`,
validation/replay records `1012`, ZIP gồm `1012` evidence CSV files +
`submission.json` có `1012` submission records, duplicate IDs `0`, errors `[]`,
`gate_passed=true`,
`changed_during_build=false`. Đây là best-effort submission artifact; chưa
phải strict VERIFIED/release.

### Canonical deterministic E2E

- Config: [deterministic_replay_v1_local_source_semantic_v7.yaml](../../configs/e2e/deterministic_replay_v1_local_source_semantic_v7.yaml)
- Receipt: [grounded_e2e_run_v1.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_local_source_semantic_v10/grounded_e2e_run_v1.json)
- Run ID: `080e4fc66e020f2ffb95d13dc4f5a9337d583da89de15818eeb1caf59eb4bf8c`

| Chỉ tiêu | Kết quả |
|---|---:|
| câu hỏi | 1.012 |
| certificate `ABSTAIN` | 1.012 |
| strict `answer_count` | 0 |
| `strict_answer_authorized_count` | 0 |
| candidate prediction | 978 |
| execution replay ready | 55 |
| evidence binding blocked | 884 |
| source lineage PASS/BLOCKED | 55 / 829 |
| release authorized | `false` |

Kiểm tra trực tiếp toàn bộ 1.012 certificate: không có giá trị ở
`answer`, `answer_decimal` hoặc `strict_answer_decimal`; 978 certificate có
candidate tách riêng; không có cờ strict/release/submission/training/promotion
nào bật.

## Full-integrated best-effort submission

Theo yêu cầu chạy toàn bộ pipeline, đã kiểm tra thêm variant dùng final builder
snapshot với candidate-validity ranking, bounded research candidate hydration,
route overlay và model-candidate replay. Các sidecar này chỉ bổ sung/rerank
candidate; builder ghi nhận `raw_research_values_used=false`, candidate validity
ghi `may_authorize_answer=false`, và canonical E2E độc lập vẫn fail-closed.

- [Full-integrated build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/control/submission/build_report.json)
- [Full-integrated completion gate](../../artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/control/submission/completion_gate_v1.json)
- [Full-integrated canonical E2E](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_full_integrated_best_effort_v1/grounded_e2e_run_v1.json)
- [Upload-ready ZIP copy](../../submission_ready/vifinqa_full_integrated_best_effort_v1_20260831.zip)

Control variant đạt `1.012/1.012` submission records trong `submission.json`,
đính kèm `1.012` evidence CSV files, `968` candidates, `44` fallback rows,
validation/replay errors `0`, ZIP integrity pass và canonical E2E complete.
E2E vẫn có `answer_count=0`, `strict_answer_authority=false` và
`release_authorized=false`; đây là submission best-effort được cho phép,
không phải strict VERIFIED.
SHA-256 ZIP control:
`b4a05bb9352ea36d3373911e5029a7847e5686ee1f85d2660af83ccd1b6561ca`.

### Family-level subsidiary-investment ablation (bản upload-ready mới nhất)

Variant được chạy lại bằng final builder snapshot, completion gate và
canonical E2E riêng:

- [Variant config](../../configs/e2e/deterministic_replay_v1_subsidiary_investment_best_effort_v1.yaml)
- [Variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/variant/submission/build_report.json)
- [Variant completion gate](../../artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/variant/submission/completion_gate_v1.json)
- [Variant family trace](../../artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/variant/submission/subsidiary_investment_trace_v1.jsonl)
- [Variant canonical E2E](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_subsidiary_investment_best_effort_v1/grounded_e2e_run_v1.json)
- [Upload-ready v2 ZIP](../../submission_ready/vifinqa_full_integrated_subsidiary_investment_best_effort_v2_20260831.zip)

Variant đạt `1.012/1.012` submission records, `971` candidates, `41`
fallback rows, validation/replay errors `0`, ZIP integrity pass. So với
control, route family-level thêm `4` exact source-row candidates; một nhóm
duplicate có câu trả lời mâu thuẫn bị loại. Trace ghi rõ yêu cầu scope công ty
mẹ/separate, một ticker, một năm, exact row, unit và current-period column;
không có Question-ID allowlist.

Canonical E2E của variant vẫn giữ `1.012` certificate ở `ABSTAIN`,
`answer_count=0`, `strict_answer_authorized_count=0`,
`best_effort_candidate_authority=false` và `release_authorized=false`. Đây là
bản best-effort upload-ready có provenance/candidate labels, chưa phải strict
VERIFIED hoặc bằng chứng leaderboard. Run ID E2E:
`ad7814990d3bfc9246fba289decd1f7eb568c7ab31c305c8f6c60a6a53b018d8`.
SHA-256 ZIP v2:
`18e57850637e7efa1ae012eeaab9d6ef24e9cd0fac82984e9693f756f6db6c3a`.

### Independent audit r9

- [r9 summary](../../artifacts/research/independent_qna_review_v1_20260831_r9/summary.json)
- [r9 JSONL](../../artifacts/research/independent_qna_review_v1_20260831_r9/semantic_contract_reviews.jsonl)
- [r1→r9 comparator](../../artifacts/research/semantic_audit_comparison_v1_20260831_r1_to_r9_v6/semantic_audit_comparison.json)

Kết quả: `172/172` record align; `34` abstain, `90` changed, `48` retained;
`0` abstain thiếu reason; `29.405` rejection events; tất cả event trong run
này là `GUARD_REJECTION`; `question_id_exceptions=false`;
`value_free_output=true`; `accuracy_measured=false`; strict verification `0`.

Các rejection counts là event/trace counts, không phải số câu sai. Không có
gold/model/research value được dùng để chọn hoặc nâng cấp đáp án.

### Wheel smoke test

Đã build wheel bằng `pip wheel . --no-deps --no-build-isolation`, cài vào
target directory riêng, chạy `finance-query build-submission --help` thành
công; wheel chứa
`finance_query-0.1.0.data/data/share/finance-query/scripts/e2e/build_competition_submission_v1.py`.

## Blocker còn lại và bước tiếp theo

Blocker không còn nằm ở test/contract mà ở dữ liệu/provenance strict:

- 829 source V2/V3 lineage closure đang `BLOCKED`/thiếu trong authorization
  input;
- 884 binding còn entity/variable unresolved;
- 853 period unresolved;
- 829 unit và source-integrity unresolved;
- 949 route chưa đủ execution; chỉ 55 replay-ready;
- strict answer count vẫn 0.

Muốn nâng candidate lên strict VERIFIED cần bổ sung source certificate đúng
schema cho từng binding, rồi chạy lại independent authorization. Không được
giảm guard, dùng raw full-table asset làm canonical V2, dùng model/reviewer/gold
metadata làm authority, hoặc thêm ngoại lệ theo Question ID.

## Trạng thái các agent/process

- Agent A (period/column): completed, đã đóng.
- Agent B (authority boundary): completed, đã đóng.
- Agent C (source-first guards): completed, đã đóng.
- Agent D (audit contract/review): completed, đã đóng.
- Các build variant cũ và Kaggle poller là process/artifact lịch sử hoặc nền;
  không được dùng thay cho clean final build. Không còn child-agent process
  cần chờ trong task này.
