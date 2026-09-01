# Argmax ratio-period rebase q82 after q81

Ngày: 2026-08-31  
Lane: `FULL_POPULATION_VALIDATED` về execution scope; `CANDIDATE_ONLY` về
authority  
Phạm vi: toàn bộ snapshot ViFinQA gồm `1.012` câu

## Kết luận ngắn

q82 là bản materialized tiếp theo của lineage q81, bổ sung phần còn thiếu của
family “chọn năm có tỷ trọng/tỷ lệ lớn nhất qua nhiều năm”. Candidate v3 đã
được replay độc lập trên toàn bộ corpus và q82 chỉ copy các rows có exact
source-cell closure; không copy toàn bộ full-build output của nhánh cũ.

Khi so sánh trực tiếp với q81 mới nhất:

- `1.012/1.012` submission rows có ở cả hai arm, không thiếu ID;
- `5` complete rows thay đổi, và cả `5` đều đổi answer/tier;
- `1.007` complete rows giữ nguyên ở mức JSON value; non-target changes = `0`;
- 8 family rows được checker độc lập xác nhận, trong đó 3 rows đã có trong q81
  (`Q826,Q877,Q982`) và 5 rows là thay đổi mới trong q82;
- source replay: `PASS`, `8/8` supported questions, `37` verified source UIDs,
  `81` source cells, `0` failures;
- q82 overlay validation: `valid=true`, `records=1012`,
  `queries_replayed=1012`, `errors=[]`;
- ZIP: `1.013` members gồm `submission.json` và `1.012` evidence CSVs,
  `unzip -tq` exit `0`.

Không có independent gold hoặc official scorer. Vì vậy các thay đổi answer
trên là candidate evidence, không phải accuracy gain hay official score gain.

## Hypothesis/family

Failure class có thể tái sử dụng là câu hỏi yêu cầu chọn năm có ratio/share/
percentage lớn nhất giữa nhiều năm. Hợp đồng source-first phải:

1. nhận diện family theo semantic metric pattern, không theo `question_id`;
2. bind numerator/denominator vào đúng report year, scope, unit và current
   period column;
3. replay bằng `Decimal`, chọn một unique arg-extreme và từ chối tie/ambiguity;
4. yêu cầu source-cell provenance và source-line closure cho mọi operand;
5. giữ validation, evidence writing và authority policy ở canonical pipeline;
6. không cho replay/model/rank metadata tự cấp `VERIFIED`.

Tám family trong replay độc lập là `lease_land_cost_share`,
`production_factor_depreciation_share`, `deposit_interest_expense_share`,
`certificate_deposit_short_maturity_share`, `usd_long_term_loan_share`,
`net_on_balance_currency_asset_share`, `transport_segment_asset_share` và
`management_depreciation_share`.

## Experiment contract

### Control fingerprint

Control là q81 integrated route overlay, được chọn vì đây là lineage mới nhất
trước q82; không dùng q72 hoặc các overlay cũ làm control:

- submission:
  `934e1a7171cd095f75844e74e5f12d57cdaa78f4cdca9ee06c67ebd4ef326fd3`;
- ZIP:
  `dd9e2369786bb454f6857f3205bac68539f757f5aa2f3de133814657a5dc2d84`;
- build report/route manifest:
  `ca39df95a8b4f0a48954dbba0bc4cc3a3cf9ad882c05e9250312dfb22bbbf267`;
- route overrides: `81`;
- validation: `1012/1012`, `errors=[]`.

### Candidate fingerprint

Candidate q82 được tạo bởi `materialize_validated_route_overlay_v1.py`, với
replacement selection từ independent replay exact source-cell set. Chọn rows
không dùng explicit ID allowlist (`replacement_ids_are_explicit=false`):

- submission:
  `1c7f851126e7568ab2af3d8723086971db7472f64450453b787df5a41fb6b16e`;
- ZIP:
  `589bdadc0a1cb42e63357b52f0166fec83d6cf23154e5d390d90666d927967d8`;
- build report/route manifest:
  `ec7e593068670a67341741d7fbcd1feeeceee51664370ccf24f89758d53c819c`;
- total route overrides: `89` (`81` inherited + `8` independently replayed);
- independent replay:
  `d08f19b8cc2f0bcf9d3a376615eb4f49568c4d86915232f9b21380db2279faf8`;
- independent replay recheck:
  `9aa5d40effd7e49bde5f9008c57c14869c6c6aa579028529b1b1a251f6547d85`;
- source candidate submission copied from:
  `5e31b1e3aeff895e6aa4cb7184271dd88989565777451d507d33f539312adb0f`;
- source builder report:
  `8badd7ab3a296efaa8f9ed213112b2000a369d6be42c247cb8b42f3ed6a2a928`;
- source lookup implementation:
  `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`.

The source full build was from an older isolated ratio snapshot. Only the 8
records selected by independent source-cell closure were copied into q81;
unselected q81 rows remain unchanged.

### Population and split

- population: all `1.012` questions from the frozen input;
- structured tables: `146.246`;
- no question removed or held out from this structural A/B;
- discovery/census was used to formulate the family contract, then the
  materialization and comparison covered the complete population;
- no selected-question tuning is used for an accuracy claim;
- technical regression tolerance: zero non-target JSON-value changes and zero
  source replay failures;
- semantic regression tolerance: `NOT_MEASURED` without gold/scorer.

### Acceptance metrics

Primary acceptance metrics are `ANSWER_ACCURACY` and
`EXECUTION_ACCURACY` from an independent gold/scorer. They are unavailable in
this environment. Secondary replay, coverage and packaging measurements below
are diagnostics only.

## Scorer/gold

- gold source: `NOT_AVAILABLE`;
- official scorer: `NOT_AVAILABLE`;
- scorer command: `NOT_EXECUTED`;
- scorer exit status: `NOT_MEASURED`.

The q72 historical official receipt remains a separate baseline
(`ANSWER_ACCURACY` and `EXECUTION_ACCURACY` both `0.2174`), but it is not an
evaluation of q82 and must not be used as an isolated q81→q82 delta.

## Mandatory A/B report

```text
Hypothesis/family: reusable source-first argmax ratio-period family; q82 is a q81 rebase of 8 independently replayed family rows
Control fingerprint: q81 submission=934e1a7171cd095f75844e74e5f12d57cdaa78f4cdca9ee06c67ebd4ef326fd3; zip=dd9e2369786bb454f6857f3205bac68539f757f5aa2f3de133814657a5dc2d84; report=ca39df95a8b4f0a48954dbba0bc4cc3a3cf9ad882c05e9250312dfb22bbbf267
Candidate fingerprint: q82 submission=1c7f851126e7568ab2af3d8723086971db7472f64450453b787df5a41fb6b16e; zip=589bdadc0a1cb42e63357b52f0166fec83d6cf23154e5d390d90666d927967d8; report=ec7e593068670a67341741d7fbcd1feeeceee51664370ccf24f89758d53c819c; independent_replay=d08f19b8cc2f0bcf9d3a376615eb4f49568c4d86915232f9b21380db2279faf8
Population and split: complete frozen population; 1,012 questions; 146,246 structured tables; no gold split available
Scorer/gold: NOT_AVAILABLE; official scorer not executed
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
Improved / regressed / unchanged / unresolved: improved=NOT_MEASURED; regressed=NOT_MEASURED; unchanged=1,007 complete JSON rows; changed-answer=5; technical unresolved=0 in overlay validation; semantic unresolved=NOT_MEASURED
Family-level results: 8/8 ratio-family rows independently replayed PASS; 5 new q81→q82 answer changes; 3 family rows already present in q81; non-target changes=0
Diagnostic-only metrics: overlay validation 1,012/1,012 with errors=[]; independent replay 8/8; 37 verified source UIDs; 81 source cells; ZIP test exit=0; generic completion gate exit=1 due overlay schema lifecycle fields missing
Artifact paths and hashes: see Artifact paths and hashes below
Decision: KEEP
Authority status: CANDIDATE_ONLY
```

## Population A/B counts

| Measure | q81 control | q82 candidate | Interpretation |
|---|---:|---:|---|
| Input questions | 1,012 | 1,012 | same frozen population |
| Structured tables | 146,246 | 146,246 | same full corpus |
| Submission records | 1,012 | 1,012 | no missing IDs |
| Overlay validation records | 1,012 | 1,012 | technical validation only |
| Queries replayed | 1,012 | 1,012 | technical validation only |
| Validation errors | 0 | 0 | overlay report validation |
| Route overrides | 81 | 89 | q82 adds 8 source-closed family routes |
| Complete-row changes | — | 5 | Q849, Q885, Q968, Q980, Q1004 |
| Answer-field changes | — | 5 | diagnostic changed answers |
| Prediction-tier changes | — | 5 | target ratio route only |
| Unchanged complete rows | — | 1,007 | non-target changes = 0 |
| Improved answers | — | `NOT_MEASURED` | no gold/scorer |
| Regressed answers | — | `NOT_MEASURED` | no gold/scorer |
| Independent ratio replay | — | 8/8 PASS | source consistency diagnostic |
| Verified source UIDs / cells | — | 37 / 81 | exact closure diagnostic |

Structural unchanged/changed counts are not correctness counts.

## Family-level results

The year below is the unique winner in the independent Decimal/source replay.
The q81 and q82 answer fields are shown exactly as materialized; this table is
not a gold evaluation.

| Family | Tracking Q | Scope | Independent winner | q81 answer | q82 answer | q81→q82 | Replay |
|---|---:|---|---:|---:|---:|---|---|
| `lease_land_cost_share` | 826 | KBC consolidated | 2016 | 2016.0 | 2016.0 | unchanged | PASS |
| `production_factor_depreciation_share` | 849 | SAB consolidated | 2020 | 68.78 | 2020.0 | new change | PASS |
| `deposit_interest_expense_share` | 877 | HDB consolidated | 2025 | 2025.0 | 2025.0 | unchanged | PASS |
| `certificate_deposit_short_maturity_share` | 885 | STB consolidated | 2021 | 3.0 | 2021.0 | new change | PASS |
| `usd_long_term_loan_share` | 968 | POW separate | 2017 | 12950216434336.0 | 2017.0 | new change | PASS |
| `net_on_balance_currency_asset_share` | 980 | HDB consolidated | 2022 | 7355000000.0 | 2022.0 | new change | PASS |
| `transport_segment_asset_share` | 982 | PVT separate | 2025 | 2025.0 | 2025.0 | unchanged | PASS |
| `management_depreciation_share` | 1004 | IJC separate | 2023 | 737084353.0 | 2023.0 | new change | PASS |

The three unchanged rows were already present in q81. The five new changes are
exactly the rows listed in the direct JSON diff; no additional row changed.

## Changed-answer detail

| Q | Family | q81 answer | q82 answer | q82 tier |
|---:|---|---:|---:|---|
| 849 | `production_factor_depreciation_share` | 68.78 | 2020.0 | `program_arg_extreme_ratio_period_v1` |
| 885 | `certificate_deposit_short_maturity_share` | 3.0 | 2021.0 | `program_arg_extreme_ratio_period_v1` |
| 968 | `usd_long_term_loan_share` | 12950216434336.0 | 2017.0 | `program_arg_extreme_ratio_period_v1` |
| 980 | `net_on_balance_currency_asset_share` | 7355000000.0 | 2022.0 | `program_arg_extreme_ratio_period_v1` |
| 1004 | `management_depreciation_share` | 737084353.0 | 2023.0 | `program_arg_extreme_ratio_period_v1` |

These five answer changes are neither labelled wins nor losses without an
independent scorer or gold set.

## Diagnostic-only validation

The q82 overlay report has:

- `validation.valid=true`;
- `records=1012`;
- `queries_replayed=1012`;
- `errors=[]`;
- `inherited_replacement_count=81`;
- `replacement_count=8`;
- `total_replacement_count=89`;
- `replacement_ids_are_explicit=false`;
- `replacement_selection_basis=independent_replay_source_cell_exact_set_when_coordinates_present`;
- `numeric_values_copied_from_source_build=true`;
- `new_numeric_arithmetic_invented=false`;
- `unselected_rows_preserved_bytewise_at_json_value_level=true`;
- `human_verified=false` and `promotion_allowed=false`.

The independent replay protocol is
`vifinqa_independent_arg_extreme_ratio_replay_v2`. It scans the full
structured-table asset, binds periods/headers, replays Decimal ratios, checks
scope/unit/family contracts, verifies source hash and line-map closure, and
reports `PASS` for all 8 supported rows. This is source/replay consistency, not
answer accuracy.

The generic completion gate was also run:

```text
command: .venv/bin/python scripts/research/run_pipeline_completion_gate_v1.py --output-dir <q82> --expected-count 1012
exit: 1
gate_passed: false
errors:
  - build_report.json: question_count=None expected=1012
  - build_report.json: missing zip_path
  - build_report.json: best_candidate_ledger_count=None expected=1012
```

This is a schema mismatch because q82 is a route-overlay report
(`vifinqa_route_overlay_report_v1`), not a canonical full-build report. The
overlay-specific validation and ZIP test passed; the generic gate failure
must be repaired or explicitly adapted before any release workflow.

## Artifact paths and hashes

### q81 control

- [q81 submission.json](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q81_trading_debt_after_q80_generic_v1/submission.json)
- [q81 build_report.json](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q81_trading_debt_after_q80_generic_v1/build_report.json)
- [q81 route_overlay_manifest.json](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q81_trading_debt_after_q80_generic_v1/route_overlay_manifest.json)
- q81 submission SHA-256: `934e1a7171cd095f75844e74e5f12d57cdaa78f4cdca9ee06c67ebd4ef326fd3`
- q81 ZIP SHA-256: `dd9e2369786bb454f6857f3205bac68539f757f5aa2f3de133814657a5dc2d84`
- q81 report/manifest SHA-256: `ca39df95a8b4f0a48954dbba0bc4cc3a3cf9ad882c05e9250312dfb22bbbf267`

### q82 candidate

- [q82 output directory](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q82_ratio_extended_after_q81_v1)
- [q82 submission.json](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q82_ratio_extended_after_q81_v1/submission.json)
- [q82 build_report.json](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q82_ratio_extended_after_q81_v1/build_report.json)
- [q82 route_overlay_manifest.json](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q82_ratio_extended_after_q81_v1/route_overlay_manifest.json)
- [q82 ZIP](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q82_ratio_extended_after_q81_v1.zip)
- [q82 experiment contract](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q82_ratio_extended_after_q81_v1/EXPERIMENT_CONTRACT.md)
- q82 submission SHA-256: `1c7f851126e7568ab2af3d8723086971db7472f64450453b787df5a41fb6b16e`
- q82 ZIP SHA-256: `589bdadc0a1cb42e63357b52f0166fec83d6cf23154e5d390d90666d927967d8`
- q82 report/manifest SHA-256: `ec7e593068670a67341741d7fbcd1feeeceee51664370ccf24f89758d53c819c`

### Independent source replay

- [primary independent replay](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/independent_replay_ratio_families_v3.json)
- [independent replay recheck](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/independent_replay_ratio_families_v3_recheck.json)
- primary replay SHA-256: `d08f19b8cc2f0bcf9d3a376615eb4f49568c4d86915232f9b21380db2279faf8`
- recheck SHA-256: `9aa5d40effd7e49bde5f9008c57c14869c6c6aa579028529b1b1a251f6547d85`
- protocol: `vifinqa_independent_arg_extreme_ratio_replay_v2`
- status: `PASS`; supported/replayed: `8/8`; failures: `0`
- verified source UIDs/cells: `37/81`

## Limits, decision and next gate

No QID-specific exceptions were introduced. Q828 and other ratio-like
questions without a complete numerator/denominator contract remain unresolved
discovery evidence and were not forced into q82.

The q82 candidate is kept for the next independent scored evaluation. Before
calling it an accuracy improvement, run the exact q81 and q82 ZIPs through the
same official scorer or a frozen independent gold evaluator and report answer
and execution regressions at population and family level. Separately, repair
or define an overlay-aware completion-gate schema so the currently explicit
technical gate mismatch cannot reach release.

Decision: `KEEP`  
Authority status: `CANDIDATE_ONLY`  
`promotion_allowed=false`; `human_verified=false`.
