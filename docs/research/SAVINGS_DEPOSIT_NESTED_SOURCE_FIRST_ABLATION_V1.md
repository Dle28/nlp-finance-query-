# Savings-deposit nested-row source-first ablation v1

Date: 2026-08-31

Status: candidate-only improvement; no official ViFinQA gold/scorer is
available locally, and no strict `VERIFIED` certificate was produced.

## Outcome

The experiment targeted the failure family where a question asks for a
currency child row under a named deposit section, but generic semantic
matching can bind the same `Bằng VND` phrase in an unrelated reserve-ratio
table.

For Q151, the source-first variant requires all of the following in one
hydrated table:

- the question contains `Tiền gửi tiết kiệm` and `Bằng VND`;
- the table context contains `Tiền gửi của khách hàng`;
- the selected row is the normalized child label `Bằng VND`;
- the nearest local text-only parent row is exactly `Tiền gửi tiết kiệm`;
- the requested end-period column is selected by the existing explicit
  period-column contract.

This is a local nested-row family rule. It is not a Q151 answer allow-list and
it does not copy a value from a research packet.

## Controlled comparison

The isolated control is the completed operating-lease variant immediately
before this family was added:

`artifacts/runs/vifinqa_answer_optimization_20260830/operating_lease_total_ab_v1/variant/submission/`

The variant is:

`artifacts/runs/vifinqa_answer_optimization_20260830/savings_deposit_nested_ab_v1/variant/submission/`

Both use the same builder (`d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8`),
the same 146,246-table asset (`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`),
the same complete source-line map (`533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`),
and the same 1,012-question population. The variant lookup implementation
hash is
`a8356f69c2dcd9dc81fb5a224dddea16a974afdff07b7459a18de103252ba602`; the
control lookup hash is
`9d5fb7c58b3c1bc4d498d28b2954124c11f74104d57f9667e1eddcaf51da4dd6`.

| Metric | Control | Variant |
|---|---:|---:|
| Full-population records | 1,012 | 1,012 |
| Replay records | 1,012 | 1,012 |
| Validation errors | 0 | 0 |
| Source-first direct answers | 112 | 113 |
| `source_first_exact_row_v1` tier count | 81 | 82 |
| Semantic heuristic tier count | 595 | 594 |
| Predicted questions | 971 | 971 |
| Fallback questions | 41 | 41 |
| Nonzero answers | 958 | 958 |

The normalized submission comparison has exactly one change between these two
runs: Q151's route/provenance. Its numeric value is the same in both runs,
but the variant upgrades it from `semantic_cell_heuristic` to
`source_first_exact_row_v1`.

## Q151 source audit

Question:

`Tiền gửi tiết kiệm bằng VND của EIB đến ngày 31/12/2015 là bao nhiêu triệu đồng?`

The source-first variant selects:

- document: `EIB_financial_statements_2015_consolidated`;
- table UID:
  `3cef32a6ffce05838e0fb90dc45f97f51b50ad583ffd50e71678bbbb8b914846`;
- canonical table-start line: 1831;
- local row index: 8, label `▪ Bằng VND`;
- preceding parent row index: 7, label `Tiền gửi tiết kiệm`;
- selected column index: 1, header `31/12/2015Triệu VND`;
- raw/current-table replay value: `53.658.311`;
- replayed answer: `53658311`;
- evidence CSV: `data/q0151_evidence.csv`.

The same nested row and value are independently present in the separate
report, which the control selected:

- separate table UID:
  `6397250a6f2bb9beabf39736332acbc896c5686f44569444a35f47c808bd0632`;
- canonical table-start line: 1894;
- source SHA256:
  `407e056fa0f56230ca994ce3ee1b762677018662b04b59499625e53d0c377aa3`.

The consolidated source SHA256 is
`f57df4f04e10f9bf235bd38baf1adc0af07d1b9a598b273a3968fde659d327e1`.
Both scope variants expose the same current-period value, so the unqualified
question does not rely on a hidden scope preference for its numeric result.

The variant's Q151 audit records `source_binding=PASS` and
`provenance_integrity=PASS`, but its verification class remains `PARTIAL`:
there is no local strict verifier authority, no human semantic approval, and
no canonical certificate.

## Candidate materialization

The validated source route was copied into the existing 63-override candidate
using the explicit materializer. The base candidate's Q151 value was
`0.000003`; the materialized source-first value is `53658311.0`.

Candidate directory:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_lease_savings_v1/`

Candidate ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_lease_savings_v1.zip`

The materializer report records:

- inherited overrides: 63;
- new Q151 replacement: 1;
- total overrides: 64;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`;
- `promotion_allowed=false`;
- final validation: 1,012 records, 1,012 replays, `errors=[]`;
- ZIP: 1,013 members, 1,012 evidence CSVs;
- ZIP SHA256:
  `93244641a752a7c3fdb424a3131bb5c1e397ef28d6d57d9cac4228b27fe4729a`.

`unzip -t` completed with `No errors detected in compressed data`.

## Regression and interpretation

Focused tests passed:

`PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/e2e/test_source_first_lookup.py tests/e2e/test_submission_integration.py`

Result: `117 passed`.

The current full suite remains `636 passed, 1 skipped, 8 failed`; those eight
failures are the pre-existing candidate-plan integration import problem
(`finance_query.e2e.core.candidate_plan_selector`) and are unrelated to this
family experiment.

The candidate has a concrete numeric improvement for Q151 relative to its
63-override base, but this is not an official score delta. The next research
queue should continue with another nested/contextual metric family only after
checking exact source-row agreement, scope compatibility, unit semantics and
candidate-level numeric difference.
