# Report normalization and staged metric routing

This layer runs before lexical/dense retrieval and before any LLM reviewer.
It does not modify an OCR report or make an answer eligible.

```text
raw report
  -> V2 reconstructed source grid
  -> V3 canonical header/period/unit context
  -> document metadata
  -> table routing catalog
  -> metric-registry stage plan
  -> metadata-filtered retrieval
  -> exact V2/V3 evidence binding and deterministic calculation
  -> optional LLM review of a small, bounded candidate packet
```

## Source-preserving normalization

`scripts/build_report_normalization.py` writes two bundle-local sidecars:

- `document_metadata_v1.jsonl`: `company`, `report_year`, `report_scope`, and
  `report_type`, plus a source-derived `reporting_period_end` when available;
- `table_routing_catalog_v1.jsonl`: `table_type`, canonical variable row
  labels, explicit header years and V3 header-integrity status.

Table types are `balance_sheet`, `income_statement`,
`cash_flow_statement`, `equity_change_statement`, `notes`, `schedule`, or
`other`.  A table is routing-eligible only when V3 is `review_ready` and its
type is structurally observed.  A V2 row that was excluded from the canonical
header prefix remains recorded in `header_integrity`; it is never relabelled.

Canonical variable aliases are exact source-row aliases after removing only
structural prefixes, note references, a terminal page-continuation marker, and
a fully numeric accounting equation suffix such as `(100 = 110 + 120)`.
For example, the exact source labels `Lợi nhuận (thuần) sau thuế TNDN` map to
`net_income`; the raw label and row index remain in the sidecar, so every
match is auditable.  No OCR wording or numeric value is repaired.

Where a primary statement repeats the same text for a total and detail row,
the alias is additionally constrained by the literal source account code:
`100` for current assets, `140` for total inventory, and `310` for current
liabilities. This prevents `141 Hàng tồn kho` from silently replacing the
`140` total used by Quick Ratio.

`reporting_period_end` is important for issuers with a non-calendar fiscal
year. For example, a literal title stating “năm kết thúc ngày 30 tháng 9 năm
2022” records `30/09/2022`; it is not defaulted to `31/12`. A year-specific
stage can therefore select that documented fiscal year-end while rejecting a
separate `01/10/2021` opening balance or a quarterly date.

If a primary-statement column is labeled only `2022`, it can be used only when
the same source document has already supplied an explicit fiscal period end
for 2022. A competing explicit date in the header is still rejected. This
admits annual layouts such as `2022 Nghìn VND` without permitting an interim
or opening-balance column to inherit a date by guesswork.

## Metric registry

| Metric | Required variables | Table type |
| --- | --- | --- |
| `quick_ratio` | `current_assets`, `inventory`, `current_liabilities` | `balance_sheet` |
| `net_profit_margin` | `net_income`, `net_revenue` | `income_statement` |
| `gross_profit_margin` | `gross_profit`, `net_revenue` | `income_statement` |
| `interest_coverage` | `profit_before_tax`, `interest_expense` | income statement or notes |

The same metric can bind operands from separate source tables, but every
operand must be present for every requested company and must later pass the
ordinary exact row/header/period/scope/unit gate.

Before an LLM or calculator sees the operands, its review packet also requires
one resolved report scope and one resolved source `document_id` per company.
When a 2022 report and a 2023 comparative report both expose 2022, the
target-year document is preferred; a mixed-document packet is rejected.

`HybridRetriever.retrieve(..., allowed_uids=..., apply_plan_metadata_filters=False)`
uses the routed UID allow-list for lexical and dense ranking. This is the only
mode that may rank a comparative table whose document year differs from the
question year: the stage catalog first proves that the requested year occurs
in a canonical V3 header. It still does not make that header an evidence
binding.

## Multi-stage example

For the question containing Quick Ratio, a median filter and Net Profit
Margin, the route plan is:

```text
Stage 1: quick_ratio
  -> balance_sheet
  -> HPG/HSG/MSR/NKG, 2022
  -> current_assets + inventory + current_liabilities
  -> deterministic median filter

Stage 2: net_profit_margin
  -> income_statement
  -> only @eligible_entities from Stage 1
  -> net_income + net_revenue
  -> deterministic average
```

No candidate is a valid result.  The materializer emits a feedback record,
for example `required_variables_missing_from_eligible_source_tables`, including
the missing variable per company.  It never fabricates an operand, expands a
year by guesswork, or lets an LLM choose a fallback table.

## Two-year selection example

The same routing contract also supports the longer Quick Ratio / Gross Profit
Margin / Interest Coverage question.  It is deliberately not one semantic
search over every table:

```text
Stage 1: quick_ratio_screen (2022)
  -> balance_sheet for HPG/HSG/MSR/NKG
  -> median; retain only entities strictly below it

Stage 2: gross_profit_margin_old (2022)
  -> income_statement only for @eligible_entities

Stage 3: gross_profit_margin_new (2023)
  -> income_statement only for the same @eligible_entities

Stage 4: gross_profit_margin_change_rank
  -> deterministic GPM(2023) - GPM(2022)
  -> require exactly one largest signed change

Stage 5: interest_coverage_lookup (2023)
  -> income_statement or notes for @winning_entity only
  -> (profit_before_tax + abs(interest_expense)) / abs(interest_expense)
```

Stages 1, 2, 3, and 5 require a complete source-backed packet. Stage 4 has no
retrieval and does not consult the LLM. If a stage lacks a valid candidate,
has multiple valid source cells for the same required binding, changes scope,
or ties for the winner, the route stops with `needs_human` and structured
feedback instead of inferring the missing fact.

## Commands

```bash
rtk .venv/bin/python scripts/build_report_normalization.py \
  --bundle-dir /home/dungle/ViFinQA_review/run_full_metadata_support_v1 --force

rtk .venv/bin/python scripts/build_staged_retrieval_routes.py \
  --questions /home/dungle/ViFinQA_review/run_full_metadata_support_v1/review_items.jsonl \
  --output /home/dungle/ViFinQA_review/evaluation_metadata_support_v1/staged_retrieval_routes_v1.jsonl

rtk .venv/bin/python scripts/materialize_staged_route_candidates.py \
  --catalog /home/dungle/ViFinQA_review/run_full_metadata_support_v1/table_routing_catalog_v1.jsonl \
  --routes /home/dungle/ViFinQA_review/evaluation_metadata_support_v1/staged_retrieval_routes_v1.jsonl \
  --output /home/dungle/ViFinQA_review/evaluation_metadata_support_v1/staged_route_candidates_v1.jsonl
```

The first two sidecars are navigation metadata.  The candidate materialization
is a routing audit.  None is evidence, a training label or a submission answer.
