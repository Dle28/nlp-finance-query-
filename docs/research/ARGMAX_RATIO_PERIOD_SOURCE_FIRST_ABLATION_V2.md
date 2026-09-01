# Argmax ratio-period source-first ablation v2

Ngày: 2026-08-31  
Lane: `FULL_POPULATION_VALIDATED` về mặt execution scope; `CANDIDATE_ONLY` về authority  
Phạm vi: toàn bộ snapshot ViFinQA 1.012 câu

## Hypothesis/family

Các câu hỏi có dạng chọn năm có tỷ trọng cao nhất/thấp nhất giữa nhiều năm
được xem là một family dùng lại được, không phải một tập ngoại lệ theo
`question_id`. Candidate thực hiện cùng một hợp đồng cho family:

1. nhận diện numerator/denominator theo nhãn/ngữ cảnh của family;
2. bắt buộc mỗi operand thuộc đúng năm đang xét và cột hiện tại có header;
3. tính `Decimal(abs(numerator) / abs(denominator))` từ ô nguồn;
4. giữ cùng scope và source multiplier qua toàn bộ các năm;
5. từ chối cặp mơ hồ, tie, thiếu operand hoặc thiếu provenance;
6. chọn một winner duy nhất bằng arg-extreme và replay lại answer trong
   `pandas_query`.

Ba family có source contract độc lập trong snapshot này là:

- `lease_land_cost_share`: KBC, giá vốn cho thuê đất/cơ sở hạ tầng chia cho
  giá vốn hàng bán và dịch vụ cung cấp; scope canonical là consolidated.
- `deposit_interest_expense_share`: HDB, chi phí lãi tiền gửi chia cho tổng
  chi phí lãi; tổng dòng trống phải bằng tổng chi tiết liên tục; scope
  canonical là consolidated.
- `transport_segment_asset_share`: PVT, tài sản bộ phận dịch vụ vận tải chia
  cho tổng tài sản trong cùng block header; câu hỏi nêu công ty mẹ nên scope
  là separate.

Runtime adapter nhận diện theo family/semantic contract. Ba `question_id`
chỉ được dùng ở lớp audit/materialization để chọn các row đã được build và
replay độc lập; chúng không tham gia prediction, routing, retrieval, parsing,
formula selection hay answer selection.

## Experiment contract

### Control fingerprint

- Control population: `1012` rows.
- Control `submission.json` SHA-256:
  `ea47bdc1929eb70523b15a8a1b232074893810fd6ecac103ceb2dd987df42bc6`.
- Control ZIP SHA-256:
  `7604caa04d323ccad96b8678c9db01a07f192fd143f7dc7a5153d39adc62e7a2`.
- Control build report SHA-256:
  `9656f510d8cb9eb729b6e887173090d92645c92acdf82da0634b7067b04fa84f`.
- Control v2 submission is byte-identical to the v1 control submission; the
  v2 A/B snapshot manifest records the reused control explicitly.

Common frozen implementation/input hashes:

- builder: `8badd7ab3a296efaa8f9ed213112b2000a369d6be42c247cb8b42f3ed6a2a928`;
- source-first lookup: `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`;
- candidate-plan selector: `aded34a7aa7e68a6a5dbb57c1dc873b6af33425c59b5a362baf852464e07df29`;
- questions input: `64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0`;
- replay input: `ccb61012779a64c96a31e07dc875615fdc04973e4918edee35907a3090112372`;
- typed operand plans: `212097bf0a892d9cf7ab703b9f690b854c9fbb36ce3cc07ebd73c390c22c0319`;
- structured asset: `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source line map: `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.

### Candidate fingerprint

- Candidate adapter SHA-256:
  `95fb165f4fc1dcb722e7f6fc5bd09b3d249a1e92ed5cddf3d4cca6758c159db2`.
- Independent replay checker SHA-256:
  `f64b14700c61d2bfbaa810008c1b158186b045a9b564455eb13f3cbc7cf1670e`.
- Candidate `submission.json` SHA-256:
  `f5ac6835dcca2fe195a3e76e9eed56fa5b852ee34e876c9d1d124c2315457ab1`.
- Candidate ZIP SHA-256:
  `599eee951c47e2c99d6213b7a4fe0f40d1e546c4f204e7d3b1a888ae37c397a1`.
- Candidate build report SHA-256:
  `d3d4f9fb786fbb34845f73916030c8870f51ede162fa9c9f5a84f21dc15e5af9`.
- Independent replay artifact SHA-256:
  `77760dc5cc659addc48c4155b05a181570aec3d841752d0682657ae9f5b0525e`.

Candidate configuration changed only by enabling the generalized
`program_arg_extreme_ratio_period_v1` adapter. The control and candidate
shared the same input population, corpus, source map, replay, typed plans,
builder, selector, and validation flags.

### Population and split

- Final A/B population: all 1.012 questions from the frozen input manifest.
- Structured corpus searched: 146.246 tables.
- Split policy: discovery identified the reusable family; final candidate and
  control were then run on the complete population. There is no independent
  gold evaluation split available in this workspace.
- No question was removed because it did not belong to the target family;
  all non-target rows were retained in the A/B comparison.

### Scorer/gold

- Gold source: `NOT_AVAILABLE`.
- Official scorer: `NOT_AVAILABLE`.
- Scorer command: not executed because no independent scorer/gold evaluator is
  present in the current snapshot.
- Scorer exit status: `NOT_MEASURED`.

## Mandatory A/B report

`ANSWER_ACCURACY` and `EXECUTION_ACCURACY` below are intentionally not
estimated from answer diffs, Decimal replay, source coordinates, or ZIP
validity.

```text
Hypothesis/family: reusable source-first argmax ratio-period family
Control fingerprint: submission=ea47bdc1929eb70523b15a8a1b232074893810fd6ecac103ceb2dd987df42bc6; zip=7604caa04d323ccad96b8678c9db01a07f192fd143f7dc7a5153d39adc62e7a2
Candidate fingerprint: adapter=95fb165f4fc1dcb722e7f6fc5bd09b3d249a1e92ed5cddf3d4cca6758c159db2; submission=f5ac6835dcca2fe195a3e76e9eed56fa5b852ee34e876c9d1d124c2315457ab1; zip=599eee951c47e2c99d6213b7a4fe0f40d1e546c4f204e7d3b1a888ae37c397a1
Population and split: complete frozen population, 1,012 questions; no gold split available
Scorer/gold: NOT_AVAILABLE; official scorer not executed
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
Improved / regressed / unchanged / unresolved: improved=NOT_MEASURED; regressed=NOT_MEASURED; unchanged=1009 at complete-record JSON level; changed-answer=3; technical unresolved=0; semantic unresolved=NOT_MEASURED
Family-level results: 3 supported ratio families replayed independently; 3 candidate rows changed; 0 non-target row changes; no family accuracy claim
Diagnostic-only metrics: builder validation 1012/1012 records and 1012/1012 queries for both arms; independent replay 3/3 supported family questions; provenance failures=0; ZIP testzip=None
Artifact paths and hashes: see sections below
Decision: KEEP
Authority status: CANDIDATE_ONLY
```

### Population A/B counts

| Measure | Control | Candidate | Delta/interpretation |
|---|---:|---:|---|
| Input questions | 1,012 | 1,012 | same frozen population |
| Submission records | 1,012 | 1,012 | no missing records |
| Builder replay queries | 1,012 | 1,012 | validation only |
| Builder errors | 0 | 0 | technical gate passed |
| Complete-record changes | — | 3 | Q826, Q877, Q982 only |
| Answer-field changes | — | 3 | diagnostic changed answers |
| Prediction-tier changes | — | 3 | target route only |
| `pandas_query` changes | — | 3 | target route only |
| Unchanged complete records | — | 1,009 | no non-target changes |
| Improved answers | — | `NOT_MEASURED` | no gold/scorer |
| Regressed answers | — | `NOT_MEASURED` | no gold/scorer |

There were no missing IDs in either arm and no complete-record differences
outside the three supported ratio-family records. “Unchanged” is a structural
A/B count, not a correctness result.

### Family-level results

| Family | Question | Control answer | Candidate answer | Independent replay winner | Scope | Provenance |
|---|---:|---:|---:|---:|---|---|
| `lease_land_cost_share` | Q826 | `320000000000.0` | `2016.0` | 2016 | consolidated | pass |
| `deposit_interest_expense_share` | Q877 | `16786000000.0` | `2025.0` | 2025 | consolidated | pass |
| `transport_segment_asset_share` | Q982 | `51.0` | `2025.0` | 2025 | separate | pass |

The independent replay recomputed the ratios from current structured-table
cells using Decimal arithmetic and verified the source line map, source file
hashes, table UIDs, report years, column coordinates, units/multipliers, and
unique winner. These are provenance and execution diagnostics only; they do
not establish answer accuracy without gold/scorer.

The first v1 experiment was rejected as a candidate because the unqualified
KBC question fell through to a separate-scope review shortlist. The corrected
family contract makes consolidated the canonical scope for an unqualified
question, and v2 independently confirms Q826 on consolidated tables. The v1
artifact remains historical and was not materialized into the accepted
overlay.

## Diagnostic-only validation

### Full-population builder

- Control report: `validation.valid=true`, `records=1012`,
  `queries_replayed=1012`, `errors=[]`.
- Candidate report: `validation.valid=true`, `records=1012`,
  `queries_replayed=1012`, `errors=[]`.
- Candidate ratio adapter stats:
  - `family_seen_lease_land_cost_share=1`;
  - `family_seen_deposit_interest_expense_share=1`;
  - `family_seen_transport_segment_asset_share=1`;
  - `ratio_accepted=3`.
- Contract gates active: family-level recognition, exact report year per
  operand, header-bound current column, same scope, same multiplier, unique
  winner, tie rejection, and HDB blank-total/detail-sum equality.

### Independent replay

- Protocol: `vifinqa_independent_arg_extreme_ratio_replay_v1`.
- Status: `PASS`.
- Questions in frozen input: 1,012.
- Supported ratio questions: 3.
- Replayed supported questions: 3.
- Failures: 0.
- Official scorer: `NOT_AVAILABLE`.
- `answer_accuracy`: `NOT_MEASURED`.
- `execution_accuracy`: `NOT_MEASURED`.
- Authority: `CANDIDATE_ONLY`; `promotion_allowed=false`.

### Packaging and overlay

The new integrated overlay inherits 69 previously audited replacements and
adds exactly the three ratio-family replacements, for 72 total replacement
records. The materializer copied source numeric values and evidence files; it
invented no new numeric arithmetic. Comparing the final overlay to its base
submission gives exactly Q826/Q877/Q982 and no other changed row.

- Final overlay validator: `valid=true`, `records=1012`,
  `queries_replayed=1012`, `errors=[]`.
- Final overlay ZIP: 1,013 members, including 1,012 evidence CSVs;
  `testzip=None`.
- All 1,012 final overlay evidence CSVs are non-empty.
- The three newly copied evidence files are byte-identical to the candidate
  build files.

## Artifact paths and hashes

### A/B run

- Snapshot manifest: [`argmax_ratio_period_ab_v2/snapshot/AB_INPUT_MANIFEST_V1.md`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/snapshot/AB_INPUT_MANIFEST_V1.md)
- Control submission: [`argmax_ratio_period_ab_v2/control/submission/submission.json`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/control/submission/submission.json)
- Control ZIP: [`argmax_ratio_period_ab_v2/control/submission.zip`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/control/submission.zip)
- Candidate submission: [`argmax_ratio_period_ab_v2/variant/submission/submission.json`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/variant/submission/submission.json)
- Candidate ZIP: [`argmax_ratio_period_ab_v2/variant/submission.zip`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/variant/submission.zip)
- Control report: [`argmax_ratio_period_ab_v2/control/submission/build_report.json`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/control/submission/build_report.json)
- Candidate report: [`argmax_ratio_period_ab_v2/variant/submission/build_report.json`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/variant/submission/build_report.json)
- Candidate trace: [`argmax_ratio_period_ab_v2/variant/submission/arg_extreme_ratio_period_trace_v1.jsonl`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/variant/submission/arg_extreme_ratio_period_trace_v1.jsonl)
- Independent replay: [`argmax_ratio_period_ab_v2/independent_replay_ratio_families_v1.json`](../../artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/independent_replay_ratio_families_v1.json)

### Final materialized candidate overlay

- Overlay directory: [`integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_v2`](../../artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_v2)
- Overlay submission SHA-256:
  `3f0aeb33b2eb6831808a2f3dbcd5c86e288285986871c943f40e75b15a896b5e`.
- Overlay build/route report SHA-256:
  `ab0177fd2ef8b0c45f74f776351341ea09bfeb670634b745fd01a9b873d13108`.
- Overlay ZIP: [`integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_v2.zip`](../../artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_v2.zip)
- Overlay ZIP SHA-256:
  `6518639aa63169ce6b3b99fd85a3b1f3a4c54dcaec5711fd63686a0e8abd142f`.
- Materializer SHA-256 after the packaging-only route contract:
  `fdbae0ac6dab8321743481eb2efcece4aafaa5e001bfb7c5b0549b7c98402037`.

### Verification commands

```bash
/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q tests/research/test_arg_extreme_ratio_variant_v1.py
# 6 passed in 0.12s

/home/dungle/.local/bin/rtk proxy .venv/bin/python -m py_compile \
  scripts/research/run_arg_extreme_ratio_variant_v1.py \
  scripts/research/replay_arg_extreme_ratio_families_v1.py \
  scripts/research/materialize_validated_route_overlay_v1.py
# exit status 0
```

## Decision and authority

Decision: `KEEP` the candidate artifacts for the next independently scored
evaluation. The decision means the generalized route has clean full-population
execution scope, an auditable A/B delta, and independent source replay; it does
not mean that the three answer changes are proven correct.

Authority status: `CANDIDATE_ONLY`. No promotion, strict verification,
leaderboard score claim, or release authorization is permitted until an
independent gold set or official scorer is available and reports answer and
execution accuracy with regressions.
