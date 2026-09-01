# Q884 total-liabilities source-first argmax ablation

Date: 2026-08-31  
Status: full-population validated candidate; independently replayed source
cells; not human verified; no official score measured.

## Hypothesis and family

Q884 asks:

> Năm nào có tổng nợ phải trả của HND cao nhất trong các năm 2016, 2017, 2018, 2021 và 2022?

The active 73-replacement candidate answered `-384536504472.0` from an
unrelated semantic-cell fallback. The reusable hypothesis is that an
unscoped Vietnamese total-liabilities argmax must bind to a balance-sheet
total-liabilities row family and compare the current-period column across all
requested years. The route must reject a similarly named cash-flow change row,
the prior-period column, mixed table kinds, and incomplete or ambiguous
cohorts.

This is a family contract for `Tổng nợ phải trả` / `Nợ phải trả`, not a Q884
answer exception. The predictor does not read Q884 as a routing key. The
materialization step also uses an independent exact source-table-UID closure;
the question ID is present only as tracking metadata in the audit.

## Contract implemented

The candidate change is in
`scripts/research/run_argmax_period_variant_v1.py`:

- the canonical metric family includes `tong no phai tra` with the
  `Nợ phải trả` alias;
- a candidate row is eligible only in a raw `balance_sheet` table;
- the row code must be `300` and the normalized row label must contain
  `no phai tra`, covering the OCR label
  `NỘ PHẢI TRẢ (300 = 310 + 330)`;
- every requested year must contribute exactly one current-period value in the
  `31/12/{year} VND` column;
- all operands must have the same row family, table kind, source multiplier
  and scope cohort; the source represents scope as `unknown`, so all five
  records must agree on that same unknown scope;
- a unique maximum is required; ties and incomplete cohorts are rejected;
- the cash-flow statement row with code `11` is excluded even when its label
  or nearby values could look like a liability change;
- the route returns the winning year and copies the replayed source value only
  after the source/table contract has passed.

The independent checker is
`scripts/research/replay_total_liabilities_q884_v1.py`. It does not import the
variant driver. It checks source and table hashes, source-line and byte/column
coordinates, exact table context, code `300`, the OCR row alias, current and
prior VND headers, the shared unknown scope and the unique arg-max winner.

## Required experiment contract

### Control fingerprint

- Frozen control full-population artifact:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_scope_consensus_ab_v1/variant/submission/`.
- Control ZIP SHA-256:
  `7b6e96bd090fdb7366b9e6adff514fedffa9a8d25a073186edbdef05c6adf22d`.
- Control build report SHA-256:
  `29f4baf4d5e8b1ce07afb5834acf6a5216a1ca3b160cebee0670a05bf48d8963`.
- Control builder SHA-256:
  `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e`.
- Control source-first lookup SHA-256:
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`.
- Control structured-table asset:
  `artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl`,
  146,246 tables, SHA-256
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`.
- Control research-driver fingerprint before the total-liabilities family
  addition: `e87620f651d232d62bc6073037298e75361eb145cb873abb47d8720f22518dbb`.

### Candidate fingerprint

- Full-population candidate artifact:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_total_liabilities_ab_v5/variant/submission/`.
- Candidate ZIP SHA-256:
  `e3f0db87c0840d148eb0b2434ec98f9188588c5b2e9b1a6505c9f916d22a0305`.
- Candidate build report SHA-256:
  `f5b225ec4879de23886669030618b303da2af6054aff26b0aca948b4c66fa04d`.
- Candidate builder SHA-256: same frozen builder
  `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e`.
- Candidate source-first lookup SHA-256: same frozen lookup
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`.
- Candidate research-driver SHA-256:
  `7811003c6633bebaf4dffa7c794d9a94c034769a08bf888e3a8a6b9dd70412c8`.
- Candidate independent checker SHA-256:
  `9358a7a26e7dbe532a243d2fe06b56d1fae342790ee7dce5ec393b91380dcad0`.
- Candidate structured-table asset: the same 146,246-table asset and SHA as
  the control.

The control and candidate use the same full-population runtime inputs,
candidate-fusion inputs, model/reranking settings and source-first lookup
disables. The only intended route change is the generalized total-liabilities
row-family contract.

### Population and split

- Population: the complete ViFinQA submission snapshot, 1,012 questions.
- Split: no tuning split was used for the reported A/B; control and candidate
  were both run over the complete population.
- Candidate validation/replay: 1,012 records, 1,012 queries replayed,
  `errors=[]`.
- Control validation/replay: 1,012 records, 1,012 queries replayed,
  `errors=[]`.
- Both arms emitted 952 predicted questions and 60 fallback questions. The
  local verification classes stayed `PARTIAL=950`, `UNRESOLVED=62` in both
  arms.

### Scorer/gold

No independent gold evaluator or official leaderboard scorer was available
for this local run. The structural validator, Decimal replay, ZIP test and
independent source checker are engineering/provenance checks only.

`ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED`  
`EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED`

Scorer command: `NOT_RUN` (no local official scorer/gold identity).  
Scorer exit status: `NOT_APPLICABLE`.

## Full-population A/B result

The control-to-candidate comparison over all 1,012 submission records found:

- changed answer records: `1`;
- changed record: Q884 only;
- non-target answer changes/regressions: `0`;
- unchanged records: `1,011`;
- missing records: `0`;
- errors: `0`;
- Q884: `-384536504472.0` / `semantic_cell_heuristic` -> `2016.0` /
  `program_arg_extreme_period_v1`;
- changed Q884 fields: `answer`, `pandas_query`, `prediction_tier`,
  `relevant_docs`, `relevant_tables`.

The route-level diagnostic counts changed from control to candidate as follows:

| Diagnostic | Control | Candidate | Delta |
|---|---:|---:|---:|
| typed arg-extreme plans seen | 43 | 43 | 0 |
| accepted period-extreme cases | 33 | 34 | +1 |
| cohort rejected | 5 | 4 | -1 |
| unique-extreme ties rejected | 1 | 1 | 0 |
| cross-scope winner consensus cases | 7 | 7 | 0 |
| year-selection candidates | 2,974 | 3,122 | +148 |
| mixed-table-kind rejections | 103 | 110 | +7 |
| year-candidate window empty | 14 | 11 | -3 |
| semantic-cell heuristic tier | 573 | 572 | -1 |
| program arg-extreme tier | 33 | 34 | +1 |

These are diagnostic route/replay counts, not accuracy metrics. In particular,
the single changed answer is not called a gold-correct gain without an
independent scorer.

## Independent source replay

Replay artifact:
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_total_liabilities_ab_v1/independent_replay_q884_v1.json`  
Replay SHA-256:
`0af3eb9574ebc0d8efc60061dabc7868b2ce3fc6a40dedf9f01bcbc5518c792f`

Replay status is `PASS`. It has five exact records, five verified internal
table UIDs and a unique winner at 2016. The current-period values are:

| Year | Current header | Current raw VND value | Row code/label |
|---:|---|---:|---|
| 2016 | `31/12/2016 VND` | 12,393,987,700,725 | `300 / NỘ PHẢI TRẢ (300 = 310 + 330)` |
| 2017 | `31/12/2017 VND` | 9,968,932,894,559 | `300 / NỘ PHẢI TRẢ (300 = 310 + 330)` |
| 2018 | `31/12/2018 VND` | 8,077,150,487,394 | `300 / NỘ PHẢI TRẢ (300 = 310 + 330)` |
| 2021 | `31/12/2021 VND` | 2,475,731,954,180 | `300 / NỘ PHẢI TRẢ (300 = 310 + 330)` |
| 2022 | `31/12/2022 VND` | 1,903,239,627,025 | `300 / NỘ PHẢI TRẢ (300 = 310 + 330)` |

All five records are HND balance-sheet tables with VND multiplier `1`, the
same unknown scope and the same row family. The checker also confirms the
prior-period columns are not used. It reports:

- `winner_year=2016`;
- `winner_value_vnd=12393987700725`;
- `unique_winner=true`;
- `same_unknown_scope=true`;
- `same_vnd_multiplier=true`;
- `exact_balance_sheet_context_checked=true`;
- `exact_code_300_row_checked=true`;
- `source_hashes_checked=true`;
- `source_line_coordinates_checked=true`;
- `promotion_allowed=false`;
- `strict_certificate=false`;
- `official_score_measured=false`.

The complete source/table hashes, line numbers, page numbers, local ordinals,
coordinates and internal UIDs remain in the replay JSON. The materializer
records the sorted verified-UID closure hash
`410e4d3afec49aaa5efd0455f117c16a052b305cd100f2fb3e6e92b42962ab9f`.

## Materialized candidate and overlay provenance

The active overlay base is the 73-replacement candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q73_q829_after_q921_v1.zip`  
SHA-256:
`ad7afa28f4c01b3cbfdab180f0bee1519c24fcc32c098525292124a3083c4968`

The valid Q884 overlay is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q74_q884_after_q829_generic_v2.zip`  
SHA-256:
`d7f576f15c865c51f23544dfa6441ee80cbc27bfc1282721d02761ed9ff3b7b3`

Overlay build report:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q74_q884_after_q829_generic_v2/build_report.json`  
SHA-256:
`232b4487d7714e68e8f7a3873bf0b1cedc81dace7d9a9d0a14fa22759e544bac`

The report proves:

- base submission SHA-256:
  `2a932f52cac6b2d6c5ec77e2cae201be49b85b96f36b104216d2d7bd3e6bb717`;
- source submission SHA-256:
  `8a161c4ef8153e5351d581a7668f50a177ae73ef14bf08fee1b2e3a94ccc5904`;
- inherited replacements: `73`;
- new replacement: `1` (Q884 as tracking metadata);
- total route overrides: `74`;
- source route validation: `1,012/1,012`, `errors=[]`;
- final overlay validation: `1,012/1,012`, `errors=[]`;
- ZIP members: `1,013`, including `1,012` evidence CSV members;
- `replacement_selection_basis=independent_replay_source_uid_exact_set`;
- `replacement_ids_are_explicit=false`;
- `independent_replay_status=PASS`;
- `verified_source_uid_count=5`;
- `unselected_rows_preserved_bytewise_at_json_value_level=true`;
- `numeric_values_copied_from_source_build=true`;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`;
- `promotion_allowed=false`;
- lane: `authorized_best_effort_submission_candidate`.

An earlier packaging attempt at
`integrated_candidate_q74_q884_after_q829_v1.zip` is intentionally retained
for provenance but is not the accepted artifact for this experiment: its
report used the legacy explicit-ID metadata and the older `ab_v1` source
directory. The `generic_v2` artifact above is the one to use for the candidate
handoff.

## Family-level results and decision

### Diagnostic family result

The total-liabilities family contract produced one new full-population route
acceptance and one independently replayed Q884 replacement. There were no
non-target answer changes or regressions in the control/candidate A/B. This
supports retaining the generalized contract for further investigation, but it
does not establish family accuracy from one audited question.

### Required handoff fields

Hypothesis/family: context-bound balance-sheet total-liabilities row family
for simple period argmax.  
Control fingerprint: frozen `argmax_scope_consensus_ab_v1` artifact, builder
`97e91f…`, lookup `d68ed4…`, asset `617ae0…`, ZIP
`7b6e96…`.  
Candidate fingerprint: `argmax_total_liabilities_ab_v5`, same builder/lookup/
asset, research driver `7811003c…`, ZIP `e3f0db…`.  
Population and split: complete 1,012-question population; no tuning split in
the reported A/B.  
Scorer/gold: no independent gold or official scorer; `NOT_MEASURED`.  
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED.  
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED.  
Improved / regressed / unchanged / unresolved: 1 replay-supported candidate
change / 0 non-target regressions / 1,011 unchanged / semantic correctness
unresolved without an independent scorer.  
Family-level results: accepted route 33 -> 34; target replay 5/5 PASS;
non-target answer diffs 0.  
Diagnostic-only metrics: route counts, replay count, validation count,
coverage and tier counts above.  
Artifact paths and hashes: see the control/candidate/overlay/replay sections
above.  
Decision: `KEEP` as a candidate family contract and candidate artifact;
continue independent scoring and holdout validation before promotion.  
Authority status: `CANDIDATE_ONLY`.

The next step is a new family-level residual audit, not another Q884-specific
patch. Q822 (`Lợi nhuận khác`) remains a candidate for source inspection only
after this Q884 artifact is frozen.
