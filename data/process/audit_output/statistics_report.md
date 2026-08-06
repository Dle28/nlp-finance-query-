# ViFinQA Detailed Dataset Statistics

## Dataset summary

- ticker_count: **100**
- year_count: **11**
- document_count: **1,973**
- separate_document_count: **942**
- consolidated_document_count: **945**
- unknown_scope_document_count: **86**
- total_pages: **121,756**
- total_html_tables: **146,246**
- question_count: **1,012**
- relevant_table_reference_count: **0**
- anomaly_count: **0**

## Table-ID hypothesis ranking

| Rank | Hypothesis | Exact match | Unique match |
|---:|---|---:|---:|
| 1 | local_order_0 | 0.0000% | 0.0000% |
| 2 | local_order_1 | 0.0000% | 0.0000% |
| 3 | start_char_0 | 0.0000% | 0.0000% |
| 4 | start_char_1 | 0.0000% | 0.0000% |
| 5 | start_byte_0 | 0.0000% | 0.0000% |
| 6 | start_byte_1 | 0.0000% | 0.0000% |
| 7 | start_line_0 | 0.0000% | 0.0000% |
| 8 | start_line_1 | 0.0000% | 0.0000% |
| 9 | page_no | 0.0000% | 0.0000% |
| 10 | page_order_0 | 0.0000% | 0.0000% |
| 11 | page_order_1 | 0.0000% | 0.0000% |
| 12 | global_order_0 | 0.0000% | 0.0000% |
| 13 | global_order_1 | 0.0000% | 0.0000% |

## Automated conclusion

No hypothesis passed the lock threshold. Best candidate is **local_order_0** with 0.0000% exact and 0.0000% unique matching.

## Model architecture implication

The model should retrieve/segment a table candidate, not predict the raw numeric ID.
A deterministic mapping layer must emit the table ID using the verified preprocessing rule.
If `start_char_0` wins, compute it on the unmodified extracted text.

## DataFrame variable implication

`df1`, `df2`, ... are local aliases from evidence metadata.
`df` is the one-table alias in the current runtime.
`dfs` is the multi-table dictionary keyed by table reference.
`result` is the required final scalar in the current runtime.