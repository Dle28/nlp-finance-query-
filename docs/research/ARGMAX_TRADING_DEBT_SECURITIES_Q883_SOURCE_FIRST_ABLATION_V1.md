# Argmax trading-debt-securities family: Q883 source-first ablation v1

Date: 2026-08-31  
Status: full-population validated candidate; independent replay PASS; no
official score measured; authority remains `CANDIDATE_ONLY`.

## Outcome

The reusable family contract resolves period-extremum questions asking for the
balance of debt securities held for trading. It binds the exact row `Chứng
khoán nợ` inside the main note 8, `Chứng khoán kinh doanh`, selects the
current-period column, and requires a single consolidated scope and a single
million-VND multiplier across the requested years. It deliberately excludes
the similarly named investment-portfolio rows, listing-status child tables,
and interest-income rows.

The isolated integrated A/B comparison changes exactly one answer:

```text
Q883: 1220511000000.0 -> 2023.0
```

The candidate answer is supported by an independently replayed maximum over
three exact current-period source cells. This is a source/replay improvement
candidate, not a measured answer-accuracy gain: no independent gold set or
official scorer is available in the workspace.

## Hypothesis and family contract

The hypothesis is that this failure class is caused by lexical collision among
three accounting families sharing the phrase `Chứng khoán nợ`: trading
securities, investment securities, and income/provision disclosures. A
reusable trading-debt-securities rule should therefore:

1. recognize the metric phrase `Chứng khoán kinh doanh nợ` / `dư nợ chứng
   khoán kinh doanh nợ`;
2. require a financial-note or note-detail table whose source topic contains
   `Chứng khoán kinh doanh`;
3. bind the exact row label `Chứng khoán nợ`;
4. exclude source contexts containing `Tình trạng niêm yết`, investment-note
   headings, and generic debt/income contexts;
5. select the current-period column for each requested report year and keep the
   adjacent prior column only as a comparison value; and
6. require one `consolidated` scope and one source-declared multiplier. Here
   the table header explicitly declares `triệu đồng`, so the multiplier is
   `1,000,000` to VND. Retrieval rank, page number, numeric magnitude, and
   Question ID are not semantic or unit authority.

The raw extractor uses the same `financial_note` kind for the selected 2022,
2023, and 2025 tables. The implementation also keeps a note-detail extraction
variant in the same family only when the exact topic and row contract passes;
it does not widen the family to generic `debt_schedule` or
`financial_data_schedule` rows.

Q883 is tracking metadata for this audit. It does not control prediction,
routing, retrieval, parsing, formula selection, or answer selection.

## Control fingerprint

The isolated control is the active 80-replacement integrated candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q80_construction_payable_after_q79_generic_v1/`

Its fingerprints are:

- `submission.json` SHA-256:
  `82e24393473ab3ab691440ff73da4f1ca89661e558ca7dbd62bf8422ef225cf0`;
- `build_report.json` SHA-256:
  `514bb6ac68141c0d7172f9e145cd0b4a3a43f86ce33a0eea0aae1de0f3bb5f20`;
- ZIP SHA-256:
  `222fd4457d3f6c57a15b5c9da767fc95e27e6377611ab52faf693bd2c08ad6fb`;
- control Q883 evidence CSV SHA-256:
  `9dcb8eca80e014388699ba8ffa44072a48b6c4ffaf8e6f094090e7cab70e12ef`.

Control Q883 was the heuristic value `1220511000000.0`, selected from the
MBB separate table UID
`f64e295648ec027939f9e1c1a2a02ababbe2996fb952f6ba3db6975fe3d87e1f`, row 8,
column 2, labelled `Chứng khoán kinh doanh`, with source multiplier
`1,000,000`. That is not the exact `Chứng khoán nợ` row in the main
consolidated trading-securities note.

The control contains 1,012 submission rows and 1,012 diagnostic rows. Its
verification-class census is 1,010 `PARTIAL` and 2 `UNRESOLVED`; these labels
are not correctness labels.

## Candidate fingerprint

The full-population source candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_trading_debt_securities_ab_v1/variant/submission/`

Its fingerprints are:

- `submission.json` SHA-256:
  `5d5a8f8f5f2eb22807b3d1917d0b43ed3e12a67e49ddd872f2b994914dd24e95`;
- `build_report.json` SHA-256:
  `365d99fd68bbfe40287b4551f733eaeff6d9749034a19bb749fa95189cdb7370`;
- full source candidate validation: 1,012 records, 1,012 queries replayed,
  `errors=[]`;
- full source candidate structured asset: 146,246 tables;
- full source candidate accepted strict period-extreme records: 37.

The isolated materialized candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q81_trading_debt_after_q80_generic_v1/`

and the handoff ZIP is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q81_trading_debt_after_q80_generic_v1.zip`

Its fingerprints are:

- `submission.json` SHA-256:
  `934e1a7171cd095f75844e74e5f12d57cdaa78f4cdca9ee06c67ebd4ef326fd3`;
- `build_report.json` and `route_overlay_manifest.json` SHA-256:
  `ca39df95a8b4f0a48954dbba0bc4cc3a3cf9ad882c05e9250312dfb22bbbf267`;
- ZIP SHA-256:
  `dd9e2369786bb454f6857f3205bac68539f757f5aa2f3de133814657a5dc2d84`;
- ZIP size: 552,118 bytes;
- ZIP members: 1,013, including 1,012 evidence CSVs;
- candidate Q883 evidence CSV SHA-256:
  `3030469eb04ab8f31a5bb2514c718a1e4013607d2a4bfda82f764424acbbcfda`.

Implementation fingerprints:

- argmax adapter
  `scripts/research/run_arg_extreme_period_variant_v1.py`:
  `f4f74438c4db29c5239311af5899ae0cee0cec899a952550827562631c0a3126`;
- independent replay checker
  `scripts/research/replay_trading_debt_securities_q883_v1.py`:
  `ecf991b1d3eab8a2e802a157ad29816f05a42ed963dd2f8f467fb724e9ba63b4`;
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

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_trading_debt_securities_ab_v1/independent_replay_q883_v1.json`

Its SHA-256 is
`72145e7c80aaf6908f8fa40c8e10c351be4d733fd18a32d339a953d580d5519d`.
It reports `status=PASS`, `failures=[]`,
`answer_accuracy=NOT_MEASURED`, `execution_accuracy=NOT_MEASURED`,
`official_scorer=NOT_AVAILABLE`, `promotion_allowed=false`, and
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

The full A/B used the same frozen builder, source-first lookup, structured
asset, source-line map, question population, replay input, candidate files,
and strict-source settings in both arms. The q80 integrated candidate is the
isolated control; older artifacts are not used because they would combine
this change with inherited route changes.

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
integrity, and unchanged answer fields are engineering/provenance evidence;
none is substituted for independent answer scoring.

## Full-population A/B result

The final isolated comparison is q80 control -> q81 materialized candidate:

| Measure | Control q80 | Candidate q81 | Interpretation |
|---|---:|---:|---|
| population | 1,012 | 1,012 | same complete input |
| submission records | 1,012 | 1,012 | no missing records |
| validation queries | 1,012 | 1,012 | structural replay only |
| validation errors | 0 | 0 | technical gate passed |
| changed answer fields | — | 1 | Q883 only |
| changed complete rows | — | 1 | Q883 only |
| non-target changed rows | — | 0 | no observed collateral change |
| unchanged complete rows | — | 1,011 | value-level comparison |
| source-supported candidate improvements | — | 1 | Q883 family replay |
| source-supported regressions | — | 0 | no non-target change |
| semantic answer accuracy | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |
| execution accuracy | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |
| unresolved semantic correctness | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |

The only answer/tier change is:

| Family | Tracking row | Control | Candidate | Independent replay | Route |
|---|---:|---:|---:|---|---|
| trading debt-securities argmax | Q883 | `1220511000000.0` | `2023.0` | 2023 | `program_arg_extreme_period_v1` |

The candidate changes the Q883 evidence, pandas query, relevant table list,
and prediction tier as a coherent route replacement. Those fields are
candidate diagnostics and provenance, not accuracy labels. The verification
census remains 1,010 `PARTIAL` and 2 `UNRESOLVED` in both integrated arms.

The exact complete-row comparison is:

```text
submission rows: control=1012 candidate=1012 changed=1
changed tracking row: 883
Q883 answer: 1220511000000.0 -> 2023.0
Q883 tier: semantic_cell_heuristic -> program_arg_extreme_period_v1
non-target changes: 0
unchanged rows: 1011
```

## Independent replay and source closure

The replay checker is independent of the argmax adapter and verifies three
MBB consolidated source tables:

| Report year | Internal table UID | Source line | Row/column | Current value (million VND) | Current value (VND) |
|---:|---|---:|---:|---:|---:|
| 2022 | `0c124caed4e0beb07f895f45444f9ad92df62446e59af7605e070c53de765ad6` | 1556 | 1 / 1 | 4,070,884 | 4,070,884,000,000 |
| 2023 | `c923005e15b0c9494497aef0f1d0a315eeab7fd6d72b5b11044d53b085187d54` | 1477 | 1 / 1 | 44,095,180 | 44,095,180,000,000 |
| 2025 | `42dfc4a100ecd3e03fe4dfb4b02064a016582d04117c4721d91bbd9d912582e1` | 2036 | 1 / 1 | 4,375,694 | 4,375,694,000,000 |

The 2022 table has source SHA-256
`3ee9e2e49aec63aeedd3bbc08a9d064b77486d5d01b5196f1218b20030ca64d3` and
table SHA-256
`a6fdb2df90cbc9132ac5fd24f51c14d6146cb6036c84c90935d09cdc5f959a2f`.
Its character coordinates are `104358:105306` and byte coordinates are
`131463:132501`.

The 2023 table has source SHA-256
`249803144cdae3dadb09c5defd0cd7c324a4f4564f4d139203c29d8f98bdcb3c` and
table SHA-256
`40d0d0da3313674fed3aaaf546fac81468116c58f10fd75dde205880a0f937d2`.
Its character coordinates are `106943:107889` and byte coordinates are
`134856:135892`.

The 2025 table has source SHA-256
`47f2348913b400ea5a6280ff9d93f5a5e409ed45fffdf627c34da73c27d4618f` and
table SHA-256
`b1928e709ec6197f105d647191c66692846fe1c630c1bca623af3faf747abdcf`.
Its character coordinates are `124463:125490` and byte coordinates are
`157350:158473`.

All three tables are `financial_note` tables from note 8, have
`period_comparison` purpose, `consolidated` scope, and headers explicitly
declaring `triệu đồng`. The current/prior headers are respectively
`31/12/2022triệu đồng` / `31/12/2021triệu đồng`,
`31/12/2023triệu đồng` / `31/12/2022triệu đồng`, and
`31/12/2025 triệu đồng` / `31/12/2024 triệu đồng`.

The replay checks exact source and table hashes, recomputed table slices,
byte/character coordinates, source-line coordinates, report years, local
ordinals, page numbers, note-8 trading-securities context, exact row
uniqueness, current/prior headers, explicit million-VND unit, consolidated
scope, equal multipliers, and a unique Decimal maximum. The three source
multipliers are all `1,000,000`; no numeric-magnitude heuristic is used.

The generic materializer records:

```text
replacement_ids_are_explicit=false
replacement_selection_basis=independent_replay_source_cell_exact_set_when_coordinates_present
verified_source_uid_count=3
verified_source_uid_closure_count=1
verified_source_cell_count=3
matched_submission_row_count=1
matched tracking row=Q883 (tracking metadata only)
new_numeric_arithmetic_invented=false
unselected_rows_preserved_bytewise_at_json_value_level=true
human_verified=false
promotion_allowed=false
```

The final Q883 evidence CSV contains exactly the three replayed UID/cell
records. Its source closure, rather than the question ID or answer value,
selects the row to overlay.

## Verification commands and results

```text
/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q tests/research/test_arg_extreme_period_variant_v1.py
26 passed

/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q
743 passed, 1 skipped

/home/dungle/.local/bin/rtk proxy unzip -tq artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q81_trading_debt_after_q80_generic_v1.zip
No errors detected in compressed data
```

The independent replay command was:

```text
/home/dungle/.local/bin/rtk proxy .venv/bin/python scripts/research/replay_trading_debt_securities_q883_v1.py \
  --asset artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl \
  --source-line-map artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json \
  --output artifacts/runs/vifinqa_answer_optimization_20260830/argmax_trading_debt_securities_ab_v1/independent_replay_q883_v1.json
exit status: 0; status=PASS; failures=[]
```

Decision: `KEEP` the generalized family candidate for independent
scorer/holdout evaluation; authority remains `CANDIDATE_ONLY`. The next
research queue is a new residual family audit, with no Q883-specific
exception and no claim of official score gain.
