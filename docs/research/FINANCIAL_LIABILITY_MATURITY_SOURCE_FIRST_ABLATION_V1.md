# Financial-liability maturity total: source-first ablation v1

Date: 2026-08-30  
Scope: ViFinQA 1,012-question best-effort submission lane  
Status: validated research result; not an official leaderboard score

## Research question

Can a narrow, fail-closed source-first contract recover a question that the
compiler classified as `multi_entity_or_period_aggregation` even though it
asks for one reported total from a financial-liability maturity schedule?

The tested family is the Vietnamese wording pattern
`Tổng cộng nghĩa vụ nợ tài chính ... đến ngày ...`. The route is intentionally
not a generic `TỔNG CỘNG` matcher. It requires all of the following:

- the question is a one-ticker, one-year `multi_entity_or_period_aggregation`
  item and uses the recognized maturity-total wording;
- the table is classified as a liability table;
- the local schedule contains the maturity headers (`Quá hạn`, `Không xác
  định kỳ hạn`, `Đến 01 năm`, `Từ 01 - 05 năm`, and `Trên 05 năm`);
- the table header contains the requested reporting date; and
- the selected row is a generic `TỔNG`/`TỔNG CỘNG` row whose unique numeric
  column is explicitly labelled `Tổng cộng`.

The route only proposes a current structured-table Decimal replay. It does
not create a strict verification certificate, human semantic approval, or
promotion authority.

## Reproducible A/B

Control and variant were built from the same immutable snapshot:

```text
builder_sha256: 09a63768f06e5762ae5c1de1c7569d67f0b130d6f10c407c710297400b2d2d84
source_first_lookup_sha256: c4c8c6495e9e232e2e44c4febe09852d6765d9c04a0308fa95cd0f3b27d2995b
changed_during_build: false
```

The control disabled only
`--disable-source-first-financial-liability-total`; the variant left the
route enabled. All other source-first experimental lanes were disabled in
both runs so the measured delta is attributable to this family.

Artifacts:

- Control: [build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/control/submission/build_report.json), [submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/control/submission.zip)
- Variant: [build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/variant/submission/build_report.json), [submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/variant/submission.zip)
- Variant Q261 evidence: [q0261_evidence.csv](../../artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/variant/submission/data/q0261_evidence.csv)

Both builds passed the mechanical gates:

```text
validation.valid       = true
records                = 1012
queries_replayed       = 1012
errors                 = []
nonzero_answers        = 999
PARTIAL                = 1010
UNRESOLVED             = 2
```

Both ZIPs passed `unzip -t` with `No errors detected` and contained 1,013
files. The strict certificate path remained null; therefore these are
best-effort submission candidates, not `VERIFIED` releases.

## Observed delta

The serialized submission changed exactly one answer: Q261.

| Item | Control | Variant | Change |
| --- | ---: | ---: | --- |
| Q261 answer | 16,496,708.0 | 174,052,754.0 | recovered source-first maturity total |
| Q261 tier | `semantic_cell_heuristic` | `source_first_reclassified_direct_v1` | route changed |
| Other serialized records | unchanged | unchanged | no additional answer/tier delta |

Variant route statistics were:

```text
questions_considered                  = 10
kind_financial_liability_maturity_total = 1
questions_resolved                    = 9
questions_unresolved_or_ambiguous     = 1
```

The other eight resolved records belong to the already-existing reclassified
families; the new maturity pattern contributes one resolved question in this
ablation.

## Independent source audit: Q261

Question:

> Tổng cộng nghĩa vụ nợ tài chính của Tập đoàn Bảo Việt (BVH) đến ngày 31
> tháng 12 năm 2019 là bao nhiêu triệu đồng?

The variant evidence selects:

```text
document_id:       BVH_financial_statements_2019_consolidated
internal_table_uid:d3eff4436ee4bd8227a656ad9b18952a1a49f08dba8c86f03f0c13e4534e528f8
table_section:     liability / Nợ phải trả
row_index:         8
row_label:         TỔNG CỘNG -
column_index:      6
raw_value:         174052754
source_multiplier: 1000000
answer:            174052754 million VND
```

The full structured-table row is:

```text
TỔNG CỘNG | 10.641 | - | 11.836.096 | (23.549.138) | 185.755.155 | 174.052.754
```

Its header is explicitly dated 31 December 2019 and its last column is
`Đơn vị: triệu đồng Tổng cộng`. The same corpus also contains an asset
maturity table and a prior-year liability table; the contract rejects those
alternatives through the liability section and local requested-year header
checks. Thus this result is a source-coordinate replay, not a retrieval-score
substitution.

## Decision

Keep the route in the authorized best-effort candidate lane. The ablation is
positive for coverage and source binding, but no official gold answers or
scorer are available locally, so the numeric change must not be reported as a
verified accuracy or leaderboard improvement. Before promotion to a strict
release, require the normal independent semantic/scope approval and the
complete canonical E2E certificate.
