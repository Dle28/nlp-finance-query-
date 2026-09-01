# Source-bound plan union + metric-row gate V1

Ngày chạy: 2026-08-31 (Asia/Bangkok)

## Kết luận

Đã tạo một candidate submission mới trên toàn bộ 1.012 câu. Candidate này
union các proposal ledger đã freeze, hydrate lại exact source coordinate, replay
AST, rồi áp dụng một gate dùng chung ở cấp family: chỉ với `direct_lookup`,
plan bị loại nếu metric/row binding là `FAIL`; `REVIEW` và `UNKNOWN` vẫn được
giữ trong selector để không biến không chắc chắn thành câu trả lời mới.

Gate không đọc gold, không đọc model output, không dùng score để chọn plan và
không có Question-ID exception. `question_id` chỉ xuất hiện để join/tracking.

Kết quả r4 đạt validation kỹ thuật trên 1.012/1.012, `errors=[]`, ZIP test
pass. So với control có 4 output thay đổi và 1.008 không đổi; correctness
không được đo vì chưa có scorer/gold được bind với đúng r4. Vì vậy đây là
`CANDIDATE_ONLY`, quyết định `INVESTIGATE_FURTHER`, chưa phải accuracy gain,
promotion hay release.

## Hypothesis/family

**Hypothesis:** complete whole-question source-bound plan phải được tạo trước
route priority, và direct lookup chỉ nên thay thế control khi row source thực
sự bind với metric/qualifier của câu hỏi và source-unit metadata không xung
đột.

Đây là một quy tắc ở cấp pattern/failure class, phù hợp với hướng evidence-first
của các nghiên cứu table QA:

- [FinQA (EMNLP 2021)](https://aclanthology.org/2021.emnlp-main.300/) dùng
  reasoning program có gold evidence để xử lý numerical reasoning nhiều bước;
- [TAT-QA (ACL 2021)](https://aclanthology.org/2021.acl-long.254/) kết hợp
  truy hồi cell/span với symbolic reasoning trên table và text;
- [Syllogism-Inspired TableQA (AAAI 2026)](https://ojs.aaai.org/index.php/AAAI/article/view/40792)
  nhấn mạnh evidence-first premises, reasoning path và consistency checks;
- [TIDE: Triples as the Key (ICLR 2025)](https://proceedings.iclr.cc/paper_files/paper/2025/hash/5e50b663324972bb8cc7b5c06a059438-Abstract-Conference.html)
  dùng cấu trúc decomposition/verification để tránh bỏ sót thông tin.

Điểm chuyển hóa vào pipeline là: evidence/plan completeness và semantic
binding phải đứng trước route priority; route priority chỉ còn là tie-break
cuối cùng.

## Mandatory A/B report

```text
Hypothesis/family:
  complete whole-question source-bound plan union plus a direct-lookup
  metric-row binding gate

Control fingerprint:
  3f78a7b3e605d6b8e0a88115b181c420894c6c32af8b5eaf3ef1b0e3091d9c64

Candidate fingerprint:
  8d9cd454bbdf7c9c5ffee2ccdc413a98f2aefd7b498109b1671fc534defc634b

Population and split:
  1,012/1,012; full population; frozen control; no tuning split; 10 candidate
  ledgers, 10,120 ledger rows

Scorer/gold:
  NOT_AVAILABLE for this control/candidate pair; scorer command NOT_RUN;
  scorer exit status NOT_RUN

ANSWER_ACCURACY:
  control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED

EXECUTION_ACCURACY:
  control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED

Improved / regressed / unchanged / unresolved:
  correctness improved=NOT_MEASURED; correctness regressed=NOT_MEASURED;
  output changed=4; output unchanged=1,008; correctness unresolved=NOT_MEASURED

Family-level results:
  output changes are all in direct_lookup; no non-target output change was
  measured; semantic correctness remains NOT_MEASURED

Diagnostic-only metrics:
  candidate rows hydrated=10,120; structurally complete=1,142;
  metric-row gate rejections=821; candidate selections=22;
  control retained=990; selector control retained=93;
  selector abstain/control fallback=897; source closure pass selected=331;
  AST replay pass selected=244

Decision:
  INVESTIGATE_FURTHER

Authority status:
  CANDIDATE_ONLY
```

### Gate effect

The ungated union r3 changed 5 outputs versus control. The gated r4 changed 4;
the fifth change was removed because its current source row carried a
customer-loan qualifier while the question plan requested a State Bank loan.
This is a family-level semantic rejection, not a QID rule. The gate also
changed source selection metadata for two records whose serialized output was
unchanged; those metadata transitions are diagnostic only.

For the four remaining output changes, the independent value-free audit reports
`semantic_status=PASS` and `unit_status=PASS` for each. Two have a sign
transition against the control output; this is explicitly left for gold/scorer
review and is not interpreted as a correction.

## Full-population semantic feedback

The r4 feedback audit produced 1,012 value-free records and a priority queue of
139 records. It uses current structured-table rows and source hashes as
locators, but does not emit answer values, raw cells, gold labels, model output,
or research answers.

| Family | Population | Metric/row PASS | Metric/row FAIL | REVIEW | UNKNOWN | Unit mismatch |
|---|---:|---:|---:|---:|---:|---:|
| `direct_lookup` | 339 | 168 | 68 | 58 | 45 | 9 |
| `temporal_change` | 39 | not assessed | not assessed | not assessed | not assessed | 2 |
| `conditional_analytical` | 111 | not assessed | not assessed | not assessed | not assessed | not assessed |
| `cross_entity_comparison` | 113 | not assessed | not assessed | not assessed | not assessed | not assessed |
| `multi_entity_or_period_aggregation` | 308 | not assessed | not assessed | not assessed | not assessed | not assessed |
| `ratio_or_derived` | 102 | not assessed | not assessed | not assessed | not assessed | not assessed |

`FAIL`, `REVIEW` and `UNKNOWN` are feedback states, not wrong-answer counts.
The next generalization target is the same contract for multi-operand plans:
metric-to-operand edges, qualifier direction, period/column, total/component,
scope/entity and currency/unit must be checked before choosing an AST route.

## External score feedback and lineage

The attached score receipt was resolved to an existing artifact before being
used as feedback:

- `scoring_result (13).zip` SHA-256:
  `bebbe9324bd0095674bd73cce452288453c3f712947eb47aeb10fe06163efa6b`;
- `prediction_result (11).zip` SHA-256:
  `ad0119e6a03b3fee8d1694b3c296d4fd17a76bb988cfc805725361d83fe655d0`;
- embedded `submission.json` SHA-256:
  `9ce027417893cd2c067512559e84853d69a474927b2758a6827b2ab6544dbca1`;
- the embedded submission matches
  `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q72_q921_after_q878_v1/submission.json` byte-for-byte;
- `scores.txt` SHA-256:
  `6c9299803e55543927ec51271a44187baad67e7a8ca14d8f317ebb8bce5d7250`;
- external receipt values: `ANSWER_ACCURACY=0.2174`,
  `EXECUTION_ACCURACY=0.2174`, `TABLES_F2MACRO=0.2934`,
  `DOCS_F2MACRO=0.6999`;
- scorer command and exit status are not present in the ZIP metadata.

This score belongs to q72 lineage, not r4. It is not used to select r4 plans or
to claim a delta for r4. The earlier user-reported `0.2194` is also kept as an
unbound external reference until its exact submission hash and scorer receipt
are available.

## Artifact paths and hashes

### Candidate submission r4

- [A/B report](../../artifacts/research/source_bound_plan_union_v1_20260831_r4/source_bound_plan_union_ab_report_v1.json)
  — SHA-256 `15accfdd015cdab73d57b7e8465dc17d3aeb6aec576bd9064875ade97ddb68fa`;
- [selected ledger](../../artifacts/research/source_bound_plan_union_v1_20260831_r4/source_bound_plan_union_selected_v1.jsonl)
  — SHA-256 `11f4c6bbff1d2baea4b7ee96fbc7f59a1c3c3bcbe664c67fa9823268c7580f28`;
- [submission.json](../../artifacts/research/source_bound_plan_union_v1_20260831_r4/submission.json)
  — SHA-256 `7c93c707297f2246846c283749b694de31623238a47712975f08880b0e7eab70`;
- [submission.zip](../../artifacts/research/source_bound_plan_union_v1_20260831_r4/submission.zip)
  — SHA-256 `ff06f4d8366c5b2cb987fced10cd03f79d1ce6e59e1c7c26639c2a2ba345b662`;
- [manifest](../../artifacts/research/source_bound_plan_union_v1_20260831_r4/manifest.json)
  — SHA-256 `e400f94cd8781681bfd31778dcbfc605768027a8d2ec8e3af94934abe12ca358`.

`submission.zip` contains 1,012 evidence CSV files plus `submission.json`; the
archive test returned `No errors detected in compressed data`.

### Value-free feedback r6

- [feedback A/B report](../../artifacts/research/semantic_binding_feedback_v1_20260831_r6/semantic_binding_feedback_ab_report_v1.json)
  — SHA-256 `4264e4c0a48a012e518709d2a69eaffd9d3373c839217924e46cb69853c79d03`;
- [full feedback JSONL](../../artifacts/research/semantic_binding_feedback_v1_20260831_r6/semantic_binding_feedback_v1.jsonl)
  — SHA-256 `2fc9d4fcf7875adf0350aca5b6d72eadc1b7d8126340dfd85ce48331b89c49f1`;
- [priority queue](../../artifacts/research/semantic_binding_feedback_v1_20260831_r6/semantic_binding_priority_queue_v1.jsonl)
  — SHA-256 `98505e8f22443ea278146f0df372498550c88b9cc639aa2d1855dbec44ac992a`;
- [feedback manifest](../../artifacts/research/semantic_binding_feedback_v1_20260831_r6/manifest.json)
  — SHA-256 `ba52cb9fdf81820277de3b566069c069b980edf7b81b8e31fa26ca4362b9f8d5`.

### Implementation and regression tests

- [source-bound union implementation](../../src/finance_query/research/source_bound_plan_union.py);
- [semantic feedback implementation](../../src/finance_query/research/semantic_binding_feedback.py);
- [union runner](../../scripts/research/run_source_bound_plan_union_v1.py);
- [feedback runner](../../scripts/research/run_semantic_binding_feedback_v1.py);
- [union tests](../../tests/research/test_source_bound_plan_union.py);
- [feedback tests](../../tests/research/test_semantic_binding_feedback.py).

Targeted regression after the gate and matcher fixes:

```text
71 passed in 0.22s
```

## Next queue

1. Run control, ungated r3 and gated r4 through the same official scorer and
   preserve exact submission hashes. Only report `ANSWER_ACCURACY` and
   `EXECUTION_ACCURACY` after that scorer returns the paired result.
2. Review the 139 value-free queue records by family, starting with direct
   lookup row/qualifier conflicts and source-unit mismatches; do not convert
   the queue into QID overrides.
3. Generalize the same source-bound evidence graph to composed/multi-operand
   questions, with a frozen held-out family set and non-target regression
   report.
4. The model-feedback worker remains a separate blocked lane: the compact
   package is prepared, but no local CUDA/weights/Kaggle credential is
   available in this run. Its output cannot authorize r4.
