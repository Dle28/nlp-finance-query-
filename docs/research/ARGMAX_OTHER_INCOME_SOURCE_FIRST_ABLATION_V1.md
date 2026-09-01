# Bounded `arg_extreme_period` source-first ablation: ASM other income Q822

Date: 2026-08-31  
Status: completed candidate audit; authorized best-effort submission candidate only  
Scope: one explicitly bounded accounting-row family; no global table-kind or unit fallback

## Result

Q822 asks which year has the highest `Thu nhập khác` for ASM in parent/separate
reports across 2016, 2021, 2022 and 2024. The legacy submission returned
`30476603104.0`, while the bounded source-first argmax route returns the year
`2021.0`.

The candidate is supported by four exact source cells:

| Year | Internal table UID | Source line | Row / column | Raw value | Replayed value |
| ---: | --- | ---: | ---: | ---: | ---: |
| 2016 | `e3274a1dd38e92184fb11cebf01203117d28070d0210a73eca55b8ccaa0cae93` | 366 | 12 / 3 | `3.188.673.987` | `3188673987` |
| 2021 | `c8ce46c8081bc9c89f4c8707c06f3bd3e4918871823f2c0d8b17c6c58af0d47a` | 408 | 12 / 3 | `180.044.077.757` | `180044077757` |
| 2022 | `eef4021e58ec9e41d2ce071b17860bc93870a8882a50fd083ec10a08de7a1ca1` | 353 | 12 / 3 | `3.734.568.921` | `3734568921` |
| 2024 | `d4b8fe40b671e47d1710c6e53494c9400607b827cfd94e26ced634f67b46648e` | 421 | 12 / 3 | `2.401.123.931` | `2401123931` |

The independent replay found the exact code-31 row (`Thu nhập khác`) in the
same `income_statement` table kind, the same separate scope, and the same VND
multiplier for all four years. The maximum is unique at 2021. The complete
independent result, including source hashes and all checks, is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_other_income_ab_v1/independent_replay_q822_v1.json`.

## Contract and attribution

The change is intentionally narrow. It accepts an argmax candidate only when
the normalized table is an income statement, the source row is code 31 /
`Thu nhập khác`, the question's source context is parent/separate, and the
source title identifies a separate report. When the structured table does not
carry a usable unit declaration, the wrapper may inspect a bounded prefix of
the exact source file, verify the source hash, normalize the OCR `√` variant of
VND, and reuse the canonical unit parser. This raw-source fallback is gated to
the exact other-income context; it is not available to unrelated accounting
rows or arbitrary table kinds.

The wrapper source hashes used for this audit are:

| Artifact | SHA-256 |
| --- | --- |
| `scripts/research/run_arg_extreme_period_variant_v1.py` | `0020189860ed5ce29f3ed259d85fbb979072b5fbe570bc0889b8f7ddcc6b0e71` |
| `scripts/research/replay_other_income_q822_v1.py` | `e46c7c519bb02b2eb60e4d033d533af222458d555e370f32e3ea3d9e10ec3b74` |
| `scripts/research/materialize_validated_route_overlay_v1.py` | `a0aa853b03649d763f9c4fcf41a4806bb9302d0b2c1b68e3039e912373d2f3b1` |

## Same-snapshot A/B

The immutable full-population A/B is under
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_other_income_ab_v1/`.
Both arms used the same code snapshot
`artifacts/runs/vifinqa_answer_optimization_20260830/semantic_contract_v7_code_snapshot_NgknJ2/`,
builder SHA `08b6551fa7aa98ae85ac12d3b6afc0e1bdd3365a6a6555afc62b8c7d34bf960b`,
source-first lookup SHA
`fed5d4a674478cb3d3f67f84c47c24749b73bf5d216f334a55aac00ff4b329c6`, the
146,246-table asset, and the complete source-line map.

Control and variant both passed the full-population delivery gates:

- validation: `valid=true`, `records=1012`, `queries_replayed=1012`,
  `errors=[]`;
- source-line map: 146,246 entries, no missing or extra entries;
- ZIP integrity: pass for both `control_same_snapshot/submission.zip` and
  `variant/submission.zip`.

The same-snapshot comparison has 32 diffs, exactly the typed argmax cohort:
`[813, 822, 829, 832, 841, 850, 860, 874, 876, 879, 890, 897, 900, 904,
906, 910, 921, 928, 929, 933, 936, 946, 948, 953, 971, 974, 981, 989, 997,
999, 1000, 1008]`. There are no non-argmax diffs. The 32 changed records are
limited to answer/route-related fields (`answer`, `prediction_tier`,
`pandas_query`, and relevant-table/document provenance); this is an attribution
boundary, not a claim that all 32 are independently semantically authorized.

Variant telemetry was 43 typed argmax plans seen, 32 accepted, 6 cohort
rejected, 1 unique-extreme tie, and 4 unsupported contracts. Two records used
the bounded raw-source unit fallback. Only Q822 from this run was independently
replayed and materialized; the other accepted argmax IDs remain research
candidates until their own source/scope checks pass.

## Materialized candidate

The one-row overlay was built on the prior 66-replacement candidate:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_v1/`.

The resulting 67-replacement candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_v1/`

and its portable archive is

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_v1.zip`

The materializer reports one explicit new replacement, 66 inherited
replacements, 67 total replacements, JSON-value preservation for unselected
rows, `new_numeric_arithmetic_invented=false`, and a single output delta from
the 66-replacement base: Q822 `30476603104.0 -> 2021.0`. The output validates
and replays 1,012/1,012 with `errors=[]`, contains 1,012 evidence CSV members,
passes `unzip -t`, and has SHA-256
`543fa06012bc3baccd79210b851945013d7735994051f63db873f5fda25c4539`.
The route manifest explicitly contains Q822 and explicitly does not contain
Q829.

## Status and next gate

The candidate is in the `authorized_best_effort_submission_candidate` lane.
`human_verified=false`, `promotion_allowed=false`, and no official score delta
is claimed. The local repository has no held-out gold scorer that can turn this
replay into an official Answer/Execution score.

Regression checks after the change are `19 passed` for
`tests/research/test_arg_extreme_period_variant_v1.py`, plus Python compile
success for the changed runner, independent replay, and materializer. Q829
remains quarantined because its unscoped TTF question has both separate and
consolidated source values. The next work remains family-bounded: finish the
active Q989 accrued-interest and tax-payable/selector runs, then inspect the
remaining no-coherent-row and tie/unsupported argmax classes without enabling a
global table-kind relaxation.
