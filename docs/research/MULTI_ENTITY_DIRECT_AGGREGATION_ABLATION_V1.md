# Multi-entity direct aggregation source-first ablation V1

Ngày: 2026-08-30  
Phạm vi: ViFinQA, 1.012 câu trong full-corpus submission build.

## Kết luận ngắn

Route `source_first_multi_entity_direct_aggregation_v1` được giữ ở
authorized best-effort submission lane. Route chỉ nhận các câu có nhiều mã cổ
phiếu rõ ràng, một năm, scope `công ty mẹ`/`separate`, và một metric thuộc
family đã được khai báo. Mỗi mã được replay độc lập qua source-first resolver;
chỉ sau khi mọi mã pass mới tính `mean` hoặc `sum` bằng `Decimal`.

Clean A/B mở route cho đúng ba câu và làm thay đổi đúng ba answer:

| Question | Control | Variant | Operation | Ticker count |
|---:|---:|---:|---|---:|
| Q827 | 6.387361021955 | 602.37564375975 | mean | 4 |
| Q858 | 0.015537986326 | 0.530161279161 | sum | 3 |
| Q927 | 2.542217408 | 37.2454051892 | mean | 5 |

Đây là thay đổi candidate prediction, không phải bằng chứng tăng official
accuracy: local workspace không có gold/scorer đủ để xác nhận accuracy mới.

## Snapshot và input closure

Hai arm dùng cùng implementation snapshot:

- builder SHA-256:
  `f29df1dbfebef0a0884cb65406ef8b3d44ec4faeefe93c84e895e3f2734025cd`;
- `source_first_lookup.py` SHA-256:
  `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`;
- structured asset:
  `full_table_assets_v1.jsonl`, 146.246 tables,
  SHA-256 `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source-line map: 146.246/146.246 entries,
  SHA-256 `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`;
- `implementation.changed_during_build=false` ở cả control và variant.

Control tắt riêng `--disable-source-first-multi-entity-direct-aggregation`;
variant không truyền flag này. Cả hai arm dùng cùng câu hỏi, runtime bundle,
research inputs, replay ledger và source-line map; period-neighbor và
threshold route đều được tắt cho attribution này.

## Validation gates

Cả hai arm đều đạt:

- `validation.valid=true`;
- 1.012 records;
- 1.012/1.012 queries replayed;
- `errors=[]`;
- 1.010 `PARTIAL`, 2 `UNRESOLVED`;
- `unzip -t` không có lỗi, archive có 1.013 entries.

Variant telemetry:

```json
{
  "questions_considered": 3,
  "questions_resolved": 3,
  "kind_tax_payable": 2,
  "kind_selling_expense": 1,
  "operation_mean": 2,
  "operation_sum": 1,
  "entity_count_3": 1,
  "entity_count_4": 1,
  "entity_count_5": 1,
  "requires_explicit_parent_company_scope": true,
  "requires_per_entity_current_table_replay": true,
  "promotion_allowed": false
}
```

Artifact A/B:

- control: `artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v1/control/submission/`;
- variant: `artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v1/variant/submission/`;
- variant ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v1/variant/submission.zip`.

## Source replay audit

Mỗi dòng dưới đây được kiểm tra lại từ full structured asset tại đúng UID,
`row_index`, `column_index`; `source_path` có SHA khớp với asset và source-line
map trỏ tới dòng nguồn có raw literal tương ứng.

### Q827 — mean, đơn vị tỷ đồng

Question yêu cầu mean của `Tổng thuế và các khoản phải nộp Nhà nước` tại
scope công ty mẹ năm 2019. Các operand replay được:

| Mã | Document / row label | Raw source cell | Source unit | Operand (tỷ VND) |
|---|---|---:|---|---:|
| SNZ | `SNZ_financial_statements_2019_separate`, row 5 col 3 | `15.344.409.381` | VND | 15.344409381 |
| VIC | `VIC_financial_statements_2019_separate`, row 5 col 3 | `2.050.099` | million VND | 2050.099 |
| DXS | `DXS_financial_statements_2019_separate`, row 5 col 3 | `185.442.713.325` | VND | 185.442713325 |
| HPX | `HPX_financial_statements_2019_separate`, row 5 col 3 | `158.616.452.333` | VND | 158.616452333 |

`(15.344409381 + 2050.099 + 185.442713325 + 158.616452333) / 4 =
602.37564375975`.

### Q858 — sum, đơn vị nghìn tỷ đồng

Question yêu cầu tổng `Chi phí bán hàng` của ba công ty mẹ năm 2017 và dùng
đơn vị nghìn tỷ đồng. Dấu âm trong ngoặc được giữ khi parse:

| Mã | Document / row label | Raw source cell | Operand (nghìn tỷ VND) |
|---|---|---:|---:|
| SAB | `SAB_financial_statements_2017_separate`, row 9 col 3 | `(1.446.841.604.384)` | -1.446841604384 |
| DBC | `DBC_financial_statements_2017_separate`, row 9 col 3 | `(83.645.537.443)` | -0.083645537443 |
| MCH | `MCH_financial_statements_2017_separate`, row 9 col 3 | `2.060.648.420.988` | 2.060648420988 |

`-1.446841604384 - 0.083645537443 + 2.060648420988 =
0.530161279161`.

### Q927 — mean, đơn vị tỷ đồng

Question yêu cầu mean của số dư thuế cuối năm 2016 tại năm công ty mẹ:

| Mã | Document / row label | Raw source cell | Operand (tỷ VND) |
|---|---|---:|---:|
| VPI | `VPI_financial_statements_2016_separate`, row 5 col 3 | `2.542.217.408` | 2.542217408 |
| DIG | `DIG_financial_statements_2016_separate`, row 5 col 3 | `26.323.895.303` | 26.323895303 |
| VRE | `VRE_financial_statements_2016_separate`, row 5 col 3 | `35.068.093.552` | 35.068093552 |
| DXG | `DXG_financial_statements_2016_separate`, row 5 col 3 | `69.976.249.971` | 69.976249971 |
| PDR | `PDR_financial_statements_2016_separate`, row 5 col 3 | `52.316.569.712` | 52.316569712 |

`(2.542217408 + 26.323895303 + 35.068093552 + 69.976249971 +
52.316569712) / 5 = 37.2454051892`.

## Safety boundary and next work

The route is intentionally not a generic multi-entity solver. It currently
accepts only `tax_payable` and `selling_expense`, rejects ranking/conditional/
ratio/comparison cues, requires one exact report year and explicit parent-
company scope, and rejects a result if any issuer is missing, scope-conflicts,
or fails the table-kind/row/column gate. This keeps ticker contamination and
aggregation over mixed units out of the accepted candidate.

The selected artifact remains `PARTIAL`/best-effort. The fields
`machine_artifact_is_not_human_verified=true` and `promotion_allowed=false`
must remain intact; source replay and local arithmetic do not create a strict
certificate or release authority.
