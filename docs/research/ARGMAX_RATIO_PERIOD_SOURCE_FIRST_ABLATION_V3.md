# Argmax ratio-period source-first ablation v3

Ngày: 2026-08-31
Lane: `FULL_POPULATION_VALIDATED` về execution scope; `CANDIDATE_ONLY` về
authority
Phạm vi: toàn bộ snapshot ViFinQA gồm 1.012 câu

## Kết luận ngắn

Đã đóng một candidate family-level cho dạng câu hỏi chọn năm có tỷ trọng
cao nhất giữa nhiều năm. Candidate dùng cùng một executor `argmax` cho tám
family có source contract cụ thể, chạy trên toàn bộ snapshot 1.012 câu, rồi
được kiểm tra lại bởi checker độc lập không import adapter.

Kết quả kỹ thuật:

- `8/8` family/row được adapter nhận diện và replay độc lập;
- `1012/1012` record và query được canonical builder replay, `errors=[]` ở
  cả control và candidate;
- A/B cùng snapshot thay đổi đúng `8` answer rows:
  `Q826,Q849,Q877,Q885,Q968,Q980,Q982,Q1004`;
- `1004` row ngoài target không đổi ở mức JSON value; overlay cuối cũng giữ
  nguyên toàn bộ row/evidence ngoài tám row được replay;
- overlay materialized có `77` replacement records (`69` inherited + `8`
  ratio-family), ZIP có `1.013` members (`submission.json` + `1.012` CSV),
  `testzip=None`.

Chưa có independent gold set hoặc official scorer trong snapshot này. Vì vậy
`ANSWER_ACCURACY`, `EXECUTION_ACCURACY`, số answer được cải thiện và số answer
bị regression đều là `NOT_MEASURED`; không được gọi các thay đổi trên là
accuracy gain hay official score gain. Artifact được giữ ở
`CANDIDATE_ONLY`, không promotion/release.

## Hypothesis/family

Hypothesis: các câu có ngữ nghĩa “tỷ trọng/tỷ lệ/phần trăm của metric A so với
metric B qua nhiều năm, chọn năm lớn nhất” tạo thành một failure class dùng lại
được. Cách xử lý đúng là nhận diện phenomenon/family và áp dụng một hợp đồng
nguồn chung, không phải thêm ngoại lệ theo `question_id`.

Candidate contract:

1. Nhận diện family từ wording và semantic metric pattern; `question_id` chỉ
   xuất hiện trong trace/audit/materialization, không điều khiển prediction,
   routing, retrieval, parsing, formula selection hay answer selection.
2. Mỗi numerator/denominator phải thuộc đúng report year của operand, cùng
   scope, cùng source multiplier; cột hiện tại phải được bind bằng header.
3. Tính ratio bằng `Decimal`, dùng absolute hoặc signed mode đúng contract của
   family; chọn một winner duy nhất bằng `max` arg-extreme.
4. Từ chối cặp mơ hồ, tie, thiếu operand, thiếu current-period anchor, thiếu
   unit declaration hoặc thiếu provenance.
5. Với denominator là tổng các component, mọi component dùng cho arithmetic
   phải được emit như source cells và phải thỏa control sum. Với Q968,
   validation rows riêng không được trộn vào exact answer-cell closure.
6. Canonical builder vẫn sở hữu evidence writing, Decimal/Pandas replay và
   authority policy; adapter không tự cấp `VERIFIED`.

Tám family được freeze trong candidate:

- `lease_land_cost_share` — KBC: giá vốn đất/cơ sở hạ tầng cho thuê chia cho
  giá vốn hàng bán và dịch vụ cung cấp.
- `deposit_interest_expense_share` — HDB: chi phí lãi tiền gửi chia cho tổng
  chi phí lãi.
- `transport_segment_asset_share` — PVT: tài sản bộ phận dịch vụ vận tải
  chia cho tổng tài sản.
- `production_factor_depreciation_share` — SAB: chi phí khấu hao và phân bổ
  chia cho tổng chi phí sản xuất/kinh doanh theo yếu tố; denominator được
  kiểm bằng tổng bốn component còn lại.
- `certificate_deposit_short_maturity_share` — STB: bucket chứng chỉ tiền
  gửi dưới 12 tháng so với các bucket maturity còn lại; tổng child phải khớp
  parent khi parent hiện diện.
- `usd_long_term_loan_share` — POW: khoản vay dài hạn bằng USD chia cho tổng
  khoản vay dài hạn; USD + VND phải khớp total. VND rows là
  `validation_sources`, không phải answer-cell closure.
- `net_on_balance_currency_asset_share` — HDB: trạng thái tiền tệ nội bảng
  chia cho tổng tài sản.
- `management_depreciation_share` — IJC: khấu hao tài sản cố định chia cho
  tổng chi phí quản lý doanh nghiệp.

## Experiment contract

### Control fingerprint

Control là run frozen trong snapshot `argmax_ratio_period_ab_v2/snapshot`, với
ratio adapter tắt. Control được dùng cho A/B chính là:

- Submission: `ea47bdc1929eb70523b15a8a1b232074893810fd6ecac103ceb2dd987df42bc6`.
- ZIP: `df32af384745deebbb2795479717b35972bd22681061f2259e8c19160f89b442`.
- Build report: `9714f828b4f106f17020de7b55501c2843112d6326ad735670b9dc7229b68d6c`.
- Run manifest: `45310e403d7b28449510266da437013802078ff36f39a70e5f3a2358be0ab7ae`.

### Candidate fingerprint

Candidate dùng cùng canonical builder/snapshot nhưng bật adapter strict source
contract:

- Protocol: `vifinqa_arg_extreme_ratio_period_strict_source_v2`.
- Adapter SHA-256:
  `44959275c77b6db986b3271d8fbc0a352c2cc7f98fb67096748d7b9bd65c6da2`.
- Independent checker SHA-256:
  `b805b5ce3d92079cf89b2bcd57d0612751d47a6d653565964123cffe664947f0`.
- Candidate submission:
  `5e31b1e3aeff895e6aa4cb7184271dd88989565777451d507d33f539312adb0f`.
- Candidate ZIP:
  `a26df9710a9e55a6f5ab1b7d20c66aa4141afc98863be509dbd5da12eb3ba359`.
- Candidate build report:
  `504bcc03681bd74f090b2fb8b9a91fb6ecab8af7e721e6f65f720b86691d264f`.
- Candidate run manifest:
  `be0f2835d8aaba9da407dd078e529830655d737ae0520834b434be0abd63da78`.
- Candidate ratio trace:
  `d331886537b9425612b35737ac4c4f420b2ec80de75900ba2c112f563ba34d48`.

Candidate configuration khác control chỉ ở ratio-family adapter và strict
typed-plan/source contract. Control và candidate dùng chung:

- canonical builder:
  `8badd7ab3a296efaa8f9ed213112b2000a369d6be42c247cb8b42f3ed6a2a928`;
- source-first lookup:
  `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`;
- candidate-plan selector:
  `aded34a7aa7e68a6a5dbb57c1dc873b6af33425c59b5a362baf852464e07df29`;
- candidate-validity model:
  `19fe579a16c480efa0097e39fa624b600a943bf72b48f164378a073de53a155a`;
- questions input:
  `64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0`;
- replay input:
  `ccb61012779a64c96a31e07dc875615fdc04973e4918edee35907a3090112372`;
- typed operand plans:
  `212097bf0a892d9cf7ab703b9f690b854c9fbb36ce3cc07ebd73c390c22c0319`;
- full structured-table asset:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source line map:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.

Run manifest cũng ghi rõ các nguồn không được yêu cầu trong cả hai arm:
dense index, fine-tuned generator/reranker và prompt contract đều là
`NOT_REQUESTED`; không có hash giả định cho các artifact này.

### Population and split

- Population: all `1,012` questions from the frozen input manifest.
- Structured-table corpus: `146,246` tables; filter mode `all`.
- Split policy: discovery/census was used to formulate the family contract;
  implementation was then frozen and run against the complete population.
  Không dùng selected-ID tuning để claim accuracy.
- No question was removed from the A/B population. Non-target rows remain in
  both arms.
- Technical regression tolerance: zero non-target row changes and zero
  full-population builder errors. Semantic regression tolerance cannot be
  measured without gold/scorer.

### Acceptance metrics

Primary acceptance metrics required by the workspace contract are
`ANSWER_ACCURACY` and `EXECUTION_ACCURACY` from an independent gold/scorer.
They are not available here and therefore remain `NOT_MEASURED`.

Secondary engineering gates are explicitly diagnostic: complete population,
canonical replay, line-map closure, independent source replay, unique source
closure, exact A/B scope, and valid ZIP packaging.

## Scorer/gold

- Gold source: `NOT_AVAILABLE`.
- Official scorer: `NOT_AVAILABLE`.
- Scorer command: `NOT_EXECUTED`; không có independent scorer/gold evaluator
  trong snapshot hiện tại.
- Scorer exit status: `NOT_MEASURED`.
- Canonical builder validation and independent Decimal/source replay are
  engineering evidence only; chúng không thay thế answer scorer.

## Mandatory A/B report

`ANSWER_ACCURACY` và `EXECUTION_ACCURACY` dưới đây không được suy ra từ answer
diff, tier diff, Decimal replay, exact-cell coverage, record count hay ZIP
validity.

```text
Hypothesis/family: reusable source-first argmax ratio-period family with eight strict source contracts
Control fingerprint: submission=ea47bdc1929eb70523b15a8a1b232074893810fd6ecac103ceb2dd987df42bc6; zip=df32af384745deebbb2795479717b35972bd22681061f2259e8c19160f89b442; report=9714f828b4f106f17020de7b55501c2843112d6326ad735670b9dc7229b68d6c
Candidate fingerprint: adapter=44959275c77b6db986b3271d8fbc0a352c2cc7f98fb67096748d7b9bd65c6da2; submission=5e31b1e3aeff895e6aa4cb7184271dd88989565777451d507d33f539312adb0f; zip=a26df9710a9e55a6f5ab1b7d20c66aa4141afc98863be509dbd5da12eb3ba359; report=504bcc03681bd74f090b2fb8b9a91fb6ecab8af7e721e6f65f720b86691d264f
Population and split: complete frozen population, 1,012 questions; 146,246 structured tables; no gold split available
Scorer/gold: NOT_AVAILABLE; official scorer not executed
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
Improved / regressed / unchanged / unresolved: improved=NOT_MEASURED; regressed=NOT_MEASURED; unchanged=1004 at complete-record JSON-value level; changed-answer=8; technical unresolved=0; semantic unresolved=NOT_MEASURED
Family-level results: 8 supported family rows replayed independently; 8 candidate rows changed; 0 non-target row changes; no semantic accuracy claim
Diagnostic-only metrics: canonical validation 1012/1012 records and queries in both arms; independent replay 8/8; 37 verified source UIDs; 81 verified source cells; provenance failures=0; ZIP testzip=None
Artifact paths and hashes: see Artifact paths and hashes below
Decision: KEEP
Authority status: CANDIDATE_ONLY
```

### Population A/B counts

| Measure | Control | Candidate | Interpretation |
|---|---:|---:|---|
| Input questions | 1,012 | 1,012 | cùng frozen population |
| Structured tables searched | 146,246 | 146,246 | cùng full asset |
| Submission records | 1,012 | 1,012 | không thiếu record |
| Builder replay queries | 1,012 | 1,012 | technical validation only |
| Builder errors | 0 | 0 | technical gate passed |
| Complete-record changes | — | 8 | Q826, Q849, Q877, Q885, Q968, Q980, Q982, Q1004 |
| Answer-field changes | — | 8 | diagnostic changed answers |
| Prediction-tier changes | — | 8 | target ratio route only |
| `pandas_query` changes | — | 8 | target ratio route only |
| Unchanged complete records | — | 1,004 | non-target rows unchanged |
| Improved answers | — | `NOT_MEASURED` | no gold/scorer |
| Regressed answers | — | `NOT_MEASURED` | no gold/scorer |
| Missing IDs | 0 | 0 | population closure passed |

Structural “unchanged” and “changed” counts are not correctness counts.

### Family-level results

Ratios dưới đây được hiển thị làm diagnostic đã replay, làm tròn để đọc; source
artifact giữ Decimal đầy đủ.

| Family | Q | Ticker/scope | Ratios by year (rounded) | Independent winner | Cells | A/B control answer | Candidate answer |
|---|---:|---|---|---:|---:|---:|---:|
| `lease_land_cost_share` | 826 | KBC / consolidated | 2016: 0.914028; 2019: 0.788458; 2020: 0.783626; 2022: 0.503331 | 2016 | 8 | 320000000000.0 | 2016.0 |
| `production_factor_depreciation_share` | 849 | SAB / consolidated | 2018: 0.052192; 2020: 0.054507; 2024: 0.037417; 2025: 0.043767 | 2020 | 20 | 66.56 | 2020.0 |
| `deposit_interest_expense_share` | 877 | HDB / consolidated | 2023: 0.776769; 2024: 0.758266; 2025: 0.786583 | 2025 | 6 | 16786000000.0 | 2025.0 |
| `certificate_deposit_short_maturity_share` | 885 | STB / consolidated | 2021: 0.086347; 2022: 0.016450; 2025: 0.015324 | 2021 | 9 | 4.0 | 2021.0 |
| `usd_long_term_loan_share` | 968 | POW / separate | 2017: 0.826867; 2019: 0.720513; 2022: 0.260450; 2023: 0.424813; 2024: 0.589797 | 2017 | 10 (+5 validation) | 12950216434336.0 | 2017.0 |
| `net_on_balance_currency_asset_share` | 980 | HDB / consolidated | 2018: 0.029261; 2021: -0.015838; 2022: 0.100585; 2024: 0.089130 | 2022 | 8 | 7355000000.0 | 2022.0 |
| `transport_segment_asset_share` | 982 | PVT / separate | 2018: 0.522068; 2019: 0.547901; 2022: 0.601555; 2023: 0.658006; 2025: 0.727093 | 2025 | 10 | 51.0 | 2025.0 |
| `management_depreciation_share` | 1004 | IJC / separate | 2016: 0.019522; 2017: 0.018517; 2018: 0.034721; 2023: 0.052673; 2024: 0.042604 | 2023 | 10 | 737084353.0 | 2023.0 |

`Cells` là số source cells trong exact answer-cell closure; Q968 có thêm năm
validation rows để chứng minh USD + VND = total nhưng các rows đó không được
dùng để chọn source row của overlay.

Independent replay không import candidate adapter. Nó scan full structured asset,
bind current header/period, replay Decimal, kiểm tra scope/unit/contract, source
file hash, line-map locator, unique winner và provenance. PASS này chỉ chứng
minh source/replay consistency của candidate family.

## Diagnostic-only validation

### Canonical full-population builder

Control và candidate đều có:

- `validation.valid=true`;
- `records=1012`;
- `queries_replayed=1012`;
- `errors=[]`;
- source-line map coverage `146246/146246`, `fallback_count=0`.

Candidate ratio metadata:

- `typed_plan_count=1012`;
- `ratio_accepted=8`;
- mỗi `family_seen_*` trong tám family bằng `1`;
- `strict_source_unit_raw_fallback=37` — multiplier được đọc từ declaration
  của chính source file, không suy ra từ magnitude;
- `strict_source_contract=true`;
- `ties_rejected=true`, `ambiguous_pairs_rejected=true`,
  `current_column_must_be_header_bound=true`;
- `promotion_allowed=false`, `research_candidate_only=true`.

### Independent replay

- Protocol: `vifinqa_independent_arg_extreme_ratio_replay_v2`.
- Status: `PASS`.
- Questions in frozen input: `1,012`.
- Supported ratio questions: `8`.
- Replayed supported questions: `8`.
- Failures: `0`.
- Verified source UID count: `37`.
- Verified source-cell count: `81`.
- Provenance errors: `0`.
- Official scorer: `NOT_AVAILABLE`.
- `answer_accuracy`: `NOT_MEASURED`.
- `execution_accuracy`: `NOT_MEASURED`.
- Authority: `CANDIDATE_ONLY`; `promotion_allowed=false`.

The independent checker was also run again using the CLI with a fresh recheck
output. Recheck status remained `PASS`, cùng population/family signature; file
hash của recheck là `9aa5d40effd7e49bde5f9008c57c14869c6c6aa579028529b1b1a251f6547d85`.
Primary replay artifact được materializer dùng có hash
`d08f19b8cc2f0bcf9d3a376615eb4f49568c4d86915232f9b21380db2279faf8`.
Hai file khác nhau ở representation của path đầu vào (absolute/relative),
không khác records/family/ratio/winner signature.

### Final overlay materialization

Overlay cuối được tạo bằng base overlay đã audit trước ratio, không dùng full
candidate build như submission cuối:

- base overlay có `69` inherited replacements;
- source full build cung cấp numeric/evidence rows đã replay;
- independent exact source-cell closure chọn `8` rows, không dùng ID allowlist
  để quyết định predictor;
- final overlay có `77` total replacements;
- numeric values được copy từ source build, `new_numeric_arithmetic_invented=false`;
- `human_verified=false`, `promotion_allowed=false`.

Independent overlay audit xác nhận:

- overlay population `1012`, candidate source population `1012`, base
  population `1012`, cùng ID set;
- overlay diff với base đúng tám Q target;
- tám target overlay rows khớp candidate source rows;
- `1004` non-target overlay rows giữ nguyên JSON value với base;
- tám selected evidence CSV byte-identical với candidate source;
- `1004` non-selected evidence CSV byte-identical với base;
- final ZIP member set chính xác, không duplicate, `testzip=None`.

Final route counts của overlay:

```text
program_arg_extreme_period_v1=31
program_arg_extreme_ratio_period_v1=8
program_named_governance_compensation_v1=3
program_subsidiary_investment_v1=4
source_first_conditional_temporal_v1=5
source_first_multi_entity_conditional_count_v1=1
source_first_multi_entity_direct_aggregation_v1=5
source_first_multi_entity_interest_threshold_v1=1
source_first_multi_entity_lease_threshold_v1=1
source_first_multi_entity_ratio_selector_v1=2
source_first_multi_entity_selector_v1=1
source_first_multi_entity_threshold_v1=1
source_first_period_extreme_v1=14
```

Tổng các route trên là `77`, không phải accuracy score.

## Artifact paths and hashes

### Frozen A/B snapshot

- Snapshot manifest: [`AB_INPUT_MANIFEST_V1.md`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/snapshot/AB_INPUT_MANIFEST_V1.md)
- Frozen structured asset: [`full_table_assets_v1.jsonl`](/home/dungle/Documents/AI_guru/artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl)
- Source line map: [`source_line_map_full_v1.json`](/home/dungle/Documents/AI_guru/artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json)
- Questions: [`questions.jsonl`](/home/dungle/Documents/AI_guru/artifacts/kaggle_upload/dungle2810_vifinqa_answer_optimized_v2_20260830/runtime/questions.jsonl)
- Replay: [`replay.jsonl`](/home/dungle/Documents/AI_guru/artifacts/kaggle_upload/dungle2810_vifinqa_answer_optimized_v2_20260830/runtime/replay.jsonl)
- Typed plans: [`typed_operand_plans_v1.jsonl`](/home/dungle/Documents/AI_guru/artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/typed_operand_plans_v1.jsonl)

Input hashes:

```text
full_table_assets_v1.jsonl = 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_full_v1.json = 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
questions.jsonl = 64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0
replay.jsonl = ccb61012779a64c96a31e07dc875615fdc04973e4918edee35907a3090112372
typed_operand_plans_v1.jsonl = 212097bf0a892d9cf7ab703b9f690b854c9fbb36ce3cc07ebd73c390c22c0319
review_calibrator.joblib = 19fe579a16c480efa0097e39fa624b600a943bf72b48f164378a073de53a155a
```

### Control arm

- Directory: [`control/submission`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot/control/submission)
- Submission: [`submission.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot/control/submission/submission.json)
- ZIP: [`submission.zip`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot/control/submission.zip)
- Build report: [`build_report.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot/control/submission/build_report.json)
- Run manifest: [`run_manifest_v1.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot/control/submission/run_manifest_v1.json)

```text
submission.json = ea47bdc1929eb70523b15a8a1b232074893810fd6ecac103ceb2dd987df42bc6
submission.zip = df32af384745deebbb2795479717b35972bd22681061f2259e8c19160f89b442
build_report.json = 9714f828b4f106f17020de7b55501c2843112d6326ad735670b9dc7229b68d6c
run_manifest_v1.json = 45310e403d7b28449510266da437013802078ff36f39a70e5f3a2358be0ab7ae
```

### Candidate full build and independent replay

- Candidate directory: [`candidate/submission`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/candidate/submission)
- Candidate submission: [`submission.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/candidate/submission/submission.json)
- Candidate ZIP: [`submission.zip`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/candidate/submission.zip)
- Candidate report: [`build_report.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/candidate/submission/build_report.json)
- Candidate manifest: [`run_manifest_v1.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/candidate/submission/run_manifest_v1.json)
- Ratio trace: [`arg_extreme_ratio_period_trace_v1.jsonl`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/candidate/submission/arg_extreme_ratio_period_trace_v1.jsonl)
- Independent primary replay: [`independent_replay_ratio_families_v3.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/independent_replay_ratio_families_v3.json)
- Independent CLI recheck: [`independent_replay_ratio_families_v3_recheck.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/independent_replay_ratio_families_v3_recheck.json)

```text
candidate/submission.json = 5e31b1e3aeff895e6aa4cb7184271dd88989565777451d507d33f539312adb0f
candidate/submission.zip = a26df9710a9e55a6f5ab1b7d20c66aa4141afc98863be509dbd5da12eb3ba359
candidate/build_report.json = 504bcc03681bd74f090b2fb8b9a91fb6ecab8af7e721e6f65f720b86691d264f
candidate/run_manifest_v1.json = be0f2835d8aaba9da407dd078e529830655d737ae0520834b434be0abd63da78
arg_extreme_ratio_period_trace_v1.jsonl = d331886537b9425612b35737ac4c4f420b2ec80de75900ba2c112f563ba34d48
independent_replay_ratio_families_v3.json = d08f19b8cc2f0bcf9d3a376615eb4f49568c4d86915232f9b21380db2279faf8
independent_replay_ratio_families_v3_recheck.json = 9aa5d40effd7e49bde5f9008c57c14869c6c6aa579028529b1b1a251f6547d85
```

### Final materialized overlay

- Overlay directory: [`...ratio_argmax_extended_v3`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_extended_v3)
- Overlay submission: [`submission.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_extended_v3/submission.json)
- Overlay report/manifest: [`build_report.json`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_extended_v3/build_report.json)
- Overlay ZIP: [`...extended_v3.zip`](/home/dungle/Documents/AI_guru/artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_extended_v3.zip)
- Materializer: [`materialize_validated_route_overlay_v1.py`](/home/dungle/Documents/AI_guru/scripts/research/materialize_validated_route_overlay_v1.py)

```text
materializer.py = 7931bdd49b2c675f0599ee5e36e66b6d19efd9bd09bbb45581cbab1203924e28
overlay/submission.json = 5b68ce3a18cfa26237388abe5a4309116321b56a930d5d5519db0a57180673dc
overlay/build_report.json = f8db8d227737231f281e147236e518aab240901af6390f5e0d775119087fc6ef
overlay/route_overlay_manifest.json = f8db8d227737231f281e147236e518aab240901af6390f5e0d775119087fc6ef
overlay.zip = e7ce9c4f810a3633a54836f24e6e17a0f3fefdeb4a169a5244a6a8ce50d67416
overlay.zip size = 553462 bytes
overlay.zip members = 1013
overlay.zip CSV members = 1012
overlay.zip testzip = None
```

## Verification commands

Các command sau đã chạy bằng `.venv` và đều dùng RTK wrapper theo
`AGENTS.md`/`RTK.md`:

```bash
/home/dungle/.local/bin/rtk proxy .venv/bin/pytest -q tests/research/test_arg_extreme_ratio_variant_v1.py
# 7 passed in 0.12s

/home/dungle/.local/bin/rtk proxy .venv/bin/python -m py_compile \
  scripts/research/run_arg_extreme_ratio_variant_v1.py \
  scripts/research/replay_arg_extreme_ratio_families_v1.py \
  scripts/research/materialize_validated_route_overlay_v1.py
# exit status 0

/home/dungle/.local/bin/rtk proxy .venv/bin/python scripts/research/replay_arg_extreme_ratio_families_v1.py \
  --asset artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl \
  --source-line-map artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json \
  --questions artifacts/kaggle_upload/dungle2810_vifinqa_answer_optimized_v2_20260830/runtime/questions.jsonl \
  --typed-plans artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/typed_operand_plans_v1.jsonl \
  --output artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/independent_replay_ratio_families_v3_recheck.json
# exit status 0; status=PASS; supported=8; replayed=8; failures=0
```

Materializer invocation used exact source-cell closure:

```bash
/home/dungle/.local/bin/rtk proxy .venv/bin/python scripts/research/materialize_validated_route_overlay_v1.py \
  --base-dir artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_v1 \
  --source-dir artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/candidate/submission \
  --output-dir artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_ratio_argmax_extended_v3 \
  --builder artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/snapshot/scripts/e2e/build_competition_submission_v1.py \
  --only-selected-routes \
  --independent-replay artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v3_same_snapshot_fix1/independent_replay_ratio_families_v3.json \
  --independent-replay-route program_arg_extreme_ratio_period_v1
# validation valid=true; records=1012; queries_replayed=1012; errors=[]
```

## Limits and next gate

Một initial control ở `argmax_ratio_period_ab_v3/control` đã bị loại khỏi A/B
chính vì nó vô tình dùng mutable current-worktree builder thay vì frozen
snapshot. Nó được giữ như diagnostic lịch sử; control fingerprint trong báo
cáo này chỉ là `argmax_ratio_period_ab_v3_same_snapshot/control`.

Candidate full build cũng không phải overlay cuối: full source build thay đổi
nhiều rows do nó hydrate toàn corpus. Overlay cuối chỉ copy tám rows có exact
source-cell closure từ independent replay vào base overlay 69 rows, nhờ đó
không đưa các thay đổi navigation ngoài target vào submission candidate.

Q828 và các ratio-like câu khác không có đầy đủ numerator/denominator source
contract chưa được materialize. Đó là unresolved discovery evidence, không phải
failure để sửa bằng Question-ID patch. Queue kế tiếp có giá trị nhất là cung
cấp independent gold/official scorer rồi đo tám thay đổi này; nếu không có
scorer thì tiếp tục census một failure class mới chỉ được ghi là
`DISCOVERY`/`CANDIDATE_ONLY`.

Hiện không còn process `argmax_ratio`, independent replay hoặc materializer
đang chạy. Một process riêng của agent khác
`formula_bridge_contract_ab_v1/control` vẫn đang chạy trong workspace và không
bị đụng tới.

## Decision and authority

Decision: `KEEP` candidate artifacts cho independent scored evaluation kế tiếp.
Lý do: hypothesis đã được tổng quát hóa theo family, full-population scope
được freeze và replay, A/B có zero non-target structural regression, independent
source replay PASS, materialization và packaging đều sạch.

Authority status: `CANDIDATE_ONLY`. Không được suy ra answer accuracy,
execution accuracy, official leaderboard score, `VERIFIED`, `PROMOTION_READY`
hay `RELEASE_AUTHORIZED` từ kết quả này.
