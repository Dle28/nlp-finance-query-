# Source-first composed total: ablation V1

Ngày: 2026-08-30  
Protocol: `source_first_composed_total_v1`

## Kết luận

Một family rất hẹp đã được kiểm định cho câu hỏi hỏi “tổng cộng dự phòng
phải trả” nhưng compiler tạo plan aggregation không có operands. Route không
cộng các dòng tùy ý: nó chỉ nhận một mã, một năm, một câu hỏi không có cue
comparison/selection, và đúng cặp dòng `Dự phòng phải trả ngắn hạn` + `Dự
phòng phải trả dài hạn`.

Controlled A/B trên 1.012 câu cho thấy:

- `questions_considered=1`, `questions_resolved=1`, `component_count_2=1`;
- đúng một answer đổi: Q175, từ `235664195.016` sang `32.587523656`;
- cả hai arm đều `validation.valid=true`, 1.012/1.012 query replay,
  `errors=[]`, source-line map 146.246/146.246 và `unzip -t` pass;
- không có thay đổi do retrieval/model/rank: các lane đó được giữ nguyên
  giữa hai arm, còn các source-first lane khác bị tắt để attribution sạch.

Đây là `authorized_best_effort_submission_candidate`, không phải
`VERIFIED`: workspace chưa có gold answer/official scorer, nên chưa thể gọi
delta này là `Answer Accuracy +1`.

## A/B snapshot và artifacts

Hai arm dùng cùng snapshot:

- builder SHA-256:
  `7ea39d9ed2b319e245097f76888b803ceb198cae275903e4d2ff513c2d14965b`;
- `source_first_lookup.py` SHA-256:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`;
- full table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- `changed_during_build=false` ở cả hai arm.

Artifacts:

- control report/ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/composed_total_ab_v1/control/submission/build_report.json`
  và `.../control/submission.zip`;
- variant report/ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/composed_total_ab_v1/variant/submission/build_report.json`
  và `.../variant/submission.zip`;
- variant audit ledger:
  `artifacts/runs/vifinqa_answer_optimization_20260830/composed_total_ab_v1/variant/submission/prediction_audit_ledger_v1.jsonl`.

## Q175: independent source replay

Câu hỏi:

> Tổng cộng dự phòng phải trả cuối năm 2020 của Tập đoàn Dệt May Việt Nam
> (VGT) là bao nhiêu tỷ đồng?

Source cell được chọn từ bảng
`VGT_financial_statements_2020_consolidated`, UID
`b81140c4879328b28319c042bfa9183c9e172e287aae684d3e484e9a2754a026`, cột
`31/12/2020 VND`:

| Component | Row | Raw VND | Converted billion VND |
|---|---|---:|---:|
| Short-term provision | `Dự phòng phải trả ngắn hạn` | `5,634,013,216` | `5.634013216` |
| Long-term provision | `Dự phòng phải trả dài hạn` | `26,953,510,440` | `26.953510440` |
| Sum | both rows, same table/scope/unit | `32,587,523,656` | `32.587523656` |

The variant evidence contains two distinct coordinates, both from the exact
2020 report year and the same table, document scope and source multiplier.
The pandas replay is explicitly restricted to the two component roles:

```text
float(df1.loc[df1.operand_role.isin(['composed_component_1','composed_component_2']), 'operand_value'].sum())
```

The control value `235664195.016` came from a semantic candidate row about
the parent company and is not a valid replay of the requested provision
total. The source-first route replaces it only after both component rows pass
the exact-row/period/same-table contract.

## Safety contract

The implementation in `scripts/e2e/build_competition_submission_v1.py`:

- rejects one-component questions and comparison/selection cues;
- requires exactly one ticker and one report year;
- resolves each component independently through the existing source-first
  Decimal replay;
- requires an exact/ordered source-row match, the requested report year, a
  non-governance table, and a non-empty source coordinate;
- requires the two components to share document, table UID, scope, source
  multiplier and table kind;
- writes both component coordinates into the audit ledger and keeps
  `promotion_allowed=false`.

The route should remain a best-effort candidate until the same variant is
checked against the official scorer. Future extensions must use an explicit
component specification for a metric family; generic “tổng” + arbitrary row
sum is not safe.

