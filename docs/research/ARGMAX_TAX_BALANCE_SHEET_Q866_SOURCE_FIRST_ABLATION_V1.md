# Argmax tax-payable balance-sheet family: Q866 source-first ablation v1

Date: 2026-08-31  
Status: full-population validated candidate; independent replay PASS; no
official score measured; authority remains `CANDIDATE_ONLY`.

## Outcome

The reusable family contract recovers period-extremum questions whose metric
is the balance-sheet line `Thuế và các khoản phải nộp Nhà nước`. It binds the
exact balance-sheet row code `313`, the current-period column `Số cuối năm`,
the same consolidated scope and table family across all requested years, and
an explicit VND document-currency declaration. It rejects the previously
selected retrieved cell from an unrelated SJG note/table.

The isolated integrated A/B comparison changes exactly one answer:

```text
Q866: 337129394454.0 -> 2018.0
```

The answer is supported by an independently replayed maximum over four
current-period source cells. This is a source/replay improvement candidate,
not a measured answer-accuracy gain: no independent gold set or official
scorer is available in the workspace.

## Hypothesis and family contract

The hypothesis is that this failure class is caused by metric-family
ambiguity plus missing source-unit evidence, not by the argmax operation
itself. A reusable balance-sheet tax-payable rule should therefore:

1. recognize the canonical metric and its Vietnamese accounting alias;
2. require `table_function.kind=balance_sheet`,
   `table_purpose.kind=period_comparison` and
   `table_section.kind=balance_sheet`;
3. locate the row by the exact accounting code `313` and the normalized label
   `Thuế và các khoản phải nộp Nhà nước`, rather than by Question ID or a
   retrieved-cell rank;
4. select column index 4 (`Số cuối năm`) as the current value and keep column
   index 5 (`Số đầu năm`) only as the prior-period comparison;
5. require one `consolidated` scope and one multiplier across the requested
   years; and
6. obtain the VND multiplier from a bounded source declaration or the exact
   document-level accounting-currency sentence. Numeric magnitude, page
   number, retrieval rank and Question ID are not unit evidence.

Q866 is tracking metadata for this audit. It does not control prediction,
routing, retrieval, parsing, formula selection or answer selection.

## Control fingerprint

The isolated control is the active 78-replacement integrated candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q78_financial_liability_after_q77_generic_v1/`

Its fingerprints are:

- `submission.json` SHA-256:
  `85c03ca842064b29ab93aede3e9c74dbc69aca35205f66942c16499b7a7d5faf`;
- `build_report.json` SHA-256:
  `cdb7c68603dfbcbad8e50d149efeacfed79d9bbc84343396eff614e4ac49789f`;
- ZIP SHA-256:
  `1653d54edceda804844a4999cb985997a6276425b1123c020f3861b5a4a1129e`;
- control Q866 evidence CSV SHA-256:
  `244c28ba3e072a500ca81b2a2e6e19ae3f9483ae9ac4d0bf58ad070d5e4736d3`.

Control Q866 was still the heuristic cell
`337129394454.0`, from internal table UID
`90e6e6576f7a5f90264c30c909d3a092c8ff5fb20e8236a9608917cccb277af1`, row 2,
column 1. That cell is not one of the four independently replayed
balance-sheet cells.

The control contains 1,012 submission rows and 1,012 diagnostic rows. Its
verification-class census is 1,010 `PARTIAL` and 2 `UNRESOLVED`; these labels
are not correctness labels.

## Candidate fingerprint

The full-population source candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_balance_sheet_ab_v1/variant/submission/`

Its fingerprints are:

- `submission.json` SHA-256:
  `58e74bb200d29a12d369764175155c7dde780493d130b7589287e031e86e23b6`;
- `build_report.json` SHA-256:
  `e6618ae3be9b71dbf34fc57f405303bcbfd86f489b97dc824548cd3606d7e011`;
- full source candidate validation: 1,012 records, 1,012 queries replayed,
  `errors=[]`;
- full source candidate structured asset: 146,246 tables;
- full source candidate accepted strict period-extreme records: 35.

The isolated materialized candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q79_tax_balance_after_q78_generic_v1/`

and the handoff ZIP is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q79_tax_balance_after_q78_generic_v1.zip`

Its fingerprints are:

- `submission.json` SHA-256:
  `5cf7ee85c3204f441e47511e3b18a38b18beac3194f5904a4971c4d064d3475f`;
- `build_report.json` and `route_overlay_manifest.json` SHA-256:
  `9c3ad731337bb8804e2eea6751d6ce6264ba14c068218969e251d881634ad4b1`;
- ZIP SHA-256:
  `045beaacacc5205cd25cc2b59bd55fb9db575bb28dbc7f9a84f317606886e83a`;
- ZIP size: 551,860 bytes;
- ZIP members: 1,013, including 1,012 evidence CSVs;
- Q866 evidence CSV SHA-256:
  `5f2a49de5ab75df4558698b3bf0b6b1f4bc168bb5f126db4b407c6b0f2963489`.

Implementation fingerprints:

- argmax adapter
  `scripts/research/run_arg_extreme_period_variant_v1.py`:
  `0e9cfa4b14537c630c692bbd4fe5af382b949546eb92fc6d77ec59955e01aead`;
- independent replay checker
  `scripts/research/replay_tax_payable_q866_v1.py`:
  `d76f07f3e25337c3af0840c52a1f6efc99a78a542251e0f557c6368b0ec26042`;
- generic materializer
  `scripts/research/materialize_validated_route_overlay_v1.py`:
  `7931bdd49b2c675f0599ee5e36e66b6d19efd9bd09bbb45581cbab1203924e28`;
- frozen builder:
  `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e`;
- frozen source-first lookup:
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`;
- structured asset: 146,246 tables, SHA-256
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source-line map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.

The independent replay JSON is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_balance_sheet_ab_v1/independent_replay_q866_v1.json`

Its SHA-256 is
`64ad92ea8b5ea8773163cd397acffee6f282337f2339796387f3a50983ee5402`.
It reports `status=PASS`, `failures=[]`,
`answer_accuracy=NOT_MEASURED`, `execution_accuracy=NOT_MEASURED`,
`official_scorer=NOT_AVAILABLE`, `promotion_allowed=false` and
`authority_status=CANDIDATE_ONLY`.

## Population and split

- Population: complete frozen ViFinQA input, 1,012 questions.
- Split policy: the family hypothesis was audited from the residual queue;
  the final source build covers the complete population. No Question-ID
  tuning or target-only population was used.
- Control and candidate integrated submissions: 1,012 rows each.
- Missing records: 0 in both arms.
- Control and candidate final materialized validation: 1,012/1,012 records,
  1,012/1,012 queries replayed, `errors=[]`.
- Source candidate structured corpus: all 146,246 tables; no reduced
  target-only table asset.

The first raw full-build comparison against an older Q884 artifact showed
unrelated inherited route differences because that artifact predates the
active Q77/Q78 integrated lineage. It is therefore not used as the isolated
accuracy comparison. The q78 integrated candidate is the frozen control for
the reported q78 -> q79 delta.

## Scorer and gold

No independent gold set or official leaderboard scorer is available locally.

```text
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
Scorer/gold identity: NOT_AVAILABLE
Scorer command: NOT_RUN
Scorer exit status: NOT_APPLICABLE
```

Source replay, exact coordinates, Decimal comparison, full coverage, ZIP
integrity and unchanged answer fields are engineering/provenance evidence;
none is substituted for independent answer scoring.

## Full-population A/B result

The final isolated comparison is q78 control -> q79 materialized candidate:

| Measure | Control q78 | Candidate q79 | Interpretation |
|---|---:|---:|---|
| population | 1,012 | 1,012 | same complete input |
| submission records | 1,012 | 1,012 | no missing records |
| validation queries | 1,012 | 1,012 | structural replay only |
| validation errors | 0 | 0 | technical gate passed |
| changed answer fields | — | 1 | Q866 only |
| changed complete rows | — | 1 | Q866 only |
| non-target changed rows | — | 0 | no observed collateral change |
| unchanged complete rows | — | 1,011 | value-level comparison |
| source-supported candidate improvements | — | 1 | Q866 family replay |
| source-supported regressions | — | 0 | no non-target change |
| semantic answer accuracy | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |
| execution accuracy | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |
| unresolved semantic correctness | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |

The only answer/tier change is:

| Family | Tracking row | Control | Candidate | Independent replay | Route |
|---|---:|---:|---:|---:|---|
| tax-payable balance-sheet argmax | Q866 | `337129394454.0` | `2018.0` | 2018 | `program_arg_extreme_period_v1` |

The candidate changes the Q866 evidence, pandas query, relevant table list,
and prediction tier as a coherent route replacement. Those fields are
candidate diagnostics and provenance, not accuracy labels. The verification
census remains 1,010 `PARTIAL` and 2 `UNRESOLVED` in both integrated arms.

## Independent replay and source closure

The replay checker is independent of the argmax adapter and verifies four
SJG consolidated balance-sheet tables:

| Report year | Internal table UID | Source line | Row/column | Current value (VND) |
|---:|---|---:|---:|---:|
| 2018 | `c59a5ca75a84db105f19c96dbebebe99782db3a77fea09c820c238de07b83ef1` | 244 | 5 / 4 | 386,945,215,579 |
| 2019 | `e402fb40b3051846c8e6677777dfb53b2c5291adffadff454c07440e680c87bc` | 310 | 5 / 4 | 307,749,988,310 |
| 2020 | `df32878ddc0701997a2a7f8c9539f39aeffceb8c7b6ecf5164711d50c69a7396` | 341 | 6 / 4 | 249,462,096,512 |
| 2021 | `136a07c2bb57670e5e2ac17733f8c5c0e8b327c93632e4cf88a64ff4117e3a89` | 310 | 5 / 4 | 249,374,016,597 |

The replay checks exact source and table hashes, byte/character coordinates,
source-line coordinates, report years, local ordinals, page numbers,
`balance_sheet` function/purpose/section, consolidated scope, code `313`,
the normalized tax-payable label, current/prior headers, unique row matching,
the document-level VND declaration and a unique Decimal maximum. The four
source multipliers are all `1` and no numeric magnitude heuristic is used.

The generic materializer records:

```text
replacement_ids_are_explicit=false
replacement_selection_basis=independent_replay_source_cell_exact_set_when_coordinates_present
verified_source_uid_count=4
verified_source_uid_closure_count=1
verified_source_cell_count=4
matched_submission_row_count=1
matched tracking row=Q866 (tracking metadata only)
new_numeric_arithmetic_invented=false
unselected_rows_preserved_bytewise_at_json_value_level=true
human_verified=false
promotion_allowed=false
```

The final Q866 evidence CSV contains exactly the four replayed UID/cell
records. Its source closure, rather than the question ID or answer value,
selects the row to overlay.

## Verification commands and results

```text
/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q tests/research/test_arg_extreme_period_variant_v1.py
24 passed

/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q
731 passed, 1 skipped

/home/dungle/.local/bin/rtk proxy .venv/bin/python scripts/research/replay_tax_payable_q866_v1.py \
  --asset artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl \
  --source-line-map artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json \
  --output artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_balance_sheet_ab_v1/independent_replay_q866_v1.json
status=PASS

/home/dungle/.local/bin/rtk proxy .venv/bin/python scripts/research/materialize_validated_route_overlay_v1.py \
  --base-dir artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q78_financial_liability_after_q77_generic_v1 \
  --source-dir artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_balance_sheet_ab_v1/variant/submission \
  --output-dir artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q79_tax_balance_after_q78_generic_v1 \
  --builder artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/snapshot/scripts/e2e/build_competition_submission_v1.py \
  --only-selected-routes \
  --independent-replay artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_balance_sheet_ab_v1/independent_replay_q866_v1.json \
  --independent-replay-route program_arg_extreme_period_v1
status=0; final validation records=1012; errors=[]

/home/dungle/.local/bin/rtk proxy unzip -tq artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q79_tax_balance_after_q78_generic_v1.zip
No errors detected in compressed data
```

`git diff --check` is the remaining workspace hygiene check for this handoff;
it must pass before treating the documentation/code slice as clean. The
working tree is shared and contains unrelated dirty work, so no unrelated
files were staged, reset, deleted or overwritten.

## Decision and authority

**Decision:** `KEEP` the generalized tax-payable balance-sheet family and the
q79 candidate for an independent scorer/holdout evaluation. The structural
delta is isolated, source-replayed and regression-free at the complete
population value level. Do not call it an accuracy gain until an official
scorer or independent gold set measures it.

**Authority status:** `CANDIDATE_ONLY`. No human semantic approval, strict
certificate, promotion, release authorization or leaderboard score is
established.
