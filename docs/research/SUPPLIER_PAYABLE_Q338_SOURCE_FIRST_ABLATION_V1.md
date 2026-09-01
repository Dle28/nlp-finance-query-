# Bounded supplier-payable source-first ablation: BSR Q338

Date: 2026-08-31  
Status: completed candidate audit; authorized best-effort submission candidate only  
Scope: one named supplier-payable row; no generic liability-table relaxation

## Result

Q338 asks for the end-2018 payable balance to `Tổng Công ty Dầu Việt Nam -
CTCP` for BSR, in thousand-billion VND. The 69-replacement candidate had
selected an unrelated equity-adjustment row and returned `31.00499616`. The
bounded supplier-payable route selects the named counterparty under the local
`Phải trả nhà cung cấp` heading and returns `2.499485052166`.

The two exact source duplicates agree:

| Scope | Document / page | Internal table UID | Source line | Table kind | Row / column | Current raw VND | Answer |
|---|---|---|---:|---|---|---:|---:|
| separate | `BSR_financial_statements_2018_separate` / 38 | `7611088abf3e60364ae6ca438477f4745ec0e17747c9455c519e21ba92d6d47a` | 1174 | `related_party_schedule` | 22 / 1 | `2,499,485,052,166` | `2.499485052166` |
| consolidated | `BSR_financial_statements_2018_consolidated` / 41 | `4c935ffee5296f3f7d86d22032b68ad2f67d9981741d4265f027fb4a0f400f1f` | 1224 | `financial_note` | 3 / 1 | `2,499,485,052,166` | `2.499485052166` |

The full UIDs and hashes are recorded in the independent replay artifact. In
both tables the current header is `31/12/2018 · VND`, the prior value is
`3,986,408,656,102`, and the source multiplier is `1`. The output divisor is
`1,000,000,000,000`, so:

```text
2,499,485,052,166 / 1,000,000,000,000 = 2.499485052166
```

## Bounded contract

The route is family-based, not a Q338-only patch. It accepts only a direct
lookup with one ticker, one report year, a named counterparty, a closing-period
cue and an explicitly declared source unit. It then requires:

- a safe `related_party_schedule` or `financial_note` table;
- a local `Phải trả nhà cung cấp` or `Phải trả người bán` section anchor;
- an exact counterparty row below that anchor;
- the current-period VND column; and
- agreement across unscoped separate/consolidated duplicates.

Conflicting duplicate values are rejected. A nearby equity-adjustment,
purchase, `Trả trước nhà cung cấp`, `Phải trả khác` or generic liability row
cannot enter this route merely because it contains a plausible number.

## Full-population A/B

The source variant run is stored at:

`artifacts/runs/vifinqa_answer_optimization_20260830/supplier_payable_ab_v1/`

The control is the frozen full-population control used for this direct-lookup
batch:

`artifacts/runs/vifinqa_answer_optimization_20260830/loan_provision_ab_v1/control_r1/submission/`

The control and supplier variant use the same builder snapshot and source
lookup fingerprints:

```text
builder SHA:       d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8
source lookup SHA: 72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9
table asset SHA:   617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
table count:       146,246
```

Both arms validate and replay 1,012/1,012 with `errors=[]`. The variant ZIP
contains 1,013 members, including 1,012 evidence CSV files, and passes ZIP
integrity. Its SHA-256 is
`500dd416a5c2f2a75160d1464120373c222cbadfa8f5b9ee3cc4eb10d0adc50d`.

The strict family telemetry is:

```text
family_seen=1
family_accepted=1
accepted_related_party_schedule=1
candidate_table_count_total=112
table_rejected_unsafe_kind=68
```

The exact control-to-variant comparison has one full-record and answer diff:
Q338 only, `0.0` / `fallback_zero` to `2.499485052166` /
`program_supplier_payable_v1`. No collateral answer diff is attributed to
this route.

## Independent source replay

The checker is:

`scripts/research/replay_supplier_payable_q338_v1.py`

Its output is:

`artifacts/runs/vifinqa_answer_optimization_20260830/supplier_payable_ab_v1/independent_replay_q338_v1.json`

The checker does not import the supplier-payable adapter. It independently
loads both exact UIDs and verifies each source SHA-256, table SHA-256,
byte/character coordinates, source-line coordinate, document/scope/year,
safe table kind, local supplier section, named counterparty row, current and
prior VND columns, and the decimal unit conversion. Both source scopes replay
to the same answer, and the artifact reports `status=PASS` with
`promotion_allowed=false`.

## Materialized candidate

Only Q338 was copied from the independently replayed variant onto the current
69-replacement candidate. The full-corpus variant was not copied wholesale.

Base candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_loan_provision_q324_v1/`

Materialized candidate ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_loan_provision_q324_supplier_payable_q338_v1.zip`

Materialization facts:

- inherited replacements: 69;
- explicit new replacement: Q338 only;
- total replacements: 70;
- base Q338: `31.00499616`;
- materialized Q338: `2.499485052166`;
- final ZIP SHA-256: `b682cc7c9a60b738b2fd7441085636881071b257cea8f6925c11bf3f34837338`;
- final ZIP size: 549,864 bytes;
- ZIP members: 1,013 total, including 1,012 evidence CSV files;
- validation: 1,012/1,012 records, 1,012/1,012 replay, `errors=[]`;
- independent base/output comparison: exactly `[338]` changed;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`, `promotion_allowed=false`;
- lane: `authorized_best_effort_submission_candidate`.

The route manifest records Q338 as
`program_supplier_payable_v1`; the earlier 69 allow-listed replacements are
retained unchanged. The final archive passes `unzip -t`.

## Nearby tax-payable shadow

The same full-population family sweep accepted Q226 with the source value
`304.2798775`, but the current 69-replacement candidate already contains that
same answer through its existing semantic source row. Therefore Q226 produces
no answer-level delta and is not counted as a new score improvement in this
increment. It remains outside the materialized route allow-list until a
separate route/provenance gain is shown to matter to the target score.

## Decision

Retain Q338 as one bounded supplier-payable route in the authorized
best-effort candidate. Do not generalize it to all payables, all related-party
tables or all balance-sheet rows. The official leaderboard score remains
unmeasured until the same evaluation split or an authoritative gold scorer is
available.
