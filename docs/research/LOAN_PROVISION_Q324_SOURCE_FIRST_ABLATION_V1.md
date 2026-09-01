# Bounded loan-provision source-first ablation: HBC Q324

Date: 2026-08-31  
Status: completed candidate audit; authorized best-effort submission candidate only  
Scope: one explicitly titled short-loan provision schedule; no generic provision-table relaxation

## Result

Q324 asks for the short-term-loan provision at the end of 2024 for the HBC
parent company, in billion VND. The existing candidate selected an unrelated
semantic cell and returned `-154.380696547`. The bounded loan-provision route
selects the exact titled schedule and returns `80.864684721`.

The source table is:

| Field | Value |
|---|---|
| ticker / scope / report year | HBC / separate / 2024 |
| document | `HBC_financial_statements_2024_separate` |
| internal table UID | `4bee72b67b57d131a6edd3f4f700cc5407054894694b64c05bbb38db6f434d79` |
| source line | 1197 |
| table kind | `financial_data_schedule` |
| schedule title | `Chi tiết dự phòng các khoản cho vay ngắn hạn` |
| current column | `31/12/2024VND` |
| selected total row / column | row 4 / column 1 (zero-based) |
| raw total | `80.864.684.721 VND` |
| answer | `80.864684721 tỷ đồng` |

There are exactly three detail rows. Their Decimal values are
`75,075,867,681 + 1,429,181,347 + 4,359,635,693 = 80,864,684,721`, exactly
matching the final total. No numeric value is invented by the route.

## Bounded contract

The route is enabled only for a direct lookup that has one ticker, one report
year, an ending-period cue, and the short-loan-provision metric. It then
requires all of the following:

- a safe balance/schedule table;
- the exact local title `Chi tiết dự phòng các khoản cho vay ngắn hạn`;
- an explicit current-period VND column;
- a final total row after the detail rows;
- at least one numeric detail row; and
- a Decimal checksum where the detail values equal the selected total.

Unscoped consolidated/separate candidates with conflicting values are rejected.
The exact table kind is retained as provenance, but the generic
`financial_data_schedule` label does not authorize the answer by itself. A
generic loan, receivable, expense or provision row cannot enter this route
without the titled schedule and checksum.

## Full-population A/B

The run is stored at:

`artifacts/runs/vifinqa_answer_optimization_20260830/loan_provision_ab_v1/`

Control and variant used the same builder snapshot, source lookup, full
146,246-table asset, complete source-line map, review/research inputs and
full-population gate. The control used the unpatched builder; the variant
added `program_loan_provision_balance_v1`.

| Gate / measure | Control | Variant |
|---|---:|---:|
| question records | 1,012 | 1,012 |
| query replay | 1,012 | 1,012 |
| validation errors | `[]` | `[]` |
| source-line-map coverage | PASS, 146,246/146,246 | PASS, 146,246/146,246 |
| ZIP integrity | PASS | PASS |
| ZIP SHA-256 | `4e7f2097ee3aa5444ecb1d82ca8bef03d17736e226f3b65afd4be39a78b806af` | `922bf680925ccdb9ebc67ed48eaca086383b96aee3b40e1397a0eb84764b1d84` |
| typed loan-provision family cases | n/a | 4 seen, 1 accepted, 3 conflicting rejects |

The exact control-to-variant comparison has one full-record and one answer
diff: Q324 only. In the paired run, Q324 changes from `0.0` / `fallback_zero`
to `80.864684721` / `program_loan_provision_balance_v1`. There are no
collateral changes. The three rejected cases are Q33, Q208 and Q235; each has
conflicting separate/consolidated or balance/movement values and remains
fail-closed.

The variant telemetry is:

```text
family_seen=4
family_accepted=1
accepted_short_loan_total=1
rejected_conflicting_duplicate_answers=3
candidate_table_count_total=752
table_rejected_unsafe_kind=129
```

The A/B is an attribution and delivery test, not an official score. No local
gold scorer is available to convert the route diff into an Answer Accuracy or
Execution Accuracy delta.

## Independent source replay

The replay checker is:

`scripts/research/replay_loan_provision_q324_v1.py`

Its output is:

`artifacts/runs/vifinqa_answer_optimization_20260830/loan_provision_ab_v1/independent_replay_q324_v1.json`

The checker does not import the loan-provision adapter. It independently
verifies the exact UID, source SHA-256, source line, byte/character table
slices, titled schedule, current VND column, three detail rows and Decimal
checksum. It reports `status=PASS` and `promotion_allowed=false`.

## Materialized candidate

Only Q324 was copied from the independently replayed variant onto the current
canonical 68-replacement candidate. The full-corpus variant was not copied
wholesale.

Base candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_v1/`

Materialized candidate ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_loan_provision_q324_v1.zip`

Materialization facts:

- inherited replacements: 68;
- explicit new replacement: Q324 only;
- total replacements: 69;
- base Q324: `-154.380696547`;
- materialized Q324: `80.864684721`;
- final ZIP SHA-256: `27d9a6e1fd87f4ccf10036f386a1963c2427448d55f3339cdbe8a7f7cdbf2369`;
- final ZIP size: 549,925 bytes;
- ZIP members: 1,013 total, including 1,012 evidence CSV files;
- validation: 1,012/1,012 records, 1,012/1,012 replay, `errors=[]`;
- independent base/output comparison: exactly `[324]` changed;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`, `promotion_allowed=false`;
- lane: `authorized_best_effort_submission_candidate`.

The final archive passes `unzip -t`. The route manifest contains
`program_loan_provision_balance_v1` for Q324 and retains the earlier 68
allow-listed replacements.

## Regression and decision

The independent replay, materializer, focused loan-provision A/B and full
submission gates all pass. The repository regression after this increment is:

```text
.venv/bin/pytest -q: 685 passed, 1 skipped in 10.03s
git diff --check: PASS
```

Decision: retain Q324 as one bounded loan-provision route in the authorized
best-effort candidate. Do not generalize it to all `Dự phòng` rows, all
`financial_data_schedule` records or all balance/movement schedules. The
official score remains unmeasured until the same leaderboard split or an
authoritative gold scorer is available.
