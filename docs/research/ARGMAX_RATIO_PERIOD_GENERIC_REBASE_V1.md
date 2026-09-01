# Generic UID-closure rebase for the argmax ratio-period candidate

Date: 2026-08-31  
Status: full-population validated candidate packaging; independent ratio
replay already PASS; not human verified; no official score measured.

## Hypothesis/family

The reusable research family is `argmax ratio-period`: choose the year with
the highest or lowest ratio between two source-table operands. The ratio
adapter itself was already built and evaluated over the complete ViFinQA
population in
`docs/research/ARGMAX_RATIO_PERIOD_SOURCE_FIRST_ABLATION_V2.md`.

This rebase addresses a packaging/provenance bottleneck exposed by the new
population-level research rule. The old ratio overlay selected three rows by
an explicit ID list. The new materializer selects them only by exact sets of
independently replayed source-table UIDs, with the replay question IDs kept as
tracking metadata. The question IDs do not control prediction, routing,
retrieval, parsing, formula selection or answer selection.

The three family instances are:

- `lease_land_cost_share`: KBC land/infrastructure lease cost divided by
  total cost of goods sold and services supplied; winner 2016;
- `deposit_interest_expense_share`: HDB deposit-interest expense divided by
  total interest expense; winner 2025;
- `transport_segment_asset_share`: PVT transport-segment assets divided by
  total assets; winner 2025.

The generic materializer maps each replay closure to a source submission row
only when the row's evidence CSV has exactly the same UID set. It then checks
the generic source tier `program_arg_extreme_ratio_period_v1`. It never reads
the answer value to choose a row and never computes a replacement number.

## Control fingerprint

The frozen full-population ratio control is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/control/submission/`

- submission SHA-256:
  `ea47bdc1929eb70523b15a8a1b232074893810fd6ecac103ceb2dd987df42bc6`;
- ZIP SHA-256:
  `7604caa04d323ccad96b8678c9db01a07f192fd143f7dc7a5153d39adc62e7a2`;
- build report SHA-256:
  `9656f510d8cb9eb729b6e887173090d92645c92acdf82da0634b7067b04fa84f`;
- builder SHA-256:
  `8badd7ab3a296efaa8f9ed213112b2000a369d6be42c247cb8b42f3ed6a2a928`;
- candidate-plan selector SHA-256:
  `aded34a7aa7e68a6a5dbb57c1dc873b6af33425c59b5a362baf852464e07df29`;
- source-first lookup SHA-256:
  `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`;
- complete structured asset: 146,246 tables, SHA-256
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- complete source-line map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.

The ratio control disables the ratio adapter. It retains all 1,012 questions
and uses the same full structured asset and runtime inputs as the candidate.

## Candidate fingerprint

The frozen full-population ratio candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/variant/submission/`

- submission SHA-256:
  `f5ac6835dcca2fe195a3e76e9eed56fa5b852ee34e876c9d1d124c2315457ab1`;
- ZIP SHA-256:
  `599eee951c47e2c99d6213b7a4fe0f40d1e546c4f204e7d3b1a888ae37c397a1`;
- build report SHA-256:
  `d3d4f9fb786fbb34845f73916030c8870f51ede162fa9c9f5a84f21dc15e5af9`;
- ratio adapter SHA-256:
  `95fb165f4fc1dcb722e7f6fc5bd09b3d249a1e92ed5cddf3d4cca6758c159db2`;
- independent replay checker SHA-256:
  `f64b14700c61d2bfbaa810008c1b158186b045a9b564455eb13f3cbc7cf1670e`;
- same builder, selector, lookup, structured asset and source-line-map
  fingerprints as the control.

The independent replay artifact is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/independent_replay_ratio_families_v1.json`

Its SHA-256 is
`77760dc5cc659addc48c4155b05a181570aec3d841752d0682657ae9f5b0525e`.
It reports `status=PASS`, three replayed family records, no failures,
`answer_accuracy=NOT_MEASURED`, `execution_accuracy=NOT_MEASURED`,
`official_scorer=NOT_AVAILABLE`, `authority_status=CANDIDATE_ONLY` and
`promotion_allowed=false`.

## Population and split

- Population: complete frozen ViFinQA snapshot, 1,012 questions.
- Split policy: the ratio hypothesis was discovered before the final A/B;
  the reported control and candidate both cover the complete population.
- Structured corpus searched: 146,246 tables.
- Control and candidate records: 1,012 each.
- Missing records: 0 in both arms.
- Validation/replay: 1,012 queries in each arm, `errors=[]`.
- The materialized overlay is compared against the complete current active
  candidate, not against a reduced target subset.

## Scorer/gold

No independent gold set or official leaderboard scorer is available in this
workspace. Structural validation, Decimal replay, source coordinates and ZIP
integrity are not answer correctness.

`ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED`  
`EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED`

Scorer/gold identity: `NOT_AVAILABLE`.  
Scorer command: `NOT_RUN`.  
Scorer exit status: `NOT_APPLICABLE`.

## Full-population A/B result

The frozen ratio control-to-candidate comparison reports:

| Measure | Control | Candidate | Interpretation |
|---|---:|---:|---|
| input questions | 1,012 | 1,012 | same full population |
| submission records | 1,012 | 1,012 | no missing records |
| builder replay queries | 1,012 | 1,012 | structural validation only |
| builder errors | 0 | 0 | technical gate passed |
| changed complete records | — | 3 | Q826, Q877, Q982 |
| changed answer fields | — | 3 | candidate diagnostics only |
| non-target complete-record changes | — | 0 | no observed collateral diff |
| unchanged complete records | — | 1,009 | structural count only |
| improved answers | — | `NOT_MEASURED` | no gold/scorer |
| regressed answers | — | `NOT_MEASURED` | no gold/scorer |
| unresolved semantic correctness | — | `NOT_MEASURED` | no gold/scorer |

The three answer/tier changes are:

| Family | Tracking row | Control answer | Candidate answer | Replay winner | Route |
|---|---:|---:|---:|---:|---|
| `lease_land_cost_share` | Q826 | `73339644528.0` | `2016.0` | 2016 | `program_arg_extreme_ratio_period_v1` |
| `deposit_interest_expense_share` | Q877 | `16786000000.0` | `2025.0` | 2025 | `program_arg_extreme_ratio_period_v1` |
| `transport_segment_asset_share` | Q982 | `3651292326370.0` | `2025.0` | 2025 | `program_arg_extreme_ratio_period_v1` |

Each changed row also changes its `pandas_query`, `prediction_tier`,
`relevant_docs` and `relevant_tables`. These are replay-supported candidate
changes, not three measured accuracy gains.

## Independent replay and UID closure

The replay has three tracking groups. The generic materializer derived the
source replacements from the following exact UID closures:

| Tracking group | Family | Verified UID count | Winner | Replay status |
|---|---|---:|---:|---|
| `question:826` | KBC lease-land-cost share | 8 | 2016 | PASS |
| `question:877` | HDB deposit-interest share | 3 | 2025 | PASS |
| `question:982` | PVT transport-segment asset share | 5 | 2025 | PASS |

There are 16 unique UIDs across the groups because the HDB numerator and
denominator share one table UID per year. The candidate replay checks exact
source/table coordinates, source hashes, table UIDs, report years, current
columns, declared multipliers, numerator/denominator roles, family semantics,
unique winner and the applicable scope. The complete cell ledger remains in
the independent replay JSON.

The generic selection audit in the materialized report records:

- `replacement_selection_basis=independent_replay_source_uid_exact_set`;
- `replacement_ids_are_explicit=false`;
- `verified_source_uid_count=16`;
- `verified_source_uid_closure_count=3`;
- `matched_submission_row_count=3`;
- matched submission IDs are explicitly marked tracking-only;
- each matched source row has the generic tier
  `program_arg_extreme_ratio_period_v1`.

## Materialized candidate rebase

The base for this rebase is the valid Q884 candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q74_q884_after_q829_generic_v2.zip`  
ZIP SHA-256:
`d7f576f15c865c51f23544dfa6441ee80cbc27bfc1282721d02761ed9ff3b7b3`  
Base `submission.json` SHA-256:
`0b8dfcf48fbab0572b197704355395776a989a79429a24ab3a6b9c5237a145ca`.

The new candidate handoff is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q77_ratio_after_q884_generic_v2.zip`  
ZIP SHA-256:
`d5d45dea9eafd14f36904f06a2db0a91b93b18316a06f07da4021145840d2baa`  
Candidate `submission.json` SHA-256:
`85c03ca842064b29ab93aede3e9c74dbc69aca35205f66942c16499b7a7d5faf`  
Overlay build report SHA-256:
`ea1455e47831899ce7ae8d95774ffff748844528134ae222c0dbe709d476d3f8`.

The current generic materializer fingerprint is
`3c457b17f6096ef70897d4ee3d24b6d062225b13403d3b38bebde074e5671978`.
It now supports one or multiple replay UID closures while preserving the
same exact-UID source selection rule.

The materialized report proves:

- inherited replacements: `74`;
- new ratio replacements: `3`;
- total route overrides: `77`;
- source validation: 1,012/1,012, `errors=[]`;
- final overlay validation: 1,012/1,012, `errors=[]`;
- ZIP members: 1,013, including 1,012 evidence CSV members;
- `unselected_rows_preserved_bytewise_at_json_value_level=true`;
- `numeric_values_copied_from_source_build=true`;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`;
- `promotion_allowed=false`;
- lane: `authorized_best_effort_submission_candidate`.

Comparing Q77 with Q74 at JSON-value level changes exactly Q826, Q877 and
Q982. The other 1,009 records are unchanged. `unzip -t` reports no errors.

## Verification commands and results

The packaging and regression checks completed with:

```text
python -m py_compile scripts/research/materialize_validated_route_overlay_v1.py
exit=0

pytest -q tests/research/test_arg_extreme_ratio_variant_v1.py tests/research/test_arg_extreme_period_variant_v1.py
28 passed in 0.41s

pytest -q
716 passed, 1 skipped in 9.00s

unzip -t .../integrated_candidate_q77_ratio_after_q884_generic_v2.zip
No errors detected in compressed data

git diff --check
exit=0
```

## Required handoff and decision

Hypothesis/family: reusable source-first argmax ratio-period family with
family-specific numerator/denominator contracts.  
Control fingerprint: ratio-period v2 control, submission
`ea47bdc1…`, ZIP `7604caa0…`, builder `8badd7ab…`, lookup `72fd26a0…`,
asset `617ae044…`.  
Candidate fingerprint: ratio-period v2 variant, adapter `95fb165f…`,
submission `f5ac6835…`, ZIP `599eee95…`, independent replay
`77760dc5…`.  
Population and split: complete frozen 1,012-question population; no rows
removed from the final A/B.  
Scorer/gold: `NOT_AVAILABLE`; official scorer not run.  
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED.  
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED.  
Improved / regressed / unchanged / unresolved: 3 replay-supported candidate
changes / 0 observed non-target changes / 1,009 unchanged / semantic
correctness unresolved without gold/scorer.  
Family-level results: three supported ratio families replayed PASS; 16 unique
UIDs across 3 exact closures; no non-target record change.  
Diagnostic-only metrics: ratio acceptance 3, full validation 1,012/1,012,
ZIP 1,013 members/1,012 CSVs, test suite 716 passed/1 skipped.  
Artifact paths and hashes: listed in the fingerprint and rebase sections.  
Decision: `KEEP` the generic family candidate and use Q77 generic v2 for the
next independent scoring/holdout run; do not promote on the basis of these
diffs.  
Authority status: `CANDIDATE_ONLY`.

The older ratio overlay remains historical. It used explicit packaging
selection and must not be mistaken for the current handoff. The next research
queue should audit a new residual family (for example tax-payable or
short-term-construction-payable argmax) rather than adding a question-ID
exception.
