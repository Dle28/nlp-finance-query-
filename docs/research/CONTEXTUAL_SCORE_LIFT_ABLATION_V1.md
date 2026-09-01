# Contextual score-lift A/B v4

Ngày chạy: 2026-08-31 (Asia/Bangkok)

## Hypothesis/family

Candidate v4 kiểm tra một quy tắc dùng được cho cả population: source-first
context được ràng buộc bởi operation/plan, scope entity, table kind, context,
row label, period, unit và tọa độ source; không dùng Question-ID để route,
parse, chọn công thức hay chọn đáp án. Ba family được kiểm tra là:

1. tổng `phải trả ngắn hạn khác` của các bên liên quan;
2. tổng `chi phí tài chính` nhiều entity, gồm trường hợp OCR compound cell có
   nhiều literal số;
3. chọn năm theo điều kiện max/min rồi lookup một metric khác trong năm đó.

Điểm sửa của v4 so với candidate v3 là giữ lại metadata
`raw_value_selector`/`raw_value_literal_index` khi serialize proposal evidence.
Điều này cho phép verifier replay đúng literal tài chính đầu tiên của compound
cell thay vì loại proposal vì toàn ô không parse được như một Decimal. Đây là
thay đổi ở contract evidence dùng chung; bốn QID xuất hiện bên dưới chỉ là
metadata để audit các record bị thay đổi, không phải allowlist.

## Control fingerprint

Control là full-population control v2, giữ nguyên trong suốt A/B:

- builder: `ac3a7a798e792dc1368b800a6bbf831cf951a10f43a34f7e7d49db8361974a3d`;
- source-first lookup: `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`;
- proposal verifier: `0179d78644ee7acb7bd9c0b7cd94264c8ed82dd852dab6ab25dcbb1e67bb42f4`;
- submission: `artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v2/control/submission/submission.json`, SHA-256 `81168a73f7748b3193aade069bbb1cfaf8d64ffab99bb416ec84ddfa161d0bb6`;
- ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v2/control/submission.zip`, SHA-256 `cd2d06bc09f67e0df57df8c8a1913208fe1d2c3d1590c649c65a74f7e1300b16`;
- control build report SHA-256: `3f6dff2651635b94d6f831c8a698b7f3ac5215afe3df203d4cec2e73dcbc3a4a`;
- control diagnostics SHA-256: `b7209d5faff8992239f33ada2c77ef88f7892fb7be3d4241b782387f8f0d3258`.

## Candidate fingerprint

Candidate v4 dùng đúng ba base fingerprint của control và thêm adapter
candidate-only:

- snapshot: `artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/snapshot`;
- contextual adapter: `scripts/research/run_contextual_score_lift_variant_v1.py`, SHA-256 trong snapshot `a17722579b94afe191ba8ce6a5d875f536b290e7244770193017d9bd64577414`;
- candidate submission: `artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/candidate/submission/submission.json`, SHA-256 `728d419dd8f21e2605b07bf5fa89334f266cc3c386f5be08b1123067174265b4`;
- candidate ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/candidate/submission.zip`, SHA-256 `bd50add87762175e32af0e579b759abca75b09c1b32b8126f6d25de98b7e41fd`;
- candidate build report SHA-256: `5cbda3d9c16249db47c35af34155b00848b32fea3ce5eb89bd533265e5712ae9`;
- candidate diagnostics SHA-256: `5c2d304c5099443696d95abbcf1d0e90ddce7a4d1e067c006a8e3ced661680b5`;
- experiment contract: `artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/EXPERIMENT_CONTRACT.md`, SHA-256 `2b9694bc05c5be54460f5877ec60ea37f9d5517e36ca34545af84df4a8c11a71`.

## Population and split

- Population cuối: 1.012/1.012 questions, không bỏ record nào;
- input: `artifacts/kaggle_upload/dungle2810_vifinqa_answer_optimized_v2_20260830/runtime/questions.jsonl`, SHA-256 `64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0`;
- review bundle SHA-256: `774791be629bec6a652b8e29c3408ce2d3b749a0e9ebc0f08360b20eb0ba7ed9`;
- replay SHA-256: `ccb61012779a64c96a31e07dc875615fdc04973e4918edee35907a3090112372`;
- full structured table asset SHA-256: `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`, 146.246 tables;
- source-line map SHA-256: `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`, 146.246 entries;
- discovery/development có thể dùng subset để hình thành hypothesis, nhưng kết
  quả A/B này chạy trên complete population và control/candidate dùng cùng
  runtime inputs.

## Scorer/gold

Có các receipt scorer cũ trong workspace, nhưng chưa có receipt nào được bind
với đúng candidate v4. Cụ thể, q72 có official score `0.2174` trong
`artifacts/scoring/vifinqa_research_integrated_q72_20260831_r1/`, còn q73 chỉ
là user-reported `0.2194`; hai receipt này dùng submission hash khác và không
được dùng để suy ra score của v4. Không có independent gold-answer artifact
hoặc scorer command đã chạy cho cặp control-v2/candidate-v4. Vì vậy:

```text
Scorer/gold for this A/B: NOT_AVAILABLE
Scorer command for this A/B: NONE
Scorer exit status for this A/B: NOT_RUN
```

`unzip -t` và replay chỉ là engineering/structural validation, không phải
scorer. Không được suy ra accuracy từ bốn answer diff, route tier, Decimal
replay, source coverage hay ZIP validity.

## Mandatory A/B metrics

```text
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
```

### Improved / regressed / unchanged / unresolved

Đây là các số structural, không phải số accuracy:

- changed answer: 4;
- unchanged answer: 1.008;
- changed ngoài bốn record thuộc các family mục tiêu: 0;
- candidate nonzero answers: 960, control 957;
- candidate predicted questions: 970, control 970;
- candidate fallback questions: 42, control 42;
- verification classes ở cả hai: `PARTIAL=968`, `UNRESOLVED=44`;
- correctness improved: `NOT_MEASURED`;
- correctness regressed: `NOT_MEASURED`;
- unresolved verification: 44 ở cả control và candidate; failure reason count
  của build là `NO_EVIDENCE=42`, `SOURCE_COORDINATE_MISSING=3` theo report
  hiện hành. Các số này không phải answer-error counts.

### Family-level results

Route-level wins/losses dưới đây chỉ nói candidate đã thay route/answer theo
contract; semantic correctness vẫn `NOT_MEASURED`.

| Family | Control | Candidate | Structural result | Correctness |
|---|---:|---:|---|---|
| Reverse conditional temporal | 0 contextual selections | 2 contextual selections | 2 route wins, 0 route regressions; tracking records Q530/Q533 | NOT_MEASURED |
| Multi-entity contextual direct aggregation | 0 contextual selections | 2 contextual selections | 2 route wins, 0 route regressions; tracking records Q898/Q915 | NOT_MEASURED |
| Non-target population | unchanged | unchanged | 0 answer changes ngoài target families | NOT_MEASURED |

Candidate answers/evidence đã được ghi lại như sau:

- Q530 tracking record: `190.759675`; condition metric được replay qua 2021--2025 và answer metric là row `Nợ đủ tiêu chuẩn` của NAB năm 2025;
- Q533 tracking record: `10.6078`; năm được chọn là 2020 theo metric điều kiện và answer metric là `Trong vòng một năm`;
- Q898 tracking record: `1775135.016586`; ba operand DIG/VRE/PDR được replay từ current table với context `related_party_short_other_payable`;
- Q915 tracking record: `1733.846929932`; MPC + SAB + HAG, trong đó HAG dùng raw compound literal `-1433862140` với `source_multiplier=1000`.

Ở cả bốn record, local verifier hiện trả `verification_class=PARTIAL`,
`source_binding=PASS`, `provenance_integrity=PASS`, nhưng `authority=none` và
không có strict canonical certificate. Đây là candidate evidence, không phải
`VERIFIED`.

## Diagnostic-only metrics and engineering gates

Candidate build report ghi nhận:

- `validation.valid=true`;
- records: 1.012;
- queries replayed: 1.012;
- errors: `[]`;
- source-line map: `status=PASS`, map entries 146.246/146.246, missing 0,
  extra 0, local-ordinal fallback 0;
- evidence CSV: 1.012 files;
- diagnostics JSONL: 1.012 lines;
- prediction audit ledger: 1.012 lines;
- output ZIP size: 541,193 bytes;
- `unzip -t .../contextual_score_lift_ab_v4/candidate/submission.zip`: exit
  status `0`, no compressed-data errors;
- contextual route counts: reverse conditional `considered=2,
  resolved=2`; direct aggregation `considered=2, resolved=2`;
- route lookup vẫn có `promotion_allowed=false`; verifier authority là `none`.

Exact candidate build command là invocation của
`artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/snapshot/scripts/research/run_contextual_score_lift_variant_v1.py`
với `PYTHONPATH`, `VIFINQA_CONTEXTUAL_BUILDER_PATH`,
`VIFINQA_CONTEXTUAL_SOURCE_FIRST_LOOKUP_PATH` trỏ vào cùng snapshot, toàn bộ
input paths ở mục Population, `--expected-question-count 1012`,
`--require-full-population`, `--require-source-line-map`,
`--direct-replay-include-provisional`, và ba disable flags giữ nguyên như
control. Build exit status là `0`.

## Artifact paths and hashes

- [v4 candidate submission](artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/candidate/submission/submission.json)
- [v4 candidate ZIP](artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/candidate/submission.zip)
- [v4 build report](artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/candidate/submission/build_report.json)
- [v4 diagnostics](artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/candidate/submission/diagnostics.jsonl)
- [v4 experiment contract](artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/EXPERIMENT_CONTRACT.md)
- [contextual adapter](scripts/research/run_contextual_score_lift_variant_v1.py)
- [compound-literal verifier](src/finance_query/e2e/core/proposal_verifier.py)

## Decision

```text
Decision: INVESTIGATE_FURTHER
Authority status: CANDIDATE_ONLY
```

Lý do: v4 đạt đầy đủ engineering gates và không tạo non-target answer change,
nhưng chưa có independent scorer/gold để phân biệt bốn thay đổi là accuracy
gain hay semantic regression. Artifact nên được giữ làm candidate cho vòng
đánh giá kế tiếp; chưa được promotion, strict verification hoặc release.

Next queue có acceptance rõ ràng: chạy control v2 và candidate v4 qua cùng
official/independent scorer; báo answer/execution accuracy cùng family wins,
losses và regressions. Chỉ khi scorer chứng minh delta dương và không vượt
regression tolerance mới mở strict semantic/provenance/policy verification.
