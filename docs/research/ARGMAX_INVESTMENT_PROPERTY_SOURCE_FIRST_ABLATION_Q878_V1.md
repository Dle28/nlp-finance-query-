# Bounded `arg_extreme_period` source-first ablation: KBC investment-property factory Q878

Date: 2026-08-31  
Status: completed candidate audit; authorized best-effort submission candidate only  
Scope: one exact KBC consolidated investment-property/factory row family; no generic investment-property or table-kind relaxation

## Result

Q878 asks which of 2015, 2017 and 2019 records the largest year-end carrying
amount for KBC investment property described as a factory. The current
68-replacement overlay has the heuristic answer `100000.0`. In the immutable
same-snapshot A/B, the control returns `-44784398876.0` through the generic
semantic-cell path, while the bounded variant returns the period answer
`2019.0`.

The independent replay checks the exact consolidated KBC source cells below:

| Year | Internal table UID | Source line | Asset row | Closing-value row / column | Raw value (VND) |
| ---: | --- | ---: | ---: | ---: | ---: |
| 2015 | `44772ad6a1109359f03de0a84179228c77247245e04c4eb7c356067e30801914` | 1137 | 1: `Nhà xuống...` (OCR-normalized factory label) | 14 / 1: `Số dư cuối năm` | `20.415.184.100` |
| 2017 | `05c1cfeb23cba4e05ecaa6e78105d18e29f16b0051716afb07adec7f7b7fef5c` | 1024 | 1: `Nhà xưởng...` | 12 / 1: `Số cuối năm` | `134.884.233.798` |
| 2019 | `b04a2dbeea9bb08086ebfc6dee4f94b3da20295a0f8b6b1aac7a179d675e0b8a` | 1110 | 1: `Nhà xưởng...` | 14 / 1: `Số cuối năm` | `432.718.621.923` |

All three records have the same consolidated scope, financial-note/movement-
schedule context, exact `Bất động sản đầu tư` section, and VND multiplier 1.
The selected value is the closing row two rows below the `Giá trị còn lại`
heading within the factory asset block. The maximum is unique at 2019.

The independent replay, including source hashes, coordinates, context checks,
and the uniqueness check, is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_investment_property_ab_v1/independent_replay_q878_v1.json`.
It reports `status=PASS`, `winner_year=2019`, and
`promotion_allowed=false`.

## Contract and implementation boundary

The alias is accepted only when all of the following hold:

- the question is a typed `arg_extreme_period` operation and names the
  investment-property/factory metric;
- the table is a financial note or detail movement schedule;
- raw context contains the exact normalized `Bất động sản đầu tư` section;
- the asset row is the exact normalized factory variant
  `nhà xưởng (bao gồm chi phí phát triển đất và cơ sở hạ tầng)`;
- the value is taken from the closing-year row under `Giá trị còn lại`, not
  from an opening, addition, disposal, depreciation, or unrelated total row;
- the three year records share consolidated scope, table family, and VND
  multiplier; and
- ties, ratios, and multi-row compositions remain rejected by the generic
  contract.

The implementation is a row-aware family alias in
`scripts/research/run_arg_extreme_period_variant_v1.py`. It is deliberately
guarded by exact source context and the carrying-amount hierarchy. The alias
may handle an OCR/header period irregularity only after those exact row and
context checks pass; it does not weaken period or scope checks for generic
tables.

The implementation fingerprints used for this audit are:

| Artifact | SHA-256 |
| --- | --- |
| `scripts/research/run_arg_extreme_period_variant_v1.py` | `e9db96ac37905adac5bdbb8691b4e5e593f96beed9b785bfc6fc55085c7a4997` |
| `scripts/research/replay_investment_property_q878_v1.py` | `8896dc09cc1aa2329d865facb77bb89b9aa15969e1aab7f8a2d818bfeeafa6ed` |
| `scripts/research/materialize_validated_route_overlay_v1.py` | `45b4704f65f33b9b15ea4a48847ec91bb7646d42a5f768e0cd4b09787098a998` |

## Same-snapshot A/B

The immutable full-population A/B is under
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_investment_property_ab_v1/`.
Both arms use the same builder SHA
`8badd7ab3a296efaa8f9ed213112b2000a369d6be42c247cb8b42f3ed6a2a928`,
source-first lookup SHA
`72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`,
candidate-plan-selector SHA
`aded34a7aa7e68a6a5dbb57c1dc873b6af33425c59b5a362baf852464e07df29`, the
same 29,428-table filtered asset, and the same 146,246-entry source-line map.
The snapshot reports `changed_during_build=false`.

Control and variant both passed the full-population delivery gates:

- `question_count=1012`, `predicted_questions=971`, `nonzero_answers=958`;
- validation `valid=true`, `records=1012`, `queries_replayed=1012`,
  `errors=[]`; and
- ZIP integrity passed for both arms, with 1,013 members and 1,012 evidence
  CSVs.

The A/B comparison has 16 full-record/answer/tier diffs:
`[813, 832, 850, 874, 878, 890, 897, 900, 906, 921, 928, 953, 974, 999,
1000, 1008]`. These are an attribution boundary for the full variant, not 16
independent gains from Q878. The materializer therefore selected only Q878.
Variant telemetry records 43 typed argmax plans seen, 16 accepted, 22
cohort-rejected, 1 unique-extreme tie, 3 ratio contracts skipped, 1
multi-row-composition contract skipped, 97 mixed-table-kind rejections, 3
undeclared-unit rejections, 11 period contradictions, and 64 unsafe-table-kind
rejections.

The control ZIP is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_investment_property_ab_v1/control/submission.zip`
with SHA-256
`87823a72b435c2b0bba64f1750bd1b7ce8dd3c44e1f46ab8e69fc77d3fd69956`.
The variant ZIP is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_investment_property_ab_v1/variant/submission.zip`
with SHA-256
`7f09cbb740ccd96acf251eceff6e3e6e0069c01c30a66f1d9967ce10328515a6`.

## Materialized candidate

Exactly one selected replacement was copied from the variant onto the
validated 68-replacement candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_v1/`

Portable archive:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_v1.zip`

The materialized output has 69 total replacements: 68 inherited plus the
explicit Q878 overlay. An independent JSON-value comparison found exactly one
changed ID, Q878, with `100000.0 -> 2019.0`; all 1,011 unselected records are
preserved at JSON-value level. The output route is
`program_arg_extreme_period_v1`, and the evidence CSV contains exactly the
three replayed source UIDs above.

The final output validates and replays 1,012/1,012 with `errors=[]`, contains
1,013 ZIP members / 1,012 evidence CSV members, and passes `ZipFile.testzip()`.
The archive SHA-256 is
`10473060121dc81ecd7298fce3bc447479aeda07c6641c69855be0535b903bbd`.

## Status and next gate

This is an `authorized_best_effort_submission_candidate` only:
`human_verified=false`, `promotion_allowed=false`, and no official score delta
is claimed. The local repository has no held-out gold scorer that can convert
this source replay into an official Answer/Execution score. Focused argmax
regression is `20 passed`, and the changed scripts compile. The next research
should remain family-based: inspect the residual tie/unsupported classes and
only materialize a new row family after its own independent source replay and
single-ID attribution.
