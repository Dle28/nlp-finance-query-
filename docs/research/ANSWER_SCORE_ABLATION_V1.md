# Answer-score ablation: source-first direct lookup V1

> **Cập nhật trạng thái 2026-08-30:** các số liệu `source_first_schema_ablation_v3`
> được ghi ở những phần lịch sử bên dưới đã bị **invalidate**. Baseline và
> variant đã chạy trong lúc `source_first_lookup.py` còn bị thay đổi giữa các
> tiến trình, nên không được dùng để kết luận score hoặc promotion. Kết quả
> controlled mới nhất nằm ở mục 10; mục đó dùng cùng full-corpus asset và
> snapshot mã ổn định, đồng thời kiểm tra answer diff chứ không chỉ đếm route.

Ngày: 2026-08-30  
Phạm vi: ViFinQA 1,012 câu, pipeline `build_competition_submission_v1.py`

## Mục tiêu

Score chính đang bị nghẽn ở answer/execution hơn là ở retrieval thuần túy. Thí
nghiệm này nhắm vào nhóm câu có `question_plan.family=direct_lookup`, nhưng chỉ
đưa proposal vào output sau khi cell hiện tại được replay bằng Decimal và đi qua
verifier hiện có.

Hai thay đổi nhỏ được đo và tách telemetry:

1. Khôi phục `ticker` từ tên tài liệu canonical
   ``<TICKER>_financial_statements_<YEAR>_*`` khi V2 asset không có field
   top-level `ticker`. Không parse ticker từ văn bản tự do.
2. Fallback report-year có kiểm soát: tìm toàn bộ report năm Y trước; chỉ khi
   Y không có candidate hợp lệ mới tìm report năm Y+1 và vẫn chọn cột số liệu
   của năm Y. Hai năm không bao giờ được trộn vào cùng một pool.

Fallback này là candidate/replay lane, không phải semantic approval và không
được gắn nhãn `VERIFIED`.

## Kết quả resolver trên cùng review population

| Asset | Exact-year direct candidates | Có fallback Y+1 | Candidate mới | Câu exact bị đổi |
|---|---:|---:|---:|---:|
| V2 `tables_structured_v2.jsonl` | 99 | 111 | +12 | 0 |
| Full corpus `full_table_assets_v1.jsonl` | 85 | 94 | +9 | 0 |

V2 có 29,509 tables; 29,423 tables được khôi phục ticker từ `document_id` và
chỉ 5 tables còn thiếu identity. Full corpus có 146,246 tables, đã có ticker
và report year đầy đủ.

## Kết quả integrated builder

Hai build sử dụng cùng questions, review bundle, full corpus asset, replay
artifact và các research inputs. Chỉ khác flag fallback:

- Baseline: `--disable-source-first-report-year-neighbor`
- Variant: mặc định `--source-first-report-year-neighbor-offset 1`

| Metric local | Baseline | Variant | Delta |
|---|---:|---:|---:|
| Source-first candidates | 85 | 94 | +9 |
| Source-first predictions được chọn | 58 | 65 | +7 |
| Semantic-cell predictions | 709 | 702 | -7 |
| Non-zero predictions | 999 | 999 | 0 |
| Records | 1,012 | 1,012 | 0 |
| Query replay | 1,012 | 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| `VERIFIED` certificates | 0 | 0 | 0 |

Chín câu chuyển route trong integrated output là:

`Q25, Q81, Q117, Q205, Q216, Q227, Q288`.

Tất cả hiện vẫn là `PARTIAL/BEST_EFFORT`; điều này đúng policy vì local
replay không tự cấp semantic authority. Hai candidate resolver bổ sung khác
không thắng precedence của route hiện có nên không làm thay đổi prediction.

## Artifact và cách tái lập

- Baseline report: `artifacts/runs/vifinqa_answer_optimization_20260830/source_first_schema_ablation_v3/baseline/submission/build_report.json`
- Variant report: `artifacts/runs/vifinqa_answer_optimization_20260830/source_first_schema_ablation_v3/neighbor/submission/build_report.json`
- Baseline ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/source_first_schema_ablation_v3/baseline/submission.zip`
- Variant ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/source_first_schema_ablation_v3/neighbor/submission.zip`
- Diagnostic diff: so sánh `diagnostics.jsonl` trong hai thư mục `submission/`

Các artifact `source_first_schema_ablation`, `source_first_schema_ablation_v2`,
`source_first_schema_ablation_v3` và các vòng tương tự là các vòng thăm dò lịch
sử. Do snapshot mã bị race giữa các tiến trình, không dùng bảng delta của v3/v4
cho submission hoặc claim score. Chỉ dùng chúng để truy nguyên vì sao cần
controlled same-snapshot A/B ở mục 10.

Lệnh baseline:

```bash
.venv/bin/python scripts/e2e/build_competition_submission_v1.py \
  --bundle artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle \
  --structured-tables artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl \
  --replay artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/grounded_execution_replay_v2.jsonl \
  --output artifacts/runs/vifinqa_answer_optimization_20260830/source_first_schema_ablation_v3/baseline/submission \
  --disable-source-first-report-year-neighbor
```

Variant bỏ flag cuối cùng để dùng offset mặc định là `1`.

## Diễn giải score

Hiện chưa có gold answer hoặc official local scorer trong workspace. Vì vậy
`+9` trong bảng lịch sử ở trên không còn là một delta được chấp nhận do race
snapshot; không thể dùng nó để tuyên bố `Answer Accuracy +9` hay
`Execution Accuracy +9`. ZIP nào qua integrity/record/query replay gate cũng
chỉ là best-effort candidate; leaderboard score chỉ được xác nhận sau khi
upload cùng split.

## Ưu tiên tiếp theo

Nguồn gain tiếp theo nhiều khả năng nằm ở 245 direct lookup còn unresolved và
nhóm composed execution/missing operands, không phải tăng dense top-k một cách
toàn cục. Bước tiếp theo nên là audit theo failure family (missing period,
metric-row mismatch, scope conflict, missing operand) trên cùng holdout; chỉ
promote một family khi có replay evidence và không làm tăng conflict/false
binding.

## 10. Controlled temporal source-first V2

Vòng này kiểm tra bottleneck Answer/Execution: hai operand của cùng một câu
temporal được resolve độc lập từ full-corpus structured tables, sau đó mới thực
hiện phép trừ hoặc phần trăm thay đổi. Baseline dùng
`--disable-source-first-temporal`; variant bật lane mặc định. Hai run dùng cùng
questions, review bundle, replay và full-corpus asset có SHA-256
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`.
Snapshot mã được kiểm tra ổn định trong controlled series:
`build_competition_submission_v1.py` =
`63a7456ba70372eb2ed03574a25937e3082d3ff7743262394c38eec30210b6bf`,
`source_first_lookup.py` =
`949268f06f271c67538a6ecbcd9f6cf0df6f7116a7590676d77511fabb6062c5`.

| Metric local | Baseline | Variant | Delta |
|---|---:|---:|---:|
| Temporal questions considered | 0 | 39 | +39 |
| Temporal source-first answers | 0 | 2 | +2 |
| Actual answer changes | 0 | 2 | +2 |
| Non-zero predictions | 999 | 999 | 0 |
| Records / query replay | 1,012 / 1,012 | 1,012 / 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |

Hai answer thay đổi là:

| Question | Baseline | Temporal source-first | Nguồn đã replay |
|---:|---:|---:|---|
| Q578 | `-29.059234208` | `333.269120577` tỷ đồng | BAF 2022: `25.699870125`; BAF 2025: `358.968990702` tỷ đồng |
| Q579 | `-81.57491945944844` | `167.23992696016566`% | IJC 2016: `2,262,694,070`; IJC 2021: `6,046,821,980` VND |

Variant chỉ cho phép một ticker, hai năm explicit, exact report-year lookup,
match mode exact/ordered OCR-gap, row phải chứa đủ token metric thiết yếu, và
hai năm phải bind cùng row signature. Đây vẫn là proposal được replay bằng
Decimal; không có `human_verified` hay strict `VERIFIED` certificate.

Các ca temporal còn lại không được mở khóa trong V2 vì có ít nhất một rủi ro
được chứng minh trong source contract: nhãn bị rút gọn (`Lãi vay phải trả`),
reclassification giữa hai năm (`Doanh thu bộ phận`/`Doanh thu thuần bộ phận`),
row tổng quát (`GIÁ TRỊ THUẦN`), hoặc phạm vi/duplicate chưa đủ rõ. Đây là chủ ý
fail-closed, không phải score claim.

Artifact chính:

- Baseline: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v2/baseline/submission/build_report.json`
- Variant: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v2/variant/submission/build_report.json`
- Variant ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_source_first_v2/variant/submission.zip`
- Stable candidate build: `artifacts/runs/vifinqa_answer_optimization_20260830/stable_control_no_dense_calibrated_r1/submission/build_report.json`

Kết luận vòng này là **giữ temporal V2 bật trong candidate lane** vì đã tạo hai
thay đổi số có source replay độc lập, nhưng chưa gọi đó là `Answer Accuracy
+2` hoặc score leaderboard. Vòng tiếp theo nên xử lý từng pattern bị chặn
(ưu tiên payable-note context và direct unresolved metric-row families), với
A/B cùng snapshot và answer diff bắt buộc.

## 11. Controlled temporal cross-family V3: route priority và semantic quarantine

V3 xử lý hai vấn đề tích hợp phát hiện sau V2. Thứ nhất, 39 câu có hình dạng
một ticker và hai năm nhưng planner gắn `family=cross_entity_comparison` đã
được source-first temporal resolver nhận diện; tier mới
`source_first_temporal_cross_entity_v1` phải có route priority ngang temporal
V2 (`92.0`), nếu không proposal sẽ rơi về priority mặc định `10.0` và bị
`semantic_cell_heuristic` (`50.0`) lấn át. Thứ hai, câu hỏi không ghi
`hiện hành` không được bind vào dòng `Chi phí thuế TNDN hiện hành`; trường hợp
này bị quarantine fail-closed thay vì chọn một dòng có qualifier khác.

Population và điều kiện mở khóa:

- 39 câu được xét là cross-family có đúng một ticker, đúng hai năm explicit;
  8 câu bind được hai source cell độc lập sau khi kiểm tra exact report year,
  cùng scope, cùng row signature và match mode exact/ordered OCR-gap.
- Một câu ứng viên là Q589 bị loại khỏi route vì `Chi phí thuế TNDN` và
  `Chi phí thuế TNDN hiện hành` là hai metric có thể khác nghĩa trong corpus.
- Hai temporal V2 cũ (Q578, Q579) cũng được giữ trong variant; vì vậy tổng
  temporal answer được chọn là 10, gồm 2 câu cũ + 8 câu cross-family mới.

Controlled A/B dùng cùng full-corpus asset SHA-256
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`, cùng
source-line map, review bundle, replay và candidate inputs. Snapshot code có:

- `build_competition_submission_v1.py`:
  `5b5fc4d77348ce0568523b1fd51d85031d9217faa24196a7cdcbad410b05c866`
- `source_first_lookup.py`:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- `changed_during_build=false` ở cả control và variant.

| Metric local | Control | Variant | Delta |
|---|---:|---:|---:|
| Temporal questions considered | 0 | 78 | +78 |
| Cross-family one-ticker/two-year questions recovered | 0 | 39 | +39 |
| Temporal answers resolved | 0 | 10 | +10 |
| Actual answer changes | 0 | 10 | +10 |
| Trong đó cross-family answer changes | 0 | 8 | +8 |
| `semantic_cell_heuristic` selected | 671 | 663 | -8 |
| Records / query replay | 1,012 / 1,012 | 1,012 / 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| `VERIFIED` certificates | 0 | 0 | 0 |

Các thay đổi mới do cross-family route là:

| Question | Control | Variant | Source replay |
|---:|---:|---:|---|
| Q585 | `22743000000` | `6717.6581805390669656597634436969617025`% | STB 2016 và 2022, `Quỹ khen thưởng phúc lợi` |
| Q604 | `264` | `123.977414336` tỷ đồng | DLG 2018 và 2019, `TỔNG CỘNG TÀI SẢN` |
| Q613 | `-301534.111657` | `186259.450830` triệu đồng | IJC 2018 và 2022, lưu chuyển tiền thuần từ HĐ tài chính |
| Q621 | `3.059363382827` | `20.799329684971` nghìn tỷ đồng | VJC 2021 và 2024, `Chi phí nhiên liệu` |
| Q629 | `-53401178126` | `-46.516152557489884210463564996832077113`% | HBC 2016 và 2020, dự phòng phải thu khó đòi |
| Q630 | `-2207215` | `-805232` triệu đồng | SHB 2024 và 2025, `Chi phí thuế TNDN hiện hành` |
| Q643 | `319895796` | `41586082` cổ phiếu | KLB 2019 và 2023, `Số lượng cổ phiếu đang lưu hành` |
| Q654 | `510.983001009` | `74.775704270` tỷ đồng | DNH 2016 và 2017, `Giá vốn bán điện` |

Replay độc lập lần cuối đạt `8/8` câu và `16/16` table UID tồn tại. Checker
độc lập đã kiểm tra document/ticker, scope, report year, row/column bounds,
raw cell value, source-unit multiplier, output-unit divisor và phép trừ hoặc
phần trăm thay đổi; mọi câu đều pass. Hai ZIP cũng pass `unzip -t`.

Artifacts:

- Control report: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/control/submission/build_report.json`
- Variant report: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission/build_report.json`
- Control submission: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/control/submission/submission.json`
- Variant submission: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission/submission.json`
- Variant audit ledger: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission/prediction_audit_ledger_v1.jsonl`
- Control ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/control/submission.zip`
- Variant ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission.zip`

V3 đủ điều kiện giữ route trong **authorized best-effort submission
candidate** lane: local answer diff là `+8` cho family mới và tất cả proposal
đều còn `PARTIAL`, không có strict certificate. Không được diễn giải `+8` này
thành `Answer Accuracy +8` hay leaderboard-score delta khi workspace chưa có
gold answer/official scorer. Bước nghiên cứu tiếp theo là audit các composed
families còn `DECOMPOSITION_REQUIRED`/`OPERAND_MISSING`, giữ cùng quy tắc:
family-level held-out probe, source replay độc lập, precedence A/B và
fail-closed semantic guards.

## 12. Controlled contextual gross-receivables total: Q323

Vòng này mở một nhánh rất hẹp cho câu hỏi `Tổng cộng các khoản phải thu` khi
planner để lại `multi_entity_or_period_aggregation` mà không có operands. Mục
tiêu là sửa một lỗi chọn row đã quan sát được: fallback chọn dòng thành phần
`Phải thu các dịch vụ công ty chứng khoán cungcấp khó đòi` (`12.971609076`),
trong khi cùng bảng có dòng `Tổng cộng` (`16.024974123`).

Contract chỉ nhận candidate khi đồng thời có:

- câu hỏi chứa `tổng cộng` và `các khoản phải thu`, có một mã/năm và scope
  `separate` từ question plan;
- table là `financial_note`/`financial_note_detail`, context có cả
  `Chi tiết dự phòng suy giảm giá trị các khoản phải thu` và cue thời điểm
  `cuối năm`/`tại ngày`;
- row sau khi normalize là `Tổng` hoặc `Tổng cộng`;
- đúng một cột số có header chứa độc lập `giá trị`, `phải thu`, `cuối năm`,
  đồng thời loại các cột `dự phòng`.

Khi contract đã tạo được candidate, resolver loại các row matcher lỏng như
`Lãi từ các khoản cho vay và phải thu` trước khi chạy duplicate/value gate.
Scope, period, UID, row/column và Decimal replay vẫn là các gate độc lập; route
không dùng retrieval rank và `promotion_allowed=false`.

### Controlled A/B

Hai arm dùng cùng snapshot và cùng questions, review bundle, replay,
full-corpus table asset, source-line map, research candidates, route overlay,
model candidate và direct-evidence replay. Khác biệt có chủ ý duy nhất là
control bật `--disable-source-first-financial-receivables-total`.

Snapshot:

- builder:
  `f29df1dbfebef0a0884cb65406ef8b3d44ec4faeefe93c84e895e3f2734025cd`;
- `source_first_lookup.py`:
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`;
- snapshot directory: `/tmp/vifinqa-receivables-ab-v1.Lw4tNk`;
- full table asset: 146,246 tables, SHA-256
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source-line map: 146,246 entries, SHA-256
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`;
- cả hai report đều ghi `changed_during_build=false` và
  `validation.valid=true`.

| Metric local | Control | Variant | Delta |
|---|---:|---:|---:|
| Reclassified questions considered | 8 | 9 | +1 |
| Reclassified questions resolved | 8 | 9 | +1 |
| `semantic_cell_heuristic` selected | 671 | 670 | -1 |
| `source_first_reclassified_direct_v1` selected | 8 | 9 | +1 |
| Non-zero predictions | 999 | 999 | 0 |
| Records / query replay | 1,012 / 1,012 | 1,012 / 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |

Answer diff trên toàn bộ ID là đúng một câu:

| Question | Control | Variant | Source-first replay |
|---:|---:|---:|---|
| Q323 | `12.971609076` / semantic | `16.024974123` / reclassified | SSI riêng 2016, row `Tổng cộng`, cột `Giá trị phải thu khó đòi cuối năm` |

### Independent source replay

Checker độc lập đọc lại UID
`508c7153dcb21cce007515aaae947c281c1b992a5927caa1609d045c77ee76f1` từ
`full_table_assets_v1.jsonl` và xác nhận:

```text
document_id: SSI_financial_statements_2016_separate
scope: separate
report_year: 2016
table_kind: financial_note
row_index: 17
column_index: 1
raw cell: 16.024.974.123
source multiplier: 1
requested divisor: 1,000,000,000
replayed answer: 16.024974123
source-line map: line 1372
source file SHA-256: matches table.source_sha256
```

Dòng source tại line 1372 chứa trực tiếp table header
`Giá trịphải thu khó đòicuối nămVND` và row `Tổng cộng` với raw cell nêu trên.
Variant CSV cũng replay đúng UID/row/column/raw value. Cả hai ZIP đều qua
`unzip -t`:

- control ZIP SHA-256:
  `d5c7c669d1b8f2d4245e1fd4fa151ee939a3725057c1706e7a0e90a679d55def`;
- variant ZIP SHA-256:
  `8f2a196476bdededefd9c7cc65ff7ffe80bda8a5f3243b74a015a2231b6ffc9a`.

Artifacts:

- control report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/receivables_total_ab_v1/control/submission/build_report.json`;
- variant report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/receivables_total_ab_v1/variant/submission/build_report.json`;
- variant audit ledger:
  `artifacts/runs/vifinqa_answer_optimization_20260830/receivables_total_ab_v1/variant/submission/prediction_audit_ledger_v1.jsonl`;
- variant Q323 evidence:
  `artifacts/runs/vifinqa_answer_optimization_20260830/receivables_total_ab_v1/variant/submission/data/q0323_evidence.csv`;
- snapshot fingerprints:
  `artifacts/runs/vifinqa_answer_optimization_20260830/receivables_total_ab_v1/snapshot_sha256.txt`.

Đây là local best-effort improvement có source replay, chưa phải official
`Answer Accuracy +1`: workspace chưa có gold answer/official scorer, proposal
vẫn `PARTIAL`, `confidence_class=BEST_EFFORT` và không được nâng thành strict
`VERIFIED`. Bước tích hợp kế tiếp là đưa Q323 cùng các lane Q175/Q261 đã có A/B
riêng vào một build combined, rồi chạy lại full validation, ZIP gate và diff
against the last integrated candidate.

## 13. Controlled multi-entity direct aggregation: Q827, Q858, Q927

Một lane hẹp khác đã được kiểm tra cho câu hỏi có cùng metric trên nhiều
issuer và phép `mean`/`sum`. Contract yêu cầu issuer/year/metric rõ, đúng một
source cell cho mỗi issuer, cùng scope/report year, và replay độc lập từng
cell trước khi tính Decimal aggregation. Các candidate vẫn là
`authorized_best_effort_submission_candidate`, `promotion_allowed=false`;
không có strict `VERIFIED` certificate.

Controlled A/B dùng cùng snapshot và inputs; control chỉ thêm
`--disable-source-first-multi-entity-direct-aggregation`. Builder SHA là
`f29df1dbfebef0a0884cb65406ef8b3d44ec4faeefe93c84e895e3f2734025cd`, core SHA
là `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`,
`changed_during_build=false`, full asset có 146,246 tables và source map
coverage chính xác `146246/146246`. Variant giải quyết 3/3 câu, đổi đúng ba
answer IDs, trong khi cả control và variant đều replay đủ 1,012 records,
`validation.errors=[]`, và ZIP đều qua `unzip -t`.

| Question | Control | Variant | Replay |
|---:|---:|---:|---|
| Q827 | `6.387361021955` | `602.37564375975` | mean 4 cells: SNZ, VIC, DXS, HPX |
| Q858 | `0.015537986326` | `0.530161279161` | sum 3 cells: SAB, DBC, MCH |
| Q927 | `2.542217408` | `37.2454051892` | mean 5 cells: VPI, DIG, VRE, DXG, PDR |

Checker độc lập pass `12/12` source cells và `3/3` aggregation results sau
khi kiểm tra UID, document/ticker, scope, report year, row/column, raw value,
source multiplier, output divisor và công thức mean/sum. Chi tiết cùng
artifact path nằm trong
`docs/research/MULTI_ENTITY_DIRECT_AGGREGATION_SOURCE_FIRST_ABLATION_V1.md`.

Đây là local candidate delta `+3`, không được gọi là official Answer Accuracy
hay leaderboard-score delta khi chưa có gold/official scorer. Bước tiếp theo
là build integrated best candidate cùng Q175, Q261, Q323 và các lane source
first đã pass replay, rồi kiểm tra answer diff trên toàn bộ 1,012 IDs.
