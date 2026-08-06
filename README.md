# Vietnamese Financial Question-to-Pandas System

This repository develops an auditable pipeline for ViFinQA financial-table retrieval and Text-to-Pandas execution.

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

- download of the public `AIGuruTinix/ViFinQA` corpus;
- centralized source-code path configuration;
- report, page, table, character-offset, byte-offset, and line statistics;
- table-ID hypothesis auditing when a labeled question file contains `relevant_tables`.

Not implemented yet:

- production question parser;
- document and table retrievers;
- row/column binding;
- execution planner;
- Pandas generator and sandbox;
- final submission builder.

## Direct path configuration

All processing paths are defined in one source file:

```text
data/process/project_paths.py
```

Default layout:

```text
<repository>/
└── data/
    ├── ViFinQA/
    │   ├── code_stock.csv
    │   ├── questions/
    │   │   └── questions.jsonl
    │   └── financial_statements/
    └── process/
        └── audit_output/
```

The key constants are:

```python
PROJECT_ROOT
VIFINQA_DIR
FINANCIAL_STATEMENTS_DIR
QUESTIONS_PATH
AUDIT_OUTPUT_DIR
```

The code also detects the former local path `data/data/ViFinQA` as a temporary fallback. New downloads always use `data/ViFinQA`.

Because paths are configured in source code, the scripts do not require path arguments.

## Installation

Python 3.11 or 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Download the dataset

Run from any working directory:

```bash
python data/process/extract_data.py
```

The downloader resolves the repository root from its own file location and writes to:

```text
data/ViFinQA/
```

## Audit the dataset

Run directly without CLI path arguments:

```bash
python data/process/audit_dataset.py
```

Output is written to:

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

To use an enriched labeled question file, change `QUESTIONS_PATH` or the resolution logic in:

```text
data/process/project_paths.py
```

## Public dataset limitation

The public `questions/questions.jsonl` contains records such as:

```json
{"id": 1, "question": "..."}
```

It does not provide public gold values for:

```text
relevant_docs
relevant_tables
evidence
pandas_query
answer
```

Therefore, the origin of a numeric reference such as:

```text
VJC_financial_statements_2018_separate|350
```

cannot be verified across the public question set alone.

A single inspected report showed that `350` matched the 0-based character offset of an opening `<table>` tag. That remains a hypothesis until verified on multiple labeled records.

The audit compares labeled IDs against:

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
collision_rate    == 0%
```

## Model responsibility

The model should not predict the raw numeric table ID.

Correct design:

```text
question
    -> retrieve/segment correct table candidate
    -> deterministic table-ID mapping
    -> convert table to DataFrame
    -> execute Pandas query
```

If `table_id == start_char_0` is verified, compute the offset on the original extracted text before:

- trimming;
- whitespace normalization;
- newline conversion;
- OCR correction;
- HTML reserialization;
- deletion of blank lines.

Changing one character before a table changes the offsets of that table and all later tables.

## Meaning of `df1`

`df1` is only a local alias for the first evidence DataFrame used by one question.

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

Conceptually:

```python
import pandas as pd

df1 = pd.read_csv(
    "data/generated/question_1_table_1.csv",
    dtype=str,
    keep_default_na=False,
)
```

`df1` is not:

- a table ID;
- a financial label;
- the first table in the complete corpus;
- a global variable shared across questions.

With multiple evidence tables, aliases are assigned sequentially as `df1`, `df2`, and so on.

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

The exact query must use the real extracted CSV schema and must not hard-code the final answer.

## Repository layout

```text
nlp-finance-query-/
├── README.md
├── ARCHITECTURE.md
├── requirements.txt
├── data/
│   ├── README.md
│   └── process/
│       ├── project_paths.py
│       ├── extract_data.py
│       └── audit_dataset.py
└── documents_contest/
    └── ViFinQA_competition_rules.md
```

Downloaded data and generated audits are ignored by Git.
