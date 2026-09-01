# Q829 other-profit scope-consensus source-first ablation

Date: 2026-08-31  
Status: locally validated best-effort candidate; not human verified; no official
Answer Accuracy or Execution Accuracy measured locally.

## Question and observed failure

Q829 asks:

> Năm nào TTF có mức Lợi nhuận khác cao nhất trong các năm 2016, 2017, 2023 và 2025?

The active 72-replacement candidate answered `139807296911.0`. Its evidence
was an unrelated numeric cell, not a period-extreme calculation over the
`Lợi nhuận khác` row. The new route answers `2025.0`.

The relevant source has both a TTF consolidated family and a TTF separate
family. The two families have different values, so their cells must never be
mixed. However, both complete families independently produce the same unique
winner year: `2025`.

## Contract and implementation

The change is a family-level contract in
`scripts/research/run_arg_extreme_period_variant_v1.py`, not an answer patch
for Q829. It requires all of the following:

- statement code `40` and the normalized row family
  `Lợi nhuận khác` / `(Lỗ) lợi nhuận khác` / `Lỗ khác`;
- one exact current-period column (`Năm nay`) for every requested year;
- one coherent table kind, scope, source multiplier and row family per cohort;
- a unique arg-max winner inside each complete scope cohort;
- for an unscoped question, if multiple complete scopes exist, the same unique
  winner year in every eligible scope before the preferred scope is selected
  for evidence.

The consensus guard does not average or merge consolidated and separate values.
It also does not infer an answer from scope ranking alone. The selected
evidence is the preferred consolidated cohort, while the trace records the
independent scope winners and the basis
`winner_consensus_preferred_scope`.

## Full-population A/B run

The variant was run against the full structured asset, not the earlier
29,428-table candidate-UID slice:

- structured asset: `146,246` tables,
  SHA-256 `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- builder snapshot SHA-256:
  `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e`;
- source-first lookup snapshot SHA-256:
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`;
- implementation fingerprints were unchanged during the build;
- variant artifact:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_scope_consensus_ab_v1/variant/submission.zip`;
- variant validation/replay: `1,012/1,012`, `errors=[]`;
- the route accepted `33` period-extreme candidates, including `7` with
  cross-scope winner consensus; `43` typed period-extreme plans were seen;
- the full variant produced `952` non-fallback candidate predictions and
  `60` fallback questions. Its local verification classes remain
  `PARTIAL=950`, `UNRESOLVED=62`; this does not create a strict certificate.

Compared with the older strict-period artifact, the full variant has ten raw
answer diffs (Q822, Q829, Q878, Q879, Q900, Q921, Q928, Q971, Q989 and Q1008).
That comparison is only an A/B candidate diff, not an accuracy claim. Only
Q829 was independently audited and copied into the active lineage in this
step.

## Independent source replay

The independent checker is
`scripts/research/replay_other_profit_q829_v1.py`. Its output is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_scope_consensus_ab_v1/independent_replay_q829_v1.json`.

The replay checks eight exact records across the two scopes, including source
and table hashes, source-line and byte/character coordinates, bounded VND
statement context, statement code, row aliases, current-period column and
raw-value parsing. It reports `status=PASS`,
`scope_winner_consensus=true`, `promotion_allowed=false`, and
`strict_certificate=false`.

| Scope | 2016 | 2017 | 2023 | 2025 | Unique winner |
|---|---:|---:|---:|---:|---:|
| consolidated | -602,515,304 | -14,555,567,878 | -69,981,712,952 | 43,934,748,741 | 2025 |
| separate | 14,353,234,722 | -17,623,021,251 | -78,924,918,067 | 39,908,113,997 | 2025 |

The selected Q829 evidence uses the consolidated UIDs for 2016, 2017, 2023
and 2025. The complete UID/hash/coordinate ledger is intentionally kept in
the independent replay JSON rather than duplicated here.

## Materialized candidate

Only Q829 was overlaid onto the previous active candidate. The resulting
candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q73_q829_after_q921_v1.zip`

SHA-256:
`ad7afa28f4c01b3cbfdab180f0bee1519c24fcc32c098525292124a3083c4968`

The overlay report records:

- base lineage: 72 inherited replacements;
- new replacement: Q829 only;
- total unique replacements: 73;
- Q829: `139807296911.0 -> 2025.0`;
- all other 1,011 submission records preserved at JSON-value level;
- source and final validation/replay: `1,012/1,012`, `errors=[]`;
- ZIP: 1,013 members, including 1,012 evidence CSV files; `unzip -t` pass;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`, `promotion_allowed=false`;
- lane: `authorized_best_effort_submission_candidate`.

This is a candidate improvement and a provenance-complete local replay, not
proof that Q829 is gold-correct and not an official leaderboard score gain.
The next candidate family must receive the same source-first and independent
replay treatment before it is overlaid.
