# Argmax tax-paid source-first ablation V1

Status: accepted local best-effort candidate extension; not an official
leaderboard score.

## 1. Objective

Q879 asks:

> Với công ty mẹ VNM, năm nào trong các mốc 2019, 2022, 2023, 2024 và 2025
> ghi nhận giá trị thuế thu nhập doanh nghiệp đã nộp cao nhất?

The existing typed plan already identified a single-metric period argmax, but
its metric hint was the grammatical wrapper `giá trị thuế thu nhập doanh
nghiệp đã nộp`. The strict period adapter could not recover the exact source row
from that hint. In addition, the 2024 and 2025 source rows were reconstructed
as `financial_data_schedule` records even though their source context is an
operating cash-flow statement.

This ablation adds two bounded accounting-family rules:

1. the question phrase `giá trị thuế thu nhập doanh nghiệp đã nộp` may generate
   the exact canonical row alias `Thuế thu nhập doanh nghiệp đã nộp`; and
2. a generic schedule is treated as a cash-flow statement only when it has the
   exact tax-paid row and its source title contains both `lưu chuyển tiền` and
   `hoạt động kinh doanh`.

The route still reads every numeric value from the frozen structured-table
coordinate. It does not copy a value from retrieval metadata, invent a value,
or promote a proposal to a strict certificate.

## 2. Fail-closed contract

The route is enabled only when all of the following hold:

- the typed plan is `arg_extreme_period` with one ticker, five explicit years,
  `max` direction, and explicit `separate` scope;
- every year binds to the exact VNM row label
  `Thuế thu nhập doanh nghiệp đã nộp`;
- every source table has the requested report year, ticker and scope;
- all five tables collapse to one supported comparison kind;
- all five tables expose the same source-declared multiplier (`1` here);
- the 2024/2025 generic schedules pass the exact row plus cash-flow-context
  gate; and
- the comparison has one unique signed numeric maximum. Ties remain rejected.

The comparison is signed. Parenthesized cash-flow values are negative, so
“cao nhất” means the numerically largest value, not the largest absolute cash
outflow. The source values therefore select 2025. If a gold annotation instead
defines “cao nhất” as the greatest payment magnitude, that is a distinct
semantic policy and must be evaluated separately; this artifact does not
silently switch to absolute value.

## 3. Frozen inputs and implementation

Both full-corpus arms used the same persisted inputs:

| Input | SHA-256 |
|---|---|
| builder snapshot `snapshot/scripts/e2e/build_competition_submission_v1.py` | `650044f7045b5a42f01b08680bf26dfe5233b625a01b063cde6302d18a18f869` |
| strict adapter snapshot `snapshot/scripts/research/run_arg_extreme_period_variant_v1.py` | `90c4d4fd574934722f2e0cb54be2b501da2d396f0b9e743f9c6d97f12c078426` |
| loaded source-first lookup | `23805052c5893c6032eba8b8126b743cd92c7071fcbe16f8b3e6a5dbde66290b` |
| full structured table asset, `146,246` records | `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7` |
| complete source-line map, `146,246` entries | `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615` |

The A/B input manifest is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/snapshot/AB_INPUT_MANIFEST_V1.md`.
It pins the question, typed-plan, replay, candidate, overlay, model-answer,
direct-evidence, calibrator, asset and source-map hashes.

The full A/B artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/`.
The control disables the period-extreme adapter. The variant runs the strict
adapter against the same complete asset and keeps the other source-first
families disabled for attribution.

## 4. A/B result

The control and variant each contain 1,012 records, replay all 1,012
questions, and report `valid=true` with `errors=[]`. The variant saw 43 typed
argmax plans and accepted 24: the 23 previously validated strict argmax
cohorts plus Q879. The expected full diff against the control is therefore the
following 24-ID set:

`Q813, Q832, Q841, Q850, Q860, Q874, Q876, Q879, Q890, Q897, Q904, Q906,
Q910, Q929, Q933, Q936, Q946, Q948, Q953, Q974, Q981, Q997, Q999, Q1000`.

The incremental route-local paired check used the same current builder and
the same five source tables. With the new alias and schedule-context gate
removed, Q879 returned `REJECTED_NO_COHERENT_ROW_FAMILY`. With the patch, it
returned `ACCEPTED`, answer `2025`, and tier
`program_arg_extreme_period_v1`.

The serialized Q879 records are:

| Arm | Answer | Tier | Relevant source coverage |
|---|---:|---|---|
| control | `252342102502.0` | `semantic_cell_heuristic` | mixed/incorrect semantic fallback |
| variant | `2025.0` | `program_arg_extreme_period_v1` | all five requested years |

The control value is retained only as the A/B baseline; it is not used as
evidence for the answer.

## 5. Independent source replay for Q879

The exact source row, zero-based coordinate and signed Decimal replay are:

| Year | Table kind | Table UID | Row | Col | Raw source value | Replayed value |
|---:|---|---|---:|---:|---:|---:|
| 2019 | `cash_flow_statement` | `96150016c12758de5300e6499885823c241c9887a67dfd101fbebf8e2ca06a44` | 17 | 3 | `(2.025.224.469.158)` | `-2025224469158` |
| 2022 | `cash_flow_statement` | `fd735fecfd2d77e42bba6ce7c06dfeb07a114f012e4d82dee56317aaaf6e40bf` | 18 | 3 | `(1.903.065.886.321)` | `-1903065886321` |
| 2023 | `cash_flow_statement` | `fbb912d788f331b89224ebb4ec104f9f0cf083c9c9f03fc0f104450b18f65448` | 15 | 3 | `(1.441.600.595.087)` | `-1441600595087` |
| 2024 | `financial_data_schedule` accepted by exact context gate | `dcd22b200f6f15c0939bf94556b282a7dddc7d5ca5ea6d50dc387b5ecb941405` | 13 | 3 | `(1.997.458.922.345)` | `-1997458922345` |
| 2025 | `financial_data_schedule` accepted by exact context gate | `0019db6262b16708747cba98c473b1147f7e94a366ec0733171ddb558064bb4c` | 12 | 3 | `(1.353.040.369.936)` | `-1353040369936` |

All five rows have the exact normalized label
`thue thu nhap doanh nghiep da nop`, source multiplier `1`, VNM ticker and
separate scope. Independent parsing reproduced the vector

`[-2025224469158, -1903065886321, -1441600595087, -1997458922345, -1353040369936]`

and its unique signed maximum is the 2025 value.

The variant trace is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/variant/submission/arg_extreme_period_trace_v1.jsonl`.
The emitted evidence is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/variant/submission/data/q0879_evidence.csv`.

## 6. Integrity and tests

Control ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/control/submission.zip`

SHA-256:
`3992ca6adbdd4368b6334bfd4286562192d73813527ac5f534e2848c51c74c01`.

Variant ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/variant/submission.zip`

SHA-256:
`a745da563d591ec76386948903eec6516782cfeeef8b216db88a838b13650bc1`.

Both passed `unzip -t`, full submission validation, 1,012/1,012 replay and
complete source-line-map coverage (`146,246` entries, no missing or extra
entries).

The focused adapter tests passed `9 passed`. The current workspace regression
after the route/materializer changes passed `584 passed, 1 skipped`.

## 7. Materialized candidate and decision

Q879 was materialized as one explicit replacement on top of the prior
45-replacement overlay:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_v1/`

ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_v1.zip`

The materialized artifact records `replacement_count=1`,
`inherited_replacement_count=45`, `total_replacement_count=46`,
`new_numeric_arithmetic_invented=false`, `human_verified=false` and
`promotion_allowed=false`. Its ZIP SHA-256 is
`63a43ad052871094d898ee6ee6feb1f0f8ff1da658dbd426b77b20d346a42f88`.
It contains 1,012 CSV members plus `submission.json`, and passes full
validation and `unzip -t`.

Decision: retain this narrow route in the authorized best-effort candidate
lane. It is a locally source-replayed prediction change, not a claim of
`Answer Accuracy +1`, `Execution Accuracy +1`, or an official score delta.
There is still no matching gold answer/scorer, human semantic approval,
complete strict certificate or Kaggle submission in the workspace. If an
authoritative scorer becomes available, score the 46-row overlay and retain
the signed-vs-absolute semantic policy as an explicit audit dimension.
