# Feedback remediation review V1

Ngày rà soát: 2026-08-31  
Phạm vi remediation đã hợp nhất: semantic period/column, source-first
row/metric/total/entity guards, strict-vs-best-effort authorization boundary,
audit harness và regression tests. Các agent chỉ sửa theo write-set disjoint;
không thêm Q-ID exception, không đọc gold/model/research answer để cấp quyền,
và không nới source authority.

## Kết luận ngắn

Các lỗi feedback trong phạm vi contract và các family guard đã được xử lý và
đã có regression/full-suite evidence. Tuy nhiên đây chưa phải là tuyên bố
accuracy hoặc strict release hoàn tất: provenance/source certificate toàn
corpus vẫn còn thiếu, nên strict answer/release gate vẫn fail-closed.

Điểm đã có trong contract mới:

- `GUARD_REJECTION` được tách khỏi `MISSING_EVIDENCE`;
- counts được tổng hợp theo `family`, `status`, rejection class và reason code;
- mọi `SEMANTIC_ABSTAIN` phải có `abstain_reason_codes`;
- audit phải nhận explicit builder snapshot và ghi SHA-256;
- Question ID chỉ là khóa tracking, không được tạo exception/override;
- feedback output không được chứa answer/value/gold/model/research payload;
- tất cả số liệu được gắn là candidate/coverage/diagnostic, không phải accuracy.

Artifact r8 là receipt legacy của schema cũ và không còn được dùng làm kết quả
final. Lỗi runtime `NameError: name 'defaultdict' is not defined` đã được sửa;
audit r9 chạy lại bằng builder snapshot explicit và ghi output value-free.
Tách biệt quan trọng: audit r9 xác nhận contract/telemetry, không xác nhận
đáp án đúng.

## Cập nhật xác nhận sau khi rerun

Audit r9: 172/172 câu registry-clean được align; 34 `SEMANTIC_ABSTAIN`, 90
`SEMANTIC_CANDIDATE_CHANGED`, 48 `SEMANTIC_CANDIDATE_RETAINED`; 0 abstain
thiếu `abstain_reason_codes`; 29.405 rejection events và toàn bộ hiện được
phân loại `GUARD_REJECTION`. Output được kiểm tra value-free, không đưa
answer/value/gold/model/research payload vào receipt; `accuracy_measured=false`,
`strict_verification_count=0`, `question_id_exceptions=false`.

Builder snapshot r9:
`artifacts/runs/vifinqa_answer_optimization_20260830/semantic_contract_v7_final_snapshot_8Z6WNf/scripts/e2e/build_competition_submission_v1.py`,
SHA-256 `d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8`.
Audit đã ghi hash để pin lineage; cờ `filesystem_immutability_proven` vẫn là
`false`, vì hash không tự chứng minh filesystem bất biến.

Các số liệu r9 là event/locator/status telemetry. Một câu có thể có nhiều
reason; không cộng reason counts để suy ra số câu sai hay accuracy.

## Artifact hiện có được dùng để review

| Artifact | Vai trò | Quan sát | Cách diễn giải đúng |
|---|---|---|---|
| `artifacts/research/independent_qna_review_v1_20260830_r8/summary.json` | audit độc lập r8 | 172 câu, 6.610 candidate-table UID, hydrate 6.610 UID, quét 146.246 dòng; 36 `SEMANTIC_ABSTAIN`, 102 `SEMANTIC_CANDIDATE_CHANGED`, 34 `SEMANTIC_CANDIDATE_RETAINED` | coverage/status/semantic telemetry trên subset, không phải accuracy |
| `.../r8/semantic_contract_reviews.jsonl` | receipt từng câu r8 | 172/172 dòng có `baseline_candidate_answer_decimal`; 172/172 có `selected`; 136 dòng có `selected.raw_value`; 36 abstain không có field `abstain_reason_codes` | legacy answer-bearing receipt, không đạt value-free feedback contract |
| `artifacts/research/semantic_audit_comparison_v1_20260830_r1_to_r8_v5/semantic_audit_comparison.json` | comparator r1→r8 | 172/172 matched; 36 abstain; 102 changed; 34 retained; 138 locator changed; 34 unchanged; unknown reason count 0 | status/locator transition telemetry; chưa đủ class-level feedback vì artifact cũ chưa có rejection-class fields |
| `artifacts/runs/vifinqa_answer_optimization_20260830/semantic_contract_v6_code_snapshot_wmXRBP/scripts/e2e/build_competition_submission_v1.py` | builder snapshot được r8 ghi nhận | path snapshot có trong `r8/summary.json` | lineage input; path/hash không tự chứng minh semantic correctness |

Hai con số sau trong receipt cũ cần được cách ly khỏi mọi báo cáo chất lượng:
`answer_decimal_equal_to_baseline=38` và `selected.raw_value`. Chúng không được
dùng làm accuracy, precision, correctness hay improvement. Feedback report mới
chỉ được dùng locator, status, family và reason metadata.

## Đối chiếu từng yêu cầu remediation

| Yêu cầu | Trạng thái | Bằng chứng/giới hạn |
|---|---|---|
| Tách rejection do semantic guard | Đã có trong contract, chưa rerun full artifact | class `GUARD_REJECTION`, fixture có một event `semantic_contract` |
| Tách blocker thiếu evidence | Đã có trong contract, chưa rerun full artifact | class `MISSING_EVIDENCE`, reason `NO_EVIDENCE_WINDOW`/`EVIDENCE_NOT_HYDRATED` |
| Counts theo family/status | Đã có trong contract và test fixture | audit có `family_status_counts`; comparator có `family_level` và status counts; r8/v5 lịch sử chỉ có family fallback `direct_lookup=172`, nên không xem là family coverage đầy đủ |
| Mỗi abstain có reason | Contract bắt buộc, artifact cũ chưa đạt | r8 có 36 abstain nhưng 0 dedicated `abstain_reason_codes`; lần chạy mới sẽ fail-closed nếu thiếu |
| Không gọi là accuracy | Đã khóa trong policy/report | `accuracy_measured=false`; không có gold/official scorer trong snapshot |
| Không answer/value leakage | Contract có deny-list và test sentinel | r8 legacy vẫn leak field; không được dùng r8 làm final value-free report |
| Không gold/model/research input | Test kiểm tra sentinel và sidecar stripping | output chỉ giữ metadata; không có claim về chất lượng model/research |
| Không Question-ID exception | Contract reject override keys và test | Question ID chỉ dùng để align/tracking |
| Audit từ builder snapshot | Contract yêu cầu `--builder-path`, ghi SHA-256 và test | hash là lineage receipt; filesystem immutability vẫn chưa được chứng minh |

## Phân biệt loại feedback

Feedback report phải dùng bảng phân loại sau, không trộn các loại lỗi:

| Class | Ý nghĩa | Không được kết luận |
|---|---|---|
| `GUARD_REJECTION` | Candidate đã có table/evidence window được hydrate nhưng bị semantic contract loại vì row, metric, period, column, entity, scope hoặc unit conflict | không gọi là thiếu dữ liệu; cũng không gọi candidate khác là đúng |
| `MISSING_EVIDENCE` | Không có evidence window usable, UID không hydrate được, hoặc structured source không hiện diện trong closure đang audit | không gọi là semantic guard đã chứng minh candidate sai |
| `UPSTREAM_FILTER` | Candidate bị filter upstream loại trước semantic-cell guard | không quy trách nhiệm cho semantic guard |
| `AUDIT_DIAGNOSTIC` | Harness phát hiện không có surviving cell nhưng không có event upstream tương ứng | không gọi là answer-selection defect nếu chưa có source evidence |

Một câu có thể có nhiều rejection event và nhiều reason code. Counts event và
counts câu hỏi phải được phân biệt: `question_count` là số câu; rejection-class
count là số event/trace. Vì vậy không cộng các class count để suy ra số câu sai.

## Báo cáo hiện có theo family/status

Receipt v5 hiện xác nhận alignment và status như sau:

| Status sau audit | Số câu |
|---|---:|
| `SEMANTIC_ABSTAIN` | 36 |
| `SEMANTIC_CANDIDATE_CHANGED` | 102 |
| `SEMANTIC_CANDIDATE_RETAINED` | 34 |
| Tổng | 172 |

Transition từ r1:

| Transition | Số câu |
|---|---:|
| `BECAME_ABSTAIN` | 36 |
| `MOVED_TO_SEMANTIC_CHANGED` | 102 |
| `MOVED_TO_SEMANTIC_RETAINED` | 34 |

Artifact v5 chỉ tạo được một bucket `direct_lookup=172` khi không có family
field đầy đủ trong receipt. Đây là limitation của receipt cũ, không phải bằng
chứng rằng toàn bộ 172 câu có cùng family. Contract mới lấy family từ
`question_plan.family` trong audit item và xuất `family_status_counts`; cần
rerun sau khi unblock import để tạo báo cáo family thật.

Các reason code nổi bật trong r8 là feedback ưu tiên, không phải lỗi đã được
đếm thành accuracy:

- row/period: `ROW_PERIOD_YEAR_MISMATCH=126`,
  `ROW_PRIOR_PERIOD_SELECTED_FOR_REQUESTED_YEAR=117`,
  `ROW_PERIOD_START_SELECTED_FOR_END_QUERY=100`;
- column/period: `COLUMN_PERIOD_YEAR_MISMATCH=90`,
  `COLUMN_PERIOD_START_SELECTED_FOR_END_QUERY=67`;
- metric/row: `PROVISION_ROW_CONFLICT=154`,
  `PERCENTAGE_CELL_FOR_AMOUNT_QUERY=134`,
  `STOCK_QUERY_FLOW_ROW_CONFLICT=66`;
- aggregate binding: `TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING=50`,
  `TOTAL_COMPONENT_ROW_NOT_SELECTED_AS_TOTAL=46`,
  `TOTAL_ROW_NOT_BOUND=24`;
- entity/scope/currency: `UNQUALIFIED_RELATED_PARTY_TABLE=91`,
  `ENTITY_TABLE_TICKER_MISMATCH=6`,
  `FOREIGN_DOMESTIC_CURRENCY_CONFLICT=4`,
  `FOREIGN_CURRENCY_TO_VND_CONVERSION_MISSING=3`.

Các số trên là số lần reason xuất hiện trong candidate rejection trace của
172 câu; một câu có thể góp vào nhiều reason. Chúng dùng để ưu tiên family
source/provenance review, không dùng để tuyên bố candidate sai hoặc accuracy
tăng/giảm.

## Value-free và anti-leakage contract

Feedback harness được kiểm tra theo ba tầng:

1. Input có thể chứa candidate packet để navigation/hydration, nhưng item đưa
   vào audit selector được lọc bỏ model answer, research answer, gold answer,
   candidate answer và research coordinate hints.
2. Output per-question chỉ có `question_id`, `family`, status, locator,
   rejection class, reason code và policy metadata. Không xuất `selected`,
   `value`, `raw_value`, baseline answer hoặc answer certificate.
3. Comparator chỉ đọc named metadata fields; nó không traverses arbitrary
   nested payload để tìm câu trả lời và fail-closed nếu output có forbidden
   answer/value key.

`research_or_model_inputs_consumed=false`, `gold_answer_available_locally=false`
và `accuracy_measured=false` là policy receipts; chúng không phải dữ liệu trả
lời. Không có hidden gold hoặc official scorer trong các artifact được dùng cho
review này.

## Regression test đã thêm

File mới:

- `tests/e2e/test_feedback_audit_contract.py`

Test fixture bao phủ:

- guard rejection và missing evidence trong cùng một câu nhưng khác class;
- `family_status_counts` và rejection class counts;
- `abstain_reason_codes` không được rỗng;
- sentinel answer/raw/model/research/gold không được xuất ra;
- explicit builder snapshot path và SHA-256;
- từ chối `question_id_overrides`;
- comparator không gọi metadata transition là accuracy;
- comparator fail-closed với output có key `value`.

Test dùng temporary fake builder snapshot, không sửa answer-selection code và
không ghi artifact nghiên cứu thật.

## Kết quả chạy kiểm tra

Đã chạy compile:

```text
.venv/bin/python -m py_compile \
  scripts/research/run_semantic_contract_audit_v1.py \
  scripts/research/compare_semantic_contract_audits_v1.py \
  tests/e2e/test_feedback_audit_contract.py
```

Compile pass.

Targeted pytest sau remediation:

```text
132 passed
```

Full repository regression sau khi hợp nhất các write-set:

```text
605 passed, 1 skipped
```

Audit integrated fixture hiện pass `4 passed`; import `defaultdict` và việc ghi
`summary.json` đã được khắc phục. Audit r9 và comparator r1→r9 đều xác nhận
`value_free_output=true`, `accuracy_measured=false` và không có Question-ID
exception. Các count r9 không phải accuracy.

## Việc cần làm tiếp theo

1. Hoàn tất clean full-corpus build 1.012 câu từ snapshot cuối và chạy
   completion gate; nếu gate pass thì chạy canonical `run-e2e` với ledger mới.
2. Giữ strict answer/release gate đóng cho tới khi từng candidate có source
   certificate đầy đủ và được authorization độc lập.
3. Chỉ khi có gold/official scorer độc lập mới được tạo một report accuracy.
   Trước điều kiện đó, mọi câu `SEMANTIC_CANDIDATE_*` vẫn là candidate/feedback
   metadata và mọi `SEMANTIC_ABSTAIN` phải giữ fail-closed.

## Artifact tham chiếu

- [r8 summary](../../artifacts/research/independent_qna_review_v1_20260830_r8/summary.json)
- [r8 per-question receipt](../../artifacts/research/independent_qna_review_v1_20260830_r8/semantic_contract_reviews.jsonl)
- [r8 TSV](../../artifacts/research/independent_qna_review_v1_20260830_r8/semantic_contract_reviews.tsv)
- [r1-to-r8 comparator](../../artifacts/research/semantic_audit_comparison_v1_20260830_r1_to_r8_v5/semantic_audit_comparison.json)
- [r9 value-free summary](../../artifacts/research/independent_qna_review_v1_20260831_r9/summary.json)
- [r9 per-question receipt](../../artifacts/research/independent_qna_review_v1_20260831_r9/semantic_contract_reviews.jsonl)
- [r1-to-r9 comparator](../../artifacts/research/semantic_audit_comparison_v1_20260831_r1_to_r9_v6/semantic_audit_comparison.json)
- [audit runner](../../scripts/research/run_semantic_contract_audit_v1.py)
- [audit comparator](../../scripts/research/compare_semantic_contract_audits_v1.py)
- [feedback regression test](../../tests/e2e/test_feedback_audit_contract.py)

### Final status

`FEEDBACK_CONTRACT`: **PASS — contract, targeted/full regression, integrated
audit r9 and comparator r1→r9 pass**.  
`VALUE_FREE_FINAL_ARTIFACT`: **PASS — r9 output is value-free; r8 remains a
legacy historical artifact and must not be used as final feedback**.  
`ACCURACY`: **NOT MEASURED — no gold/official scorer**.  
`STRICT_RELEASE`: **BLOCKED/FAIL-CLOSED — source certificate and independent
authorization are not complete for the full corpus**.  
`ANSWER_SELECTION_CODE_CHANGED_IN_THIS_REVIEW`: **YES — only family-level
guards and explicit best-effort/strict boundary; no Q-ID patch**.
