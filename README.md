# Vietnamese Financial Question-to-Pandas System

This repository develops a reproducible pipeline for the ViFinQA financial-table retrieval and Text-to-Pandas task.

The target workflow is:

```text
Vietnamese question
    -> question parsing
    -> document retrieval
    -> table extraction and retrieval
    -> row/column binding
    -> execution plan
    -> Pandas query generation
    -> safe execution
    -> validated submission record
```

## Current status

Implemented:

- download of the public `AIGuruTinix/ViFinQA` dataset;
- corpus inspection and report discovery;
- extraction of HTML-table spans from OCR text;
- document, page, table, character-offset, byte-offset, and line statistics;
- table-ID hypothesis auditing when an enriched question file contains `relevant_tables` labels.

Not implemented yet:

- production question parser;
- document and table retrievers;
- row/column binding;
- deterministic execution planner;
- Pandas query generator and sandbox;
- final submission builder.

## Important dataset limitation

The public `questions/questions.jsonl` file contains only:

```json
{"id": 1, "question": "..."}
```

It does not provide gold values for:

```text
relevant_docs
relevant_tables
evidence
pandas_query
answer
```

Therefore, the numeric origin of a table reference such as:

```text
VJC_financial_statements_2018_separate|350
```

cannot be verified across the full public question set. A labeled development file is required for a dataset-level conclusion.

A single inspected report showed that `350` matched the 0-based character offset of the opening `<table>` tag. This is a strong hypothesis, not yet a corpus-wide fact.

The model should never learn to predict the raw integer ID directly. It should retrieve or segment the correct table, then a deterministic mapping layer should emit the verified ID.

## Repository layout

```text
nlp-finance-query-/
├── README.md
├── ARCHITECTURE.md
├── data/
│   └── process/
│       ├── extract_data.py
│       └── audit_dataset.py
└── documents_contest/
    └── ViFinQA_competition_rules.md
```

Downloaded data and generated audit outputs are intentionally excluded from Git.

## Installation

Python 3.11 or 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install huggingface_hub pandas
```

## Download the dataset

From the repository root:

```bash
python data/process/extract_data.py
```

Default destination:

```text
data/ViFinQA/
├── code_stock.csv
├── questions/
│   └── questions.jsonl
└── financial_statements/
```

An alternative destination can be supplied explicitly:

```bash
python data/process/extract_data.py \
  --output-dir /absolute/path/to/ViFinQA
```

## Audit the corpus

```bash
python data/process/audit_dataset.py \
  --dataset-root data/ViFinQA/financial_statements \
  --questions data/ViFinQA/questions/questions.jsonl \
  --output-dir data/process/audit_output
```

Generated files:

```text
data/process/audit_output/
├── statistics_report.md
├── dataset_summary.csv
├── document_statistics.csv
├── table_inventory.csv
├── question_records.csv
├── question_statistics.csv
├── reference_audit.csv
├── table_id_hypothesis_summary.csv
└── anomalies.csv
```

With the public questions file, the report will explicitly state that table-ID hypotheses are not testable because there are zero `relevant_tables` references.

To test table-ID rules, pass an enriched JSONL file containing values such as:

```json
{
  "id": 1,
  "question": "...",
  "relevant_tables": [
    "VJC_financial_statements_2018_separate|350"
  ]
}
```

The audit compares the numeric suffix against:

- local table order, 0-based and 1-based;
- character offset, 0-based and 1-based;
- UTF-8 byte offset, 0-based and 1-based;
- source line number;
- page number;
- order within a page;
- deterministic global order.

Suggested lock criteria:

```text
exact_match_rate  >= 99.9%
unique_match_rate >= 99.5%
within-document collision rate == 0%
```

## Meaning of `df1`

`df1` is only a local alias for the first evidence DataFrame in one question.

```json
{
  "evidence": [
    {
      "variable": "df1",
      "csv_path": "data/generated/question_1_table_1.csv"
    }
  ]
}
```

Conceptually, the runtime performs:

```python
import pandas as pd

df1 = pd.read_csv(
    "data/generated/question_1_table_1.csv",
    dtype=str,
    keep_default_na=False,
)
```

`df1` is not a table ID, a financial label, or a global dataset variable. With multiple evidence tables, aliases may be `df1`, `df2`, and so on.

Example query:

```python
rows = df1[
    df1["Chỉ tiêu"].str.contains(
        "Doanh thu thuần",
        case=False,
        na=False,
    )
]
raw_value = rows["2023"].iloc[0]
answer = float(raw_value.replace(".", "").replace(",", "."))
```

The exact query must use the real extracted CSV schema. It must not hard-code the final answer.

## Data-integrity rule for offset-based IDs

If a labeled audit confirms `table_id == start_char_0`, calculate the ID on the original extracted text before any of the following:

- `.strip()`;
- whitespace normalization;
- newline conversion;
- OCR correction;
- HTML reformatting or reserialization;
- deletion of blank lines.

Changing one character before a table changes the offsets of that table and all later tables.

## Development order

1. Build a small manually verified development set.
2. Confirm the table-ID mapping rule.
3. Freeze extraction and table-index schemas.
4. Implement deterministic question fields: ticker, year, scope, and unit.
5. Add document retrieval.
6. Add table retrieval and reranking.
7. Add row/column binding.
8. Generate execution plans from templates.
9. Execute Pandas queries in a restricted runtime.
10. Validate and package final records.
