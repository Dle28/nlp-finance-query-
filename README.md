# Vietnamese Financial Question-to-Pandas System

This repository is an early implementation of a system for the **ViFinQA Financial Table Retrieval and Text-to-Pandas challenge**.

The final system should read a Vietnamese financial question, find the correct financial report and table, generate an executable Pandas query, run the query, and package the result in the required JSON format.

> Current status: the repository can download the ViFinQA dataset and build a basic report index. The question parser, table extractor, retriever, Pandas generator, validator, and submission builder are still planned work.

## 1. The problem in simple words

A question may look like this:

```text
Lãi tiền gửi năm 2018 của công ty mẹ CTCP Hàng không Vietjet (VJC)
là bao nhiêu triệu đồng?
```

The system must understand:

- the company is `VJC`;
- the requested year is `2018`;
- `công ty mẹ` means a separate or parent-company report;
- the financial item is `Lãi tiền gửi`;
- the requested output unit is `triệu đồng`;
- the answer is probably a direct value lookup, not a complex formula.

The system then finds the correct report, finds the correct table and row, generates Pandas code, executes it, and returns a verifiable result.

## 2. Required final output

A submission record has this form:

```json
{
  "id": 1,
  "question": "Doanh thu thuần của Công ty CP Sữa Việt Nam (VNM) năm 2023 là bao nhiêu?",
  "answer": 63075000000.0,
  "relevant_docs": [
    "VNM_financial_statements_2023_consolidated"
  ],
  "relevant_tables": [
    "VNM_financial_statements_2023_consolidated|350"
  ],
  "evidence": [
    {
      "variable": "df1",
      "csv_path": "data/VNM_financial_statements_2023_consolidated_table_350.csv"
    }
  ],
  "pandas_query": "float(df1.loc[df1['item'] == 'Doanh thu thuần', '2023'].iloc[0])"
}
```

The values above are only an example. A real submission must use document IDs, table positions, CSV files, columns, rows, units, and values that actually exist in the dataset.

## 3. How to understand the output

The easiest way to design the system is to work backward from the required JSON.

### `id`

Copied directly from the input question. No model is needed.

### `question`

Copied exactly from the input data. It should not be rewritten.

### `relevant_docs`

The report or reports that contain the required information.

To produce this field, the system must identify:

- stock ticker;
- company name;
- report year;
- report scope: separate, consolidated, aggregated, or unknown.

Example:

```text
VJC + 2018 + công ty mẹ
```

should strongly prefer a report whose ID contains:

```text
VJC_financial_statements_2018_separate
```

### `relevant_tables`

The exact table or tables that contain the values used to answer the question.

Each value uses this format:

```text
<document_id>|<table_position>
```

The table retriever must understand the financial item and its extra conditions. For example:

```text
Số dư cho vay khách hàng ngành Thương mại
```

contains:

- main metric: `cho vay khách hàng`;
- filter: `ngành Thương mại`.

Finding only the general loan table is not enough. The system must also select the correct row inside that table.

### `evidence`

The CSV tables actually loaded during execution.

```json
{
  "variable": "df1",
  "csv_path": "data/table.csv"
}
```

The variable name must be a valid Python variable and must appear in `pandas_query`.

### `pandas_query`

Executable Pandas code that reads values from the evidence DataFrames and produces the answer.

The query should not contain a hard-coded answer.

Bad:

```python
63075000000.0
```

Better:

```python
float(df1.loc[df1["item"] == "Doanh thu thuần", "2023"].iloc[0])
```

The exact query depends on the real normalized CSV schema.

### `answer`

The scalar result returned after executing `pandas_query`.

The answer must have the requested unit. If a table is in million VND and the question asks for billion VND, the conversion must appear in the Pandas query.

## 4. Main design principle

Do not ask one model to directly invent the final JSON.

Use a pipeline where each component has one clear responsibility:

```text
Question
   |
   v
Question parsing
   |
   v
Document retrieval
   |
   v
Table retrieval
   |
   v
Row and column matching
   |
   v
Execution plan
   |
   v
Pandas query generation
   |
   v
Safe execution and validation
   |
   v
Submission JSON
```

This design is easier to debug because every wrong answer can be traced to one stage.

## 5. Proposed architecture

### Stage 1: Data download

Download:

- `questions/questions.jsonl`;
- `code_stock.csv`;
- all OCR financial reports.

Current implementation:

```text
data/extract_data.py
```

### Stage 2: Report index

Create one structured row for every report:

```text
ticker | company_name | year | document_id | statement_type | path
```

Current implementation:

```text
data/build_report_index.py
```

This index is used for document retrieval. The system should not scan all report files for every question.

### Stage 3: Question parser

Convert each natural-language question into an intermediate JSON object called `QuestionIR`.

Example:

```json
{
  "id": 1,
  "ticker": "VJC",
  "company_name": "CTCP Hàng không Vietjet",
  "report_year": 2018,
  "report_scope": "separate",
  "metric_head": "Lãi tiền gửi",
  "target_full": "Lãi tiền gửi",
  "qualifiers": [],
  "time_anchor": "period",
  "requested_unit": "million_vnd",
  "operation": "direct_lookup"
}
```

Use simple deterministic rules for fields that have clear patterns:

- ticker;
- year and date;
- units;
- phrases such as `công ty mẹ` and `hợp nhất`.

Use a Vietnamese language model such as PhoBERT for contextual fields:

- main financial metric;
- full target phrase;
- person, related company, asset, project, or filter;
- operation type when wording is ambiguous;
- possible statement families.

This is a hybrid parser: rules handle easy exact fields, while the model handles language variation.

### Stage 4: Table extraction and normalization

The reports are OCR text files and may contain tables represented with inline HTML.

This stage should:

1. find every table in each report;
2. record the document ID, page, and table position;
3. convert the table to CSV;
4. keep the original cell text;
5. create normalized headers and row labels;
6. detect the table unit;
7. save a table index.

Suggested table index fields:

```text
document_id
report_ticker
report_year
report_scope
table_position
page
caption
unit
header_text
row_text
csv_path
```

Do not delete the raw OCR value. Keep both raw and normalized values so errors can be audited.

### Stage 5: Document retrieval

Use hard constraints first:

```text
ticker + year + report scope
```

For example:

```text
VJC + 2018 + separate
```

reduces 1,973 reports to a very small candidate set.

If the question does not specify separate or consolidated, keep both as candidates instead of making an unsupported assumption.

### Stage 6: Table retrieval

Search only inside candidate reports.

Use information from:

- metric text;
- full target text;
- table caption;
- row labels;
- headers;
- units;
- nearby report text.

A practical first version can combine:

- exact keyword matching;
- normalized text matching;
- BM25;
- embedding similarity;
- a reranker for the top candidates.

Because retrieval is evaluated with F2, missing the correct table is expensive. Keep a small top-k candidate set before final validation instead of selecting only one table too early.

### Stage 7: Row and column binding

After selecting a table, bind question concepts to real table locations.

Example:

```text
Question metric: doanh thu thuần
Table row: Doanh thu thuần về bán hàng và cung cấp dịch vụ
```

The row matcher should combine:

- normalized exact match;
- financial aliases;
- fuzzy matching;
- semantic similarity;
- qualifier matching.

The column matcher must understand:

```text
Năm nay
Năm trước
Cuối năm
Đầu năm
31/12/2023
01/01/2023
```

It must use the report year and the meaning of the question, not only the visible column name.

### Stage 8: Execution plan

Before generating code, create a structured plan.

Example:

```json
{
  "operation": "direct_lookup",
  "inputs": [
    {
      "dataframe": "df1",
      "row_label": "Doanh thu thuần",
      "column_label": "2023"
    }
  ],
  "source_unit": "million_vnd",
  "target_unit": "billion_vnd"
}
```

The plan is easier to inspect and validate than free-form code.

### Stage 9: Pandas query generation

Generate code from safe templates whenever possible.

Common operation templates:

```text
direct lookup
difference
growth rate
sum
average
ratio
maximum
minimum
comparison
```

Example conversion:

```python
float(df1.loc[df1["item"] == "Doanh thu thuần", "2023"].iloc[0]) / 1000
```

This means the source table is in million VND and the requested answer is in billion VND.

A language model may propose an execution plan, but the final Pandas code should preferably be created by deterministic templates.

### Stage 10: Safe execution

Do not run arbitrary generated Python with unrestricted `eval`.

The execution layer should:

- expose only approved DataFrames and approved Pandas operations;
- reject imports, file access, network access, and system calls;
- enforce a timeout;
- verify that the result is one numeric scalar;
- reject `NaN` and infinity;
- log execution errors.

### Stage 11: Validation

Before accepting a result, check:

- document ticker matches the question;
- document year matches the question;
- report scope is consistent;
- selected table exists;
- evidence CSV exists;
- all DataFrame variables are declared;
- variables appear in the query;
- query executes successfully;
- result is numeric;
- unit conversion is correct;
- no answer value is hard-coded.

### Stage 12: Submission builder

The final component combines:

- original question;
- selected documents;
- selected tables;
- copied evidence CSVs;
- executable Pandas query;
- execution result.

It then creates:

```text
submission.zip
├── submission.json
└── data/
    ├── table_1.csv
    └── table_2.csv
```

## 6. Proposed repository structure

```text
nlp-finance-query-/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .gitignore
├── configs/
│   ├── parser.yaml
│   ├── retrieval.yaml
│   └── execution.yaml
├── data/
│   ├── extract_data.py
│   ├── build_report_index.py
│   ├── raw/                  # ignored by Git
│   ├── processed/            # ignored or selectively tracked
│   └── samples/
├── documents_contest/
│   └── ViFinQA_competition_rules.md
├── src/
│   └── finance_query/
│       ├── question_parser/
│       │   ├── rules.py
│       │   ├── normalizer.py
│       │   ├── phobert_parser.py
│       │   └── schema.py
│       ├── reports/
│       │   ├── indexer.py
│       │   └── retriever.py
│       ├── tables/
│       │   ├── extractor.py
│       │   ├── normalizer.py
│       │   ├── indexer.py
│       │   └── retriever.py
│       ├── binding/
│       │   ├── row_matcher.py
│       │   └── column_matcher.py
│       ├── execution/
│       │   ├── planner.py
│       │   ├── pandas_generator.py
│       │   ├── sandbox.py
│       │   └── validator.py
│       └── submission/
│           ├── builder.py
│           └── packager.py
├── scripts/
│   ├── preprocess_reports.py
│   ├── parse_questions.py
│   ├── retrieve_tables.py
│   ├── run_pipeline.py
│   └── build_submission.py
└── tests/
    ├── test_question_parser.py
    ├── test_table_extractor.py
    ├── test_row_column_binding.py
    ├── test_pandas_execution.py
    └── test_submission_schema.py
```

## 7. Current repository files

### `data/extract_data.py`

Downloads questions, company mappings, and financial statements from:

```text
AIGuruTinix/ViFinQA
```

### `data/build_report_index.py`

Reads the downloaded report paths and creates:

```text
data/processed/report_index.csv
```

The index currently stores:

- ticker;
- company name;
- year;
- document ID;
- statement type;
- relative path.

### `documents_contest/ViFinQA_competition_rules.md`

Contains the competition rules and output requirements used by this project.

## 8. Installation

Python 3.11 or 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install pandas huggingface_hub
```

Do not commit `.venv/` to Git. Every developer should create their own local environment.

## 9. Download the dataset

```bash
python data/extract_data.py
```

Expected local data structure:

```text
data/ViFinQA/
├── code_stock.csv
├── questions/
│   └── questions.jsonl
└── financial_statements/
```

The full downloaded dataset should normally remain outside Git history. Keep download scripts and small samples in the repository instead.

## 10. Build the report index

```bash
python data/build_report_index.py
```

Expected output:

```text
data/processed/report_index.csv
```

## 11. Development order

Build one complete question before attempting all 1,012 questions.

Recommended order:

1. Clean repository files and add `.gitignore`.
2. Download data locally.
3. Build and validate the report index.
4. Extract all tables and create a table index.
5. Define the `QuestionIR` JSON schema.
6. Implement the rule-based parser.
7. Manually annotate a small parser development set.
8. Add PhoBERT only for fields that rules cannot reliably extract.
9. Implement document and table retrieval.
10. Implement row and column binding.
11. Generate Pandas code from templates.
12. Execute and validate one question end to end.
13. Expand to a diverse development set.
14. Process the complete question set.
15. Build and validate the final ZIP.

## 12. Evaluation

Evaluate each stage separately.

### Question parsing

Measure exact match or field accuracy for:

```text
ticker
year
scope
metric
qualifiers
time anchor
unit
operation
```

### Document retrieval

Measure whether the correct report appears in the top candidates.

### Table retrieval

Measure Precision, Recall, and F2. Retrieval Recall@k should be checked before evaluating a reranker.

### Execution

Measure:

- query execution success;
- answer accuracy;
- unit conversion accuracy;
- full-record validity.

A final score alone is not enough. Stage-level metrics are needed to identify the real source of errors.

## 13. Important limitations

- The public dataset does not provide gold answers, gold evidence, or gold Pandas queries.
- OCR may corrupt Vietnamese text, numbers, rows, columns, and reading order.
- The same metric can appear in multiple tables.
- Report scope may be missing or ambiguous.
- Financial terms can be open-ended, especially names of people, projects, subsidiaries, partners, and assets.
- A correct parser does not guarantee a correct table.
- A correct table does not guarantee the correct row, column, period, or unit.
- Generated code must be treated as untrusted until validated.

## 14. Model restrictions

According to the competition rules stored in this repository, the official pipeline may use open models that:

- were released before 1 June 2026;
- contain no more than 14B parameters;
- can be downloaded and reproduced.

Closed models should not be part of the official competition pipeline.

## 15. Core engineering rule

Every final answer must be traceable:

```text
question
→ parsed meaning
→ report
→ table
→ row and column
→ Pandas query
→ numeric result
```

The system should never return a number that cannot be reproduced from the submitted evidence CSV files.
