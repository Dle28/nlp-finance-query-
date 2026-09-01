# Bounded `arg_extreme_period` source-first ablation: STB accrued interest Q989

Date: 2026-08-31  
Status: completed candidate audit; authorized best-effort submission candidate only  
Scope: one explicitly bounded accrued-interest row alias; no generic debt-table relaxation

## Result

Q989 asks which year has the highest end-of-period accrued interest from
customer lending for STB, in million VND, across 2017, 2022 and 2024. The
legacy submission returned `130864302.0`; the bounded source-first route
returns `2017.0`.

The source cells are:

| Year | Internal table UID | Source line | Row / column | Source row label | Raw value (million VND) |
| ---: | --- | ---: | ---: | --- | ---: |
| 2017 | `2132330cc2677f1f38f4850d8a6ba4c33ae3a01bce1f280f31db65ebc8bcd057` | 1554 | 1 / 1 | `Lãi từ cho vay khách hàng (i)` | `22.399.323` |
| 2022 | `595d2481283d87757ea5d2cd7c8c72969793d84e0074bdc4c875fe0528b0db87` | 1678 | 1 / 1 | `Lãi từ cho vay khách hàng (*)` | `3.370.271` |
| 2024 | `04653e89a874343aaa45cf7cd99810786e8b5cabff6e7fc8170efec119d77321` | 2054 | 1 / 1 | `Lãi dự thu từ cho vay khách hàng` | `3.390.704` |

All three tables are STB consolidated period-comparison disclosures. Their
raw source context identifies the same `Các khoản lãi, phí phải thu` section;
the 2017 and 2022 editions use a shorter `Lãi từ cho vay khách hàng` label,
while the 2024 edition says `Lãi dự thu từ cho vay khách hàng`. The exact
headers state `Số cuối năm Triệu đồng` or `31/12/2024 Triệu VND`, so the
comparison is made in one million-VND unit. The unique maximum is 22,399,323
million VND in 2017.

Because the question does not specify consolidated or separate scope, the
independent replay also checks the separate cohort. Its raw vector is
`[22369585, 3375236, 3373306]` million VND, with the same exact row and
current-period column in 2017, 2022 and 2024; it also has a unique maximum at
2017. Thus the answer is scope-invariant. The materialized route keeps the
deterministically preferred consolidated cohort, while the separate replay
acts as an ambiguity gate rather than as a second answer.

The independent replay, including source hashes, row coordinates, headers,
and all checks, is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_accrued_interest_ab_v2/independent_replay_q989_v1.json`.
It reports `status=PASS` and `promotion_allowed=false`.

## Contract and attribution

The alias is accepted only when all of the following hold:

- fixed STB source UID and report year;
- the materialized route's preferred consolidated scope, with a separate-scope
  replay required when the question leaves scope unspecified;
- raw context contains the accrued-interest disclosure `Các khoản lãi, phí
  phải thu`;
- the exact year-specific customer-loan-interest row label is present;
- the first numeric column is the current-year column; and
- the header declares million VND/đồng.

The structured classifier labels all three tables `debt_schedule`. That label is
recorded but is not treated as semantic authorization; the replay requires the
exact raw disclosure context and exact row alias. This prevents a generic
"loan/debt table" rule from admitting unrelated interest or principal rows.

The independent checker source hash is:

`scripts/research/replay_accrued_interest_q989_v1.py`  
SHA-256: `0eb4f37d0e46cd5a9bc1591ba0e1d3271828b62ab97a810dfda63a38f894ded6`

The materializer used for the explicit overlay has SHA-256
`d6ce02887d2a443aab1fec4b4775875c5d42f47b3dd056fe340d9660d7ee4d8b`.

## Same-snapshot A/B

The full-population A/B is under
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_accrued_interest_ab_v2/`.
Both arms use the same temporary implementation snapshot, builder SHA
`8588dc20139f15048ce5d0773b5736488d0f492e6203caee8590eaab4abccb2c`, source
lookup SHA `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`,
candidate-plan-selector SHA
`aded34a7aa7e68a6a5dbb57c1dc873b6af33425c59b5a362baf852464e07df29`, the
146,246-table asset, and the complete source-line map.

Control and variant both passed the delivery gates:

- validation: `valid=true`, `records=1012`, `queries_replayed=1012`,
  `errors=[]`;
- source-line map: 146,246 entries with no missing or extra entries; and
- ZIP integrity: pass for both arms.

The control/variant comparison has the 32 typed-argmax cohort diffs and no
non-argmax attribution claim. Variant telemetry is 43 typed argmax plans seen,
32 accepted, 6 cohort-rejected, 1 unique-extreme tie, 4 unsupported contracts,
and 2 bounded raw-source unit fallbacks. Only Q989 from this run was
independently replayed here; the other accepted IDs remain separate research
candidates. The independent replay checks 6/6 exact source UIDs across the two
scope cohorts, not just the 3 cells copied into the materialized answer.

## Materialized candidate

The one-row overlay was copied onto the validated 67-replacement candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_v1/`

Portable archive:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_v1.zip`

The overlay contains one explicit new replacement, 67 inherited replacements,
and 68 total replacements. An independent JSON-value comparison against the
67-replacement base found exactly Q989 changed:
`130864302.0 -> 2017.0`. Unselected rows are preserved at JSON-value level,
numeric values are copied from the source build, and
`new_numeric_arithmetic_invented=false`.

The resulting candidate validates and replays 1,012/1,012 with `errors=[]`,
contains 1,012 evidence CSV members, passes `unzip -t`, and has ZIP SHA-256
`cc507808a8b0262942c51f968fc4ff76dbf88d72360e86ac5607155821ca1a3a`.
Its route manifest has 68 entries, includes Q989, and does not include Q829.

## Status and next gate

This is an `authorized_best_effort_submission_candidate` only:
`human_verified=false`, `promotion_allowed=false`, and no official score delta
is claimed. The local repository has no held-out gold scorer that can convert
this source replay into an official Answer/Execution score. Python compilation
passed for the independent checker and materializer. Q829 remains quarantined
for its mixed separate/consolidated source scope. The full local regression is
`685 passed, 1 skipped`, and `git diff --check` passes. The next research
should finish the still-running tax-payable family, audit the completed
subsidiary selector artifact, and then select another residual row family only
after its own independent replay.
