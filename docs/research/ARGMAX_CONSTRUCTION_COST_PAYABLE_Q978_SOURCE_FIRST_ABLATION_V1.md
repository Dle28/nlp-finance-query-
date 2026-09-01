# Argmax short-term construction-cost payable family: Q978 source-first ablation v1

Date: 2026-08-31  
Status: full-population validated candidate; independent replay PASS; no
official score measured; authority remains `CANDIDATE_ONLY`.

## Outcome

The reusable family contract resolves period-extremum questions asking for
short-term construction costs payable. It binds the exact row `Chi phí xây
dựng` inside a payable-note context, recognizes both directly titled
short-term payable notes and schedules reconstructed with an `a. Ngắn hạn` /
`b. Dài hạn` hierarchy, selects the current-period column, and requires one
consolidated scope and one explicit VND document declaration across the
requested years. It rejects the previously selected NVL cash/asset cell.

The isolated integrated A/B comparison changes exactly one answer:

```text
Q978: 21644596460.0 -> 2025.0
```

The candidate answer is supported by an independently replayed maximum over
three exact current-period source cells. This is a source/replay improvement
candidate, not a measured answer-accuracy gain: no independent gold set or
official scorer is available in the workspace.

## Hypothesis and family contract

The hypothesis is that this failure class is caused by a combination of
metric-family ambiguity, a reconstructed table kind, and an incomplete
consolidated-document binding. A reusable construction-payable rule should
therefore:

1. recognize the canonical metric and the Vietnamese aliases for `chi phí xây
   dựng`, `phải trả`, and `ngắn hạn`;
2. require a financial-note or financial-note-detail context containing
   `chi phí phải trả`, or a financial-data schedule with the same payable
   context and an unambiguous short-term hierarchy;
3. bind the exact accounting row label `Chi phí xây dựng`, rather than a
   semantically similar retrieved row or a Question ID;
4. select the current-period column and retain the adjacent opening/prior
   column only as a comparison value;
5. require one `consolidated` scope, one multiplier, and the same metric row
   family across all requested years; and
6. obtain the VND multiplier from a bounded source declaration or an exact
   document-level accounting-currency sentence. Numeric magnitude, page
   number, retrieval rank, and Question ID are not unit evidence.

The 2025 table is a reconstructed schedule (`financial_data_schedule`) whose
source context has the rows `a. Ngắn hạn`, `Chi phí xây dựng`, and `b. Dài
hạn`. The family contract collapses that safe, explicitly hierarchical
schedule into the same semantic `financial note` comparison family. This is a
generic table-context rule, not a Q978-specific exception.

Q978 is tracking metadata for this audit. It does not control prediction,
routing, retrieval, parsing, formula selection, or answer selection.

## Control fingerprint

The isolated control is the active 79-replacement integrated candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q79_tax_balance_after_q78_generic_v1/`

Its fingerprints are:

- `submission.json` SHA-256:
  `5cf7ee85c3204f441e47511e3b18a38b18beac3194f5904a4971c4d064d3475f`;
- `build_report.json` SHA-256:
  `9c3ad731337bb8804e2eea6751d6ce6264ba14c068218969e251d881634ad4b1`;
- ZIP SHA-256:
  `045beaacacc5205cd25cc2b59bd55fb9db575bb28dbc7f9a84f317606886e83a`;
- control Q978 evidence CSV SHA-256:
  `91705d6c161fbd0c5e5a6daead9114a968ac4aa4e844a6fcb1b42e1b055aa3d6`.

Control Q978 was the heuristic value `21644596460.0`, selected from internal
table UID
`996db4ef342bd92247ffc0f6b1378df3c888eadaf4ae05c2c47a6da06cb49c89`, row 2,
column 1, labelled `Tiền và các khoản tương đương tiền`. Its relevant-document
and table set also mixed NVL separate/consolidated candidates. That cell is
not one of the three independently replayed construction-payable cells.

The control contains 1,012 submission rows and 1,012 diagnostic rows. Its
verification-class census is 1,010 `PARTIAL` and 2 `UNRESOLVED`; these labels
are not correctness labels.

## Candidate fingerprint

The full-population source candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_construction_payable_ab_v1/variant/submission/`

Its fingerprints are:

- `submission.json` SHA-256:
  `72b7ddbb0c7c8ccadf264fc746fed016be19d247d5d689b623a58ad8b968e40f`;
- `build_report.json` SHA-256:
  `f687871ae1d997ad42622f4c5d93935d2576cbbb7c58ad3000cf6c144822c033`;
- full source candidate validation: 1,012 records, 1,012 queries replayed,
  `errors=[]`;
- full source candidate structured asset: 146,246 tables;
- full source candidate accepted strict period-extreme records: 36;
- full source candidate source-unit diagnostics: 1 document-currency
  construction-payable fallback and 2 raw-window fallbacks.

The isolated materialized candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q80_construction_payable_after_q79_generic_v1/`

and the handoff ZIP is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q80_construction_payable_after_q79_generic_v1.zip`

Its fingerprints are:

- `submission.json` SHA-256:
  `82e24393473ab3ab691440ff73da4f1ca89661e558ca7dbd62bf8422ef225cf0`;
- `build_report.json` and `route_overlay_manifest.json` SHA-256:
  `514bb6ac68141c0d7172f9e145cd0b4a3a43f86ce33a0eea0aae1de0f3bb5f20`;
- ZIP SHA-256:
  `222fd4457d3f6c57a15b5c9da767fc95e27e6377611ab52faf693bd2c08ad6fb`;
- ZIP size: 551,964 bytes;
- ZIP members: 1,013, including 1,012 evidence CSVs;
- candidate Q978 evidence CSV SHA-256:
  `1d9c47b97a0c7a95f41d9e71de0047004ac2b0963a196beb94ec82533a7a6319`.

Implementation fingerprints:

- argmax adapter
  `scripts/research/run_arg_extreme_period_variant_v1.py`:
  `8a3724ef695fbb94004c0f341f5315ed5043f4ec4e7c7fec7dbbc17edb59452f`;
- independent replay checker
  `scripts/research/replay_construction_cost_payable_q978_v1.py`:
  `60762bfc5a854bb1480a5cc7dece94829c62c44f30523333e325949ed4fc4017`;
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

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_construction_payable_ab_v1/independent_replay_q978_v1.json`

Its SHA-256 is
`0aba10a4b36456224cdf54d65e69d2dbf9ce06e30955587495459d5f4ccfed56`.
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

The full A/B was run with the same frozen builder, source-first lookup,
structured asset, source-line map, question population, replay input,
candidate files, and strict-source settings. The q79 integrated candidate is
the isolated control; a comparison with an older pre-q79 artifact would mix
inherited route changes and is not used here.

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

The final isolated comparison is q79 control -> q80 materialized candidate:

| Measure | Control q79 | Candidate q80 | Interpretation |
|---|---:|---:|---|
| population | 1,012 | 1,012 | same complete input |
| submission records | 1,012 | 1,012 | no missing records |
| validation queries | 1,012 | 1,012 | structural replay only |
| validation errors | 0 | 0 | technical gate passed |
| changed answer fields | — | 1 | Q978 only |
| changed complete rows | — | 1 | Q978 only |
| non-target changed rows | — | 0 | no observed collateral change |
| unchanged complete rows | — | 1,011 | value-level comparison |
| source-supported candidate improvements | — | 1 | Q978 family replay |
| source-supported regressions | — | 0 | no non-target change |
| semantic answer accuracy | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |
| execution accuracy | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |
| unresolved semantic correctness | `NOT_MEASURED` | `NOT_MEASURED` | no scorer/gold |

The only answer/tier change is:

| Family | Tracking row | Control | Candidate | Independent replay | Route |
|---|---:|---:|---:|---|---|
| short-term construction-cost payable argmax | Q978 | `21644596460.0` | `2025.0` | 2025 | `program_arg_extreme_period_v1` |

The candidate changes the Q978 evidence, pandas query, relevant table list,
and prediction tier as a coherent route replacement. Those fields are
candidate diagnostics and provenance, not accuracy labels. The verification
census remains 1,010 `PARTIAL` and 2 `UNRESOLVED` in both integrated arms.

The exact complete-row comparison is:

```text
submission rows: control=1012 candidate=1012 changed=1
changed tracking row: 978
Q978 answer: 21644596460.0 -> 2025.0
Q978 tier: semantic_cell_heuristic -> program_arg_extreme_period_v1
non-target changes: 0
unchanged rows: 1011
```

## Independent replay and source closure

The replay checker is independent of the argmax adapter and verifies three
NVL consolidated source tables:

| Report year | Internal table UID | Source line | Row/column | Current value (VND) | Prior/opening value (VND) |
|---:|---|---:|---:|---:|---:|
| 2020 | `bf71c88bf10549ebb44b8b6ca532a18fa373b438ca866f909edd4e22999d073f` | 1575 | 1 / 1 | 1,761,909,529,797 | 1,661,156,307,763 |
| 2022 | `73bf47b43dacdfe64542eb60733b0c7103bc5b61c01ac070719241bd016f957e` | 1612 | 1 / 1 | 3,817,192,873,315 | 3,254,716,258,582 |
| 2025 | `08662467583dc1c82bae5002d507a130e7403a465797c4f3d97814b1439be9bc` | 2264 | 3 / 1 | 4,735,317,291,614 | 4,244,216,774,226 |

The 2020 table has source SHA-256
`81ce9140e96d4d82478c614994fa83f8fe23b6098e9b8dceef4cf3e273983746` and
table SHA-256
`f6da7544d7795778156027d24cb49d9c3e8093d2318089e4c072cf8c0473d64a`.
The 2022 table has source SHA-256
`b1b208b9001cb9b45cfb6677966e8f364c236dcc62e84979df47f29c662c958e` and
table SHA-256
`af85ff6efa75c6d9d3ec45a7c0e31ae3f7962547a4b1a6a495bb341181beffcd`.
The 2025 table has source SHA-256
`958bceb95853d5e4709276e75747a234bc2eb4dc973957c66e6e6f8ceb386f28` and
table SHA-256
`076dbf25a87e4d08e81a715039197d069b1b79825f39085cd32f2e4262336929`.

The replay checks exact source and table hashes, recomputed table slices,
byte/character coordinates, source-line coordinates, report years, local
ordinals, page numbers, payable-note context, the exact construction-cost
row, short-term hierarchy, consolidated scope, current/prior headers,
document-level VND declarations, equal multipliers, and a unique Decimal
maximum. The three source multipliers are all `1`; no numeric-magnitude
heuristic is used.

The 2020 and 2022 headers are respectively
`31.12.2020VND` / `31.12.2019VND` and
`31.12.2022VND` / `31.12.2021VND`. The 2025 schedule headers are
`31/12/2025` / `01/01/2025`; the latter is explicitly treated as the
opening comparison column for that schedule, not relabelled as 2024.

The generic materializer records:

```text
replacement_ids_are_explicit=false
replacement_selection_basis=independent_replay_source_cell_exact_set_when_coordinates_present
verified_source_uid_count=3
verified_source_uid_closure_count=1
verified_source_cell_count=3
matched_submission_row_count=1
matched tracking row=Q978 (tracking metadata only)
new_numeric_arithmetic_invented=false
unselected_rows_preserved_bytewise_at_json_value_level=true
human_verified=false
promotion_allowed=false
```

The final Q978 evidence CSV contains exactly the three replayed UID/cell
records. Its source closure, rather than the question ID or answer value,
selects the row to overlay.

## Verification commands and results

```text
/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q tests/research/test_arg_extreme_period_variant_v1.py
25 passed

/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q
736 passed, 1 skipped

/home/dungle/.local/bin/rtk proxy unzip -tq artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q80_construction_payable_after_q79_generic_v1.zip
No errors detected in compressed data
```

The independent replay command was:

```text
/home/dungle/.local/bin/rtk proxy .venv/bin/python scripts/research/replay_construction_cost_payable_q978_v1.py \
  --asset artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl \
  --source-line-map artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json \
  --output artifacts/runs/vifinqa_answer_optimization_20260830/argmax_construction_payable_ab_v1/independent_replay_q978_v1.json
exit status: 0; status=PASS; failures=[]
```

Decision: `KEEP` the generalized family candidate for independent
scorer/holdout evaluation; authority remains `CANDIDATE_ONLY`. The next
research queue is the unresolved MBB trading-securities-debt residual family
(Q883), with no Q978-specific exception and no claim of official score gain.
