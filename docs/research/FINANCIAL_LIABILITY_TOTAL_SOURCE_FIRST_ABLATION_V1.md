# Financial-liability maturity total: source-first ablation v1

Date: 2026-08-30  
Status: accepted local best-effort A/B; not an official leaderboard score

## Objective

The question family asks for **Tổng cộng nghĩa vụ nợ tài chính** (the total of
financial liabilities by contractual maturity). The existing fallback could
select the row **Các nghĩa vụ nợ tài chính khác**, which is a component rather
than the requested total. This ablation adds a narrow source-first direct
lookup for the maturity-liability total and keeps it separate from the strict
verification lane.

The contract is deliberately fail-closed. It requires the question phrase,
the maturity-table headers, liability context, a liability table section, and
the requested year in the table's own header. It rejects non-total rows,
comparative tables whose own header is the prior year, and asset-maturity
tables even when their numeric total looks plausible.

## Frozen A/B snapshot

Both builds loaded the same copied implementation before execution:

```text
builder_sha256: 09a63768f06e5762ae5c1de1c7569d67f0b130d6f10c407c710297400b2d2d84
source_first_lookup_sha256: c4c8c6495e9e232e2e44c4febe09852d6765d9c04a0308fa95cd0f3b27d2995b
corpus_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false
```

The frozen snapshot directory is recorded at:
`artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/snapshot_dir.txt`.
The implementation fingerprints are also preserved in
`artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/snapshot_sha256.txt`.

Control disabled only `--disable-source-first-financial-liability-total`.
Variant enabled the lane. All other inputs and flags were held constant.

## Result

The complete submission diff contains exactly one answer change:

| Question | Control | Variant | Tier change | Interpretation |
|---|---:|---:|---|---|
| Q261 | `16496708` | `174052754` | fallback semantic cell -> `source_first_reclassified_direct_v1` | total row replayed from the 2019 consolidated liability-maturity table |

The variant evidence is:

```text
document_id: BVH_financial_statements_2019_consolidated
internal_table_uid: d3eff4436ee4bd8227a656ad9b18952a1a49f08dba8c86f03f0c13e4534e528f
table_section.kind: liability
table header: Tại ngày 31 tháng 12 năm 2019
unit: triệu đồng
row: TỔNG CỘNG
row_index: 8
column_index: 6
raw cell: 174.052.754
replayed answer: 174052754
```

The independent corpus record for this UID contains the full row
`TỔNG CỘNG > 10.641 > - > 11.836.096 > (23.549.138) > 185.755.155 >
174.052.754`, with `report_year=2019`, `scope=consolidated`, and
`unit_hint=million_vnd` represented in the corpus as `million_vnd`. The
submission CSV records `source_multiplier=1000000`, so the answer is in the
question's requested million-dong unit.

The guard also explains why nearby candidates are not interchangeable:

- UID `51d633...` is a comparative table whose own header is 2018 and is
  rejected for a 2019 request.
- UID `0f0712...` is an asset-maturity table with total `161502778` and is
  rejected by the liability-section contract.
- UID `c9e33f...` contains the financial-liabilities component
  `16,496,707,558,172` VND, not the contractual-maturity total.

## Integrity gates

Both control and variant passed the local release checks:

```text
records: 1012
queries_replayed: 1012
validation.errors: []
source-line map entries: 146246
map coverage: 146246 / 146246
source-line status: PASS
submission.zip: unzip -t PASS
```

Reports:

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/variant/submission/build_report.json)
- [control submission](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/control/submission.zip)
- [variant submission](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/variant/submission.zip)
- [control Q261 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/control/submission/data/q0261_evidence.csv)
- [variant Q261 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/variant/submission/data/q0261_evidence.csv)

## Scope and limitation

This is a prediction improvement backed by deterministic source replay. It is
not human semantic approval, a strict certificate, or an official score gain:
the current public evaluation setup does not provide a verified gold answer
or a local official scorer for this comparison. The variant is therefore an
authorized best-effort submission candidate, and the lane remains
`promotion_allowed=false` until an independent semantic/gold gate is
available.

The current code reports this as one additional resolved reclassified-direct
question (`9` resolved versus `8` in control). The next high-leverage family
to investigate is explicit multi-entity direct `mean`/`sum` aggregation, but
it should be introduced only with per-entity source replay and fail-closed
rejection when any entity is missing, duplicated, or scope-incompatible.
