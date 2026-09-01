# Guarded source-first multi-year argmax: controlled A/B V1

Ngày: 2026-08-30  
Phạm vi: ViFinQA 1.012 câu, full structured corpus 146.246 tables.

## Kết luận ngắn

Family này là nhóm câu hỏi trả về **năm** có giá trị max/min trong một danh
sách năm được nêu rõ. A/B được chạy trên cùng builder snapshot và cùng input.

Variant strict xét 43 typed `arg_extreme_period` plans và materialize 23 câu.
So với control, 23 câu đổi đáp án sang năm thắng; 20 câu còn lại của family
bị giữ lại do thiếu row family, unsupported composition/ratio hoặc tie. Đây là
coverage/proposal effect của candidate lane, không phải claim `+23 accuracy`
hay leaderboard score.

Workspace chưa có gold answer/official scorer cho split này. Variant vẫn mang
`research_candidate_only=true`, `promotion_allowed=false`; không tự nâng
`PARTIAL` thành `VERIFIED`, không cấp `human_verified` và không tự phát hành
strict release.

## Contract strict đã kiểm tra

Route chỉ được nhận khi tất cả điều kiện sau cùng đúng:

1. Typed plan có `operation_ast.op=arg_extreme_period`, một ticker và ít nhất
   hai năm explicit; năm trả về là một năm trong đúng danh sách đó.
2. Ratio/percentage/CAGR và composition nhiều row bị loại khỏi route này.
3. Mỗi năm hydrate từ đúng `report_year`; không dùng neighboring-year fallback.
4. Mỗi năm có một row numeric cùng row family. Các anchor nghĩa lấy từ câu hỏi
   vẫn phải xuất hiện trong label sau normalize; do đó `doanh thu thuần`,
   `bên liên quan`, `dự phòng cụ thể`, `tài sản cố định hữu/vô hình` không bị
   rút gọn thành một row gần nghĩa hơn.
5. Source table phải có loại được hỗ trợ và cùng loại qua mọi năm. `financial
   note_detail` chỉ được collapse vào `financial note`; schedule/governance/
   segment không được so sánh trong strict lane.
6. Source-declared multiplier phải tồn tại và giống nhau qua mọi năm. Raw cell
   được replay bằng `Decimal * multiplier` trước khi so sánh.
7. Winner phải duy nhất. Tie, thiếu source, sai coordinate hoặc semantic row
   drift đều fail closed.

Các gate này chỉ kiểm tra domain evidence và semantic identity; ranking,
retrieval confidence và số học replay không tự trở thành authority.

## Snapshot và input

Hai arm dùng cùng snapshot builder:

- Builder snapshot:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/snapshot/scripts/e2e/build_competition_submission_v1.py`
- Builder SHA-256:
  `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e`
- `source_first_lookup.py` SHA-256:
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`
- Full table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- Source-line map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`
- Frozen typed-plan input:
  `artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/typed_operand_plans_v1.jsonl`

Build reports ghi cùng builder/source hash tại lúc load và lúc report, với
`changed_during_build=false`. Các route experimental khác được tắt đối xứng;
control tắt period-extreme, variant bật runner với
`--strict-source-contract`.

Runner strict hiện tại:

`scripts/research/run_arg_extreme_period_variant_v1.py`

Runner SHA-256 hiện tại:

`90c4d4fd574934722f2e0cb54be2b501da2d396f0b9e743f9c6d97f12c078426`

Để đóng băng dependency source-first khi chạy lại, runner hỗ trợ biến môi
trường `VIFINQA_ARGMAX_SOURCE_FIRST_LOOKUP_PATH`; build integrated được chạy
với source snapshot ở trên và report xác nhận hash `d68ed4...`. Hook này chỉ
kiểm soát import lineage, không thay đổi answer logic.

## Terminal gates

Control:

- Report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v1/control/submission/build_report.json`
- ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v1/control/submission.zip`

Variant strict v4:

- Report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v4/variant/submission/build_report.json`
- Submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v4/variant/submission/submission.json`
- Trace:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v4/variant/submission/arg_extreme_period_trace_v1.jsonl`
- Evidence directory:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v4/variant/submission/data/`
- ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v4/variant/submission.zip`

| Gate | Control | Strict variant |
|---|---:|---:|
| `validation.valid` | `true` | `true` |
| records | 1.012 | 1.012 |
| query replay | 1.012 | 1.012 |
| validation errors | `[]` | `[]` |
| `changed_during_build` | `false` | `false` |
| ZIP integrity (`unzip -t`) | PASS | PASS |
| ZIP size | 532,964 bytes | 537,600 bytes |

Variant telemetry từ `build_report.json`:

| Metric | Value |
|---|---:|
| typed argmax plans seen | 43 |
| strict accepted | 23 |
| no coherent row family | 15 |
| ratio skipped | 3 |
| multi-row composition skipped | 1 |
| extreme tie rejected | 1 |
| source cells in accepted cohorts | 93 |
| `program_arg_extreme_period_v1` outputs | 23 |
| `promotion_allowed` | `false` |

Intermediate v3 chỉ tạo evidence CSV đến q714 và không có terminal report/ZIP;
intermediate này bị loại khỏi bằng chứng, không được dùng để suy ra score.

## Integrated candidate với lineage đóng băng

Sau ablation control/variant, strict route được chạy lại cùng các lane của
`integrated_best_v3_review_bundle`, nhưng preload source-first snapshot để hai
implementation fingerprints khớp nhau:

- Report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_best_argmax_v1_frozen_v2/variant/submission/build_report.json`
- Submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_best_argmax_v1_frozen_v2/variant/submission/submission.json`
- Trace:
  `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_best_argmax_v1_frozen_v2/variant/submission/arg_extreme_period_trace_v1.jsonl`
- ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_best_argmax_v1_frozen_v2/variant/submission.zip`

Candidate này đạt `validation.valid=true`, `records=1012`,
`queries_replayed=1012`, `errors=[]`, ZIP `545,803` bytes và `unzip -t`
PASS. So với integrated baseline cùng builder/source snapshot, chỉ có đúng
23 ID thay đổi; cả 23 đều có tier `program_arg_extreme_period_v1`, không có
delta ngoài family argmax. Candidate vẫn có `predicted_questions=953`,
`fallback_questions=59`, verification `PARTIAL=951` và `UNRESOLVED=61`.

Một integrated trial trước đó dùng source lookup worktree hash
`238050…` thay vì `d68ed4…`; trial đó vẫn hợp lệ về ZIP nhưng bị loại khỏi
so sánh attribution và không được dùng làm bằng chứng score.

## Answer diff control -> strict variant

23 câu thay đổi đáp án:

`Q813, Q832, Q841, Q850, Q860, Q874, Q876, Q890, Q897, Q904, Q906, Q910,
Q929, Q933, Q936, Q946, Q948, Q953, Q974, Q981, Q997, Q999, Q1000`.

Các năm winner được materialize:

| Question | Winner year |
|---:|---:|
| Q813 | 2019 |
| Q832 | 2025 |
| Q841 | 2017 |
| Q850 | 2020 |
| Q860 | 2023 |
| Q874 | 2023 |
| Q876 | 2019 |
| Q890 | 2017 |
| Q897 | 2016 |
| Q904 | 2020 |
| Q906 | 2017 |
| Q910 | 2022 |
| Q929 | 2022 |
| Q933 | 2015 |
| Q936 | 2025 |
| Q946 | 2018 |
| Q948 | 2024 |
| Q953 | 2021 |
| Q974 | 2024 |
| Q981 | 2022 |
| Q997 | 2019 |
| Q999 | 2025 |
| Q1000 | 2021 |

Control dùng numeric semantic fallback ở các vị trí này; variant dùng
`program_arg_extreme_period_v1`. Số câu đổi chỉ là delta prediction trong
candidate artifact; không được gọi là số câu đúng khi chưa có gold.

## Independent source audit

Audit được chạy độc lập bằng cách reload full table asset và source-line map,
không dùng trace của selector làm bằng chứng duy nhất. Với 23 accepted cohorts
trong integrated candidate:

- 23/23 UID cohort tồn tại; 93/93 source-cell references có UID trong full
  asset (90 UID duy nhất do có table UID được tái sử dụng giữa các references).
- 93/93 source cells có entry trong source-line map.
- 93/93 row/column coordinates nằm trong bounds và raw cell numeric.
- 93/93 `raw cell * source multiplier` khớp `operand_value` trong trace.
- 23/23 cohort cùng table kind sau collapse note/detail.
- 23/23 cohort cùng source multiplier.
- 23/23 cohort đúng ticker/report-year và semantic question anchors.

Kết quả terminal của audit:

`accepted=23, source_cells=93, failure_count=0`

Evidence CSV của integrated candidate được lưu trong:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_best_argmax_v1_frozen_v2/variant/submission/data/`

Các drift bị chặn trong quá trình thử nghiệm gồm `financial_data_schedule`,
`segment_reporting`, cohort trộn loại bảng, `Doanh thu` thay cho `Doanh thu
thuần`, `Dự phòng rủi ro` thay cho `Dự phòng cụ thể`, và doanh thu chung thay
cho doanh thu bên liên quan. Những trường hợp này không được đổi thành answer
chỉ vì chúng có số numeric.

## Tests và quyết định

Focused tests sau khi thêm strict contract và source preload hook:

```text
9 passed in 0.14s
```

`py_compile` của runner cũng PASS. Full suite hiện cho `562 passed, 1 skipped,
1 failed`; failure nằm ở test resolver hiện hữu trong worktree
`tests/pipeline/test_canonical_resolver_observer.py` (fixture/helper đang sửa
`raw_value` thành `10` nhưng assertion kỳ vọng `1000000`), không phải argmax
test. Vì vậy repository chưa được gọi là green toàn cục.

Integrated candidate là lane có provenance tốt hơn v1/v2 loose và đã sẵn sàng
để gửi qua external scorer nếu người dùng muốn thử leaderboard, nhưng chưa
được merge vào strict release. Official-score claim vẫn bị chặn cho đến khi có
scorer/gold bên ngoài; numeric replay, route tier và local ZIP chỉ chứng minh
tính hợp lệ/provenance của candidate, không chứng minh accuracy.
