# Source-first multi-year extrema: controlled A/B V1

Ngày: 2026-08-30  
Phạm vi: ViFinQA 1.012 câu, full structured corpus 146.246 tables.

## Kết luận ngắn

Family này đã được kiểm tra bằng một A/B sạch trên cùng snapshot mã bất biến.
Variant nhận diện 27 câu có hình dạng hỏi max/min qua nhiều năm và mở khóa 13
câu sau khi kiểm tra source-year, row identity, scope, unit và uniqueness của
winner. Có 13 answer thay đổi so với control; 14 câu còn lại bị giữ
`UNRESOLVED/AMBIGUOUS` hoặc không đủ contract.

Đây là gain về coverage/proposal lane có source replay, chưa phải claim
`Answer Accuracy +13` hay leaderboard score. Workspace không có gold answer và
official scorer cho split này. Route được giữ ở
`authorized_best_effort_submission_candidate` với `promotion_allowed=false`;
không tự cấp `VERIFIED`, không cấp `human_verified` và chưa gửi Kaggle.

## Contract được phép mở khóa

Resolver `source_first_period_extreme_v1` chỉ nhận:

1. Một ticker duy nhất và ít nhất ba năm explicit trong câu hỏi.
2. Operator rõ ràng là max hoặc min; các câu có điều kiện, filter, ratio,
   reclassification, flow hoặc composition không đi vào route này.
3. Metric phải khớp một family accounting có alias đã khai báo. Alias gross
   không được dùng cho metric net; ví dụ `Doanh thu bán hàng và cung cấp dịch
   vụ` không được thay thế `Doanh thu thuần`.
4. Mỗi năm phải hydrate trực tiếp từ đúng report-year trong structured table;
   fallback neighboring report-year bị tắt trong A/B này.
5. Tất cả giá trị phải cùng row signature sau khi bỏ decoration/section number,
   cùng scope và cùng bảng loại hợp lệ. Acquisition/event snapshot và bảng
   governance bị loại.
6. Mỗi năm phải có đúng một ứng viên numeric hợp lệ; max/min phải có một
   winner duy nhất. Tie, row drift, scope ambiguity và mixed-sign representation
   không được đoán.
7. Raw cell phải được replay qua Decimal với source multiplier và output-unit
   divisor đúng. Route không lấy số từ model, ranking hoặc OCR candidate làm
   authority.

## Snapshot và input bất biến

Hai arm dùng cùng builder snapshot:

- `build_competition_submission_v1.py` SHA-256:
  `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e`
- `source_first_lookup.py` SHA-256:
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`
- Snapshot builder:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/snapshot/scripts/e2e/build_competition_submission_v1.py`
- Full table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- Source-line map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`

`build_report.json` ghi cùng builder/source hash ở lúc load và lúc report ở cả
hai arm, với `changed_during_build=false`.

Các route experimental khác được tắt đối xứng trong A/B để không trộn causal
effect: report-year neighbor, reclassified direct, financial liability/
receivables total, multi-entity selector/ratio/threshold, composed total,
candidate-bound, conditional temporal, temporal, cross-entity, multi-entity
direct aggregation và multi-entity conditional-count. Control tắt thêm
`--disable-source-first-period-extreme`; variant bật route này.

## Terminal gates

Artifact:

- Control report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/control/submission/build_report.json`
- Variant report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/variant/submission/build_report.json`
- Control submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/control/submission/submission.json`
- Variant submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/variant/submission/submission.json`
- Variant audit ledger:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/variant/submission/prediction_audit_ledger_v1.jsonl`
- Control ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/control/submission.zip`
- Variant ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/variant/submission.zip`

| Gate | Control | Variant |
|---|---:|---:|
| `validation.valid` | `true` | `true` |
| records | 1.012 | 1.012 |
| query replay | 1.012 | 1.012 |
| validation errors | `[]` | `[]` |
| `changed_during_build` | `false` | `false` |
| ZIP integrity (`unzip -t`) | PASS | PASS |
| ZIP size | 532.964 bytes | 535.866 bytes |

Variant telemetry:

| Metric | Value |
|---|---:|
| questions considered | 27 |
| questions resolved | 13 |
| unresolved/ambiguous | 14 |
| cases with 3 years | 3 |
| cases with 4 years | 4 |
| cases with 5 years | 6 |
| protocol | `source_first_period_extreme_v1` |
| promotion | `false` |

## Answer diff

The 13 rows below are the exact serialized answer changes in the clean A/B.
Every variant row is `source_first_period_extreme_v1`; the corresponding
control row was `semantic_cell_heuristic`.

| Question | Control | Variant | Winner year |
|---:|---:|---:|---:|
| Q815 | `46.999721794` | `145.113883664` | 2022 |
| Q835 | `449.12019567` | `654.643132429` | 2016 |
| Q838 | `-0.002696` | `35.318781` | 2024 |
| Q845 | `2.286` | `2.843` | 2024 |
| Q847 | `0.745801791` | `22.820769751` | 2019 |
| Q859 | `-238.792109402` | `335.746014085` | 2023 |
| Q903 | `-0.18272135154` | `0.10660207796` | 2020 |
| Q911 | `0.23955` | `145.182929479` | 2017 |
| Q914 | `0.35` | `3.375` | 2022 |
| Q969 | `780.977719441` | `830.00008186` | 2019 |
| Q972 | `-40.170176148` | `879.010112441` | 2021 |
| Q988 | `485254333024` | `3375` | 2022 |
| Q996 | `1047` | `4442784` | 2021 |

Control and variant have the same answer for all other questions. The earlier
pre-guard prototype had a route-only change for Q886; it is intentionally not
resolved in this clean arm because its 2015 value is positive while its 2022
and 2023 values are negative. This mixed-sign series is a semantic warning,
not a safe max/min candidate.

## Independent source-coordinate audit

The audit reloaded the full corpus independently from the build process and
checked every evidence CSV row for all 13 answer changes:

- UID exists in the 146.246-table full asset;
- UID exists in the 146.246-entry source-line map;
- document ID matches;
- row/column bounds are valid;
- raw evidence value equals the raw structured-table cell;
- each requested report-year is present in the corresponding source document.

Result: **13/13 questions PASS; 13/13 answer cells and all 45 supporting
year-cells PASS**. Evidence files are at
`artifacts/runs/vifinqa_answer_optimization_20260830/period_extreme_ab_v5/variant/submission/data/qNNNN_evidence.csv`.

| Question | Source years | Winner | Source-line-map lines |
|---:|---|---:|---|
| Q815 | 2017, 2020, 2021, 2022 | 2022 | 206, 333, 425, 307 |
| Q835 | 2016, 2017, 2018, 2019, 2020 | 2016 | 660, 677, 782, 791, 816 |
| Q838 | 2017, 2018, 2020, 2021, 2024 | 2024 | 133, 189, 152, 233, 179 |
| Q845 | 2017, 2019, 2022, 2024 | 2024 | 468, 306, 348, 317 |
| Q847 | 2019, 2020, 2021, 2023, 2024 | 2019 | 358, 417, 444, 524, 272 |
| Q859 | 2022, 2023, 2024 | 2023 | 401, 428, 419 |
| Q903 | 2017, 2018, 2020, 2021, 2022 | 2020 | 138, 159, 179, 212, 272 |
| Q911 | 2017, 2020, 2021, 2022, 2024 | 2017 | 294, 284, 230, 238, 236 |
| Q914 | 2019, 2020, 2021, 2022, 2023 | 2022 | 362, 389, 358, 450, 534 |
| Q969 | 2017, 2018, 2019 | 2019 | 1019, 1085, 1158 |
| Q972 | 2021, 2023, 2025 | 2021 | 265, 373, 396 |
| Q988 | 2019, 2021, 2022, 2023 | 2022 | 362, 358, 450, 534 |
| Q996 | 2016, 2018, 2020, 2021 | 2021 | 194, 280, 121, 114 |

The map lines above are the source-line-map coordinates for the supporting
table records; row/column and raw-cell details are preserved in each evidence
CSV and in the variant audit ledger.

## Safety guards verified by canary tests

The focused suite was run after the implementation:

```text
8 passed, 81 deselected in 0.28s
```

It covers a positive stable-row max, tie rejection, row identity drift,
parenthetical `Lãi/(lỗ)` row normalization, gross-vs-net revenue rejection,
acquisition schedule rejection, scaled Decimal preservation (`3.375`, not
`3375`) and mixed-sign provision rejection. The route therefore remains
fail-closed when a numerical replay is technically possible but semantic
identity is not established.

## Decision and next gate

Keep this narrow route enabled in the authorized best-effort candidate lane.
Do not merge its 13 changes into a strict release and do not describe them as
official score gains until the same split is evaluated by the authoritative
scorer (or a human-approved gold set is supplied). The next useful experiment
is not a global threshold relaxation: audit the 14 unresolved cases by failure
family (metric alias, scope ambiguity, table kind, row drift, tie, and missing
year), then select at most one family for another immutable-snapshot A/B.
