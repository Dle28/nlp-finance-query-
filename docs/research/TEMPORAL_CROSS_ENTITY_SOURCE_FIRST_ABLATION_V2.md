# Temporal source-first ablation V2

Ngày: 2026-08-30  
Phạm vi: 1.012 câu ViFinQA; isolated best-effort candidate lane cho temporal
change và cross-entity plans bị nhận diện thành temporal shape.

## Kết luận

Lane `source_first_temporal_v1` được bật trong variant và đổi đúng 10/1.012
prediction so với control. Trong đó 7 câu là phép chênh lệch và 3 câu là phần
trăm thay đổi; 2 câu được phục hồi từ `cross_entity_comparison` có một mã và
hai năm, trong đó một câu là phần trăm trên cùng một mã. Mỗi operand được
hydrate từ structured table hiện tại, kiểm tra UID/document/row/column/unit,
rồi replay bằng `Decimal` trước khi tạo proposal.

Đây là candidate gain có provenance, không phải tuyên bố `Answer Accuracy` hay
`Execution Accuracy` chính thức: workspace không có gold answer key hoặc
official scorer cho split này. Mọi proposal vẫn là `PARTIAL`,
`confidence_class=BEST_EFFORT`, `promotion_allowed=false`.

## Controlled A/B

Hai process dùng cùng questions, review bundle, replay, full structured table
asset, source-line map, direct-evidence replay, research candidates, route
overlay và candidate-validity model. Cả hai cùng tắt các lane khác; khác biệt
có chủ đích duy nhất là control tắt thêm temporal lane:

- `--disable-source-first-report-year-neighbor`
- `--disable-source-first-candidate-bound`
- `--disable-source-first-conditional-temporal`
- `--disable-source-first-cross-entity`
- control thêm `--disable-source-first-temporal`

Snapshot dùng cho cả hai nhánh:
`/tmp/vifinqa-temporal-cross-ab-v2.mSUMBF`. Cả hai report đều ghi
`changed_during_build=false`.

Artifacts:

- Control report: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/control/submission/build_report.json`
- Variant report: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission/build_report.json`
- Control ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/control/submission.zip`
- Variant ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission.zip`
- Variant `submission.json`: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission/submission.json`
- Variant diagnostics: `artifacts/runs/vifinqa_answer_optimization_20260830/temporal_cross_entity_ab_v2/variant/submission/diagnostics.jsonl`

Implementation fingerprint:

- Builder SHA-256: `5b5fc4d77348ce0568523b1fd51d85031d9217faa24196a7cdcbad410b05c866`
- Source-first lookup SHA-256: `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- Structured table asset: 146,246 tables;
  SHA-256 `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- Source-line map: 146,246 entries;
  SHA-256 `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`
- Control ZIP SHA-256: `941dea4d55649cee0b3c807164ba4385567a5d9579cf7a21d83cd532b8dcdfa2`
- Variant ZIP SHA-256: `36d4cfd6c246a1809dbb8959a1ea67069b438d13ad5632944d516e7cc53e8045`

| Metric | Control | Variant | Delta |
|---|---:|---:|---:|
| Records | 1,012 | 1,012 | 0 |
| Non-zero answers | 999 | 999 | 0 |
| Query replay | 1,012 | 1,012 | 0 |
| Validation errors | 0 | 0 | 0 |
| Source-map entries | 146,246 | 146,246 | 0 |
| Source-map missing/fallback | 0 / 0 | 0 / 0 | 0 |
| Temporal questions considered | 0 | 78 | +78 |
| Temporal proposals resolved | 0 | 10 | +10 |
| Temporal unresolved/ambiguous | 0 | 68 | +68 |
| Recovered from cross-entity family | 0 | 39 | +39 |
| ZIP integrity | PASS | PASS | 0 |

Variant operation telemetry: 7 `subtract`, 3 `percentage_change`. The
`source_first_cross_entity_lookup` lane itself was disabled in both branches;
the 39 count above is the temporal adapter's explicit single-ticker/two-year
recovery path, not a cross-issuer answer.

## Answer delta

The two `submission.json` files were compared by ID across all 1,012 records.
Exactly 10 answer values and tiers changed:

| Q | Control | Variant | Source-first replay |
|---:|---:|---:|---|
| 578 | `-29.059234208` | `333.269120577` | BAF: `Trả trước cho người bán ngắn hạn`, 2025 minus 2022, billion VND |
| 579 | `-81.57491945944844` | `167.23992696016566` | IJC: `Lãi tiền gửi có kỳ hạn`, 2016 to 2021, percentage growth |
| 585 | `22743000000.0` | `6717.658180539067` | STB: `Quỹ khen thưởng phúc lợi`, 2022 versus 2016, percentage growth |
| 604 | `264.0` | `123.977414336` | DLG: `TỔNG CỘNG TÀI SẢN`, 2019 minus 2018, billion VND |
| 613 | `-301534.111657` | `186259.45083` | IJC: `Lưu chuyển tiền thuần từ hoạt động tài chính`, 2022 minus 2018, million VND |
| 621 | `3.059363382827` | `20.799329684971` | VJC: `Chi phí nhiên liệu`, 2024 minus 2021, thousand billion VND |
| 629 | `-53401178126.0` | `-46.51615255748988` | HBC: `Dự phòng phải thu ngắn hạn khó đòi`, percentage change, 2016 to 2020 |
| 630 | `-2207215.0` | `-805232.0` | SHB: `Chi phí thuế TNDN hiện hành`, 2025 minus 2024, million VND |
| 643 | `319895796.0` | `41586082.0` | KLB: `Số lượng cổ phiếu đang lưu hành`, 2023 minus 2019 |
| 654 | `510.983001009` | `74.77570427` | DNH: `Giá vốn bán điện`, 2017 minus 2016, billion VND |

Q585 is the one row recovered from the cross-entity family whose question
mentions one issuer and two years; it remains a same-issuer temporal replay.
The route metadata changes to `source_first_temporal_cross_entity_v1` for the
8 cross-family-shaped records that use the temporal adapter, while ordinary
temporal records use `source_first_temporal_v1`.

## Independent source replay

An independent checker re-read the 20 selected UIDs from the frozen
`full_table_assets_v1.jsonl`, checked document identity and row/column bounds,
matched the selected row signature, parsed the raw source cell, verified the
source-line-map membership, and recomputed:

`raw_value * source_multiplier / requested_divisor(question)`

It then replayed the emitted subtraction or percentage formula from the two
CSV operands. All 10 changed predictions passed all checks:

| Check | Result |
|---|---:|
| Selected UIDs hydrated from full corpus | 20 / 20 |
| Selected UIDs covered by source-line map | 20 / 20 |
| UID/document/row/column/raw-cell/unit checks | 10 / 10 questions pass |
| Deterministic formula replay | 10 / 10 questions pass |
| Independent source replay | PASS |

The builder's own report also records `validation.records=1012`,
`queries_replayed=1012`, `errors=[]`, map coverage 146,246/146,246 and
`source_line_coordinates.status=PASS`.

## Safety boundary and disposition

The lane remains narrow by contract:

- one ticker and two explicit report years;
- exact report-year selection, with only `exact_contiguous` or
  `ordered_with_ocr_gap` source matching;
- same semantic row signature across both years;
- when scope is omitted, the two source scopes must agree;
- no arbitrary duplicate-source selection and no promotion from source replay
  to human/semantic authorization.

The 10 source-replayed changes are suitable for the authorized best-effort
candidate path and for official-scoring evaluation when a scorer is available.
They are not a strict release certificate and were not submitted to a
leaderboard in this run.

## Next score queue

1. Test composition for `Tổng cộng dự phòng phải trả` using two independently
   replayed component rows with one document/scope/unit gate.
2. Keep `Nghĩa vụ nợ tài chính` and duplicate-source Q323 fail-closed until
   table function and scope semantics are resolved.
3. Build one combined best-effort artifact only after each new lane has its own
   isolated A/B and independent replay evidence.
