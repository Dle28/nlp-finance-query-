# Architecture

## 1. Objective

Build a Vietnamese financial question-answering pipeline that can:

1. parse a natural-language question;
2. retrieve the correct financial report;
3. retrieve and bind the correct table, rows, and columns;
4. create an explicit execution plan;
5. generate an executable Pandas query;
6. execute it in a restricted runtime;
7. validate and package the result.

The system must be auditable. A wrong answer must be attributable to one pipeline stage rather than hidden inside one end-to-end generation step.

## 2. Data reality

The public ViFinQA release contains:

- 1,012 questions;
- OCR financial reports;
- stock/company metadata.

The public `questions.jsonl` contains only `id` and `question`. It does not contain public gold values for:

- `relevant_docs`;
- `relevant_tables`;
- `evidence`;
- `pandas_query`;
- `answer`.

Consequences:

- table-ID origin cannot be proven from the public question file alone;
- document/table retrieval cannot be scored against public gold labels;
- a manually verified development set is required;
- table IDs must not be treated as semantic classes.

## 3. Core design

```text
Question
   |
   v
Question parser
   |
   v
Document candidate generator
   |
   v
Document retriever
   |
   v
Table extractor and index
   |
   v
Table retriever and reranker
   |
   v
Row/column binder
   |
   v
Execution planner
   |
   v
Pandas query generator
   |
   v
Restricted executor
   |
   v
Validator
   |
   v
Submission builder
```

Each stage receives a typed record and produces a typed record. Raw strings and normalized forms are stored separately.

## 4. Raw-data contract

Expected local dataset path:

```text
data/ViFinQA/
├── code_stock.csv
├── questions/
│   └── questions.jsonl
└── financial_statements/
    └── {ticker}/{year}/{document}/..._extracted.txt
```

Downloaded data is local-only and ignored by Git.

OCR report text may contain:

```text
===== PAGE 1 =====
...
<table>...</table>
```

The original extracted text must be treated as immutable evidence.

## 5. Table identity and offset integrity

A reference may look like:

```text
VJC_financial_statements_2018_separate|350
```

The full key is:

```text
(document_id, table_id)
```

A single inspected report showed that `350` matched the 0-based character offset of an opening `<table>` tag. This is a hypothesis until verified on labeled records.

The audit script tests table IDs against:

- local table order, 0-based and 1-based;
- character offset, 0-based and 1-based;
- UTF-8 byte offset, 0-based and 1-based;
- line number;
- page number;
- order within a page;
- deterministic global order.

Lock criteria:

```text
exact_match_rate  >= 99.9%
unique_match_rate >= 99.5%
within-document collision rate == 0%
```

If character offset is verified, calculate it before any normalization. The following operations can invalidate IDs:

- trimming;
- newline conversion;
- whitespace normalization;
- OCR correction;
- HTML reserialization;
- deletion of blank lines.

Store two representations:

```text
raw_document_text      immutable, used for identity and evidence
normalized_table_text  derived copy, used for retrieval and matching
```

The model retrieves a table candidate. A deterministic mapping layer emits the table ID. The model does not predict the raw integer.

## 6. Canonical records

### 6.1 QuestionIR

```json
{
  "id": 1,
  "original_question": "...",
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

Deterministic rules should handle fields with stable patterns:

- ticker;
- year/date;
- units;
- `công ty mẹ` -> separate;
- `hợp nhất` -> consolidated.

A language model is reserved for contextual fields:

- financial metric;
- qualifiers;
- ambiguous operation;
- likely statement family.

### 6.2 DocumentRecord

```json
{
  "document_id": "VJC_financial_statements_2018_separate",
  "ticker": "VJC",
  "year": 2018,
  "scope": "separate",
  "path": "data/ViFinQA/financial_statements/..."
}
```

### 6.3 TableRecord

```json
{
  "document_id": "VJC_financial_statements_2018_separate",
  "table_id": 350,
  "page": 2,
  "local_order_1": 2,
  "start_char_0": 350,
  "start_byte_0": 400,
  "raw_html": "<table>...</table>",
  "caption_raw": "...",
  "caption_normalized": "...",
  "unit": "million_vnd",
  "header_text": "...",
  "row_text": "...",
  "csv_path": "data/generated/...csv"
}
```

`table_id` remains nullable until its mapping rule is verified.

### 6.4 BindingPlan

```json
{
  "operation": "direct_lookup",
  "inputs": [
    {
      "table_ref": "VJC_financial_statements_2018_separate|350",
      "row_label": "Lãi tiền gửi",
      "column_label": "2018"
    }
  ],
  "source_unit": "million_vnd",
  "target_unit": "million_vnd"
}
```

### 6.5 Evidence record

```json
{
  "variable": "df1",
  "csv_path": "data/generated/question_1_table_1.csv"
}
```

`df1` is a local alias for the first DataFrame used by one question. It is not a table ID and has no fixed financial meaning.

With multiple tables:

```text
df1 -> first evidence table
df2 -> second evidence table
```

The alias builder must be deterministic and sequential.

## 7. Pipeline stages

### 7.1 Download and corpus audit

Current files:

```text
data/process/extract_data.py
data/process/audit_dataset.py
```

Responsibilities:

- download the public corpus;
- validate paths;
- count reports, pages, and tables;
- generate table inventories;
- analyze question fields;
- test table-ID hypotheses when labels exist.

### 7.2 Question parsing

Inputs:

```text
question text
code_stock.csv
```

Outputs:

```text
QuestionIR
```

Evaluation:

- ticker exact match;
- year exact match;
- scope accuracy;
- unit accuracy;
- metric span F1;
- qualifier span F1;
- operation accuracy.

### 7.3 Document retrieval

Apply hard constraints first:

```text
ticker + year + scope
```

Do not search all 1,973 reports for every question when deterministic metadata reduces the candidate set.

If scope is not stated, preserve separate and consolidated candidates rather than guessing.

Evaluation:

```text
Recall@1
Recall@3
MRR
```

### 7.4 Table extraction

For every report:

1. find `<table>...</table>` spans on raw text;
2. record offsets before normalization;
3. resolve page markers;
4. parse HTML into a rectangular representation;
5. preserve raw cells;
6. create normalized cells for retrieval;
7. detect caption and nearby context;
8. detect unit;
9. export CSV;
10. create `TableRecord` entries.

Malformed OCR tables must be logged rather than silently discarded.

### 7.5 Table retrieval

Candidate features:

- metric text;
- full target text;
- qualifiers;
- caption;
- nearby text;
- headers;
- row labels;
- statement family;
- unit.

Recommended retrieval stack:

```text
hard document filtering
    -> lexical retrieval
    -> embedding retrieval
    -> union of candidates
    -> cross-encoder reranking
    -> rule-based consistency checks
```

Because retrieval scoring may emphasize recall, retain top-k candidates until validation.

### 7.6 Row and column binding

Row matching combines:

- normalized exact matching;
- financial aliases;
- fuzzy similarity;
- semantic similarity;
- qualifier agreement;
- accounting-code hints when present.

Column matching resolves:

```text
năm nay
năm trước
đầu năm
cuối năm
31/12/YYYY
01/01/YYYY
```

Binding must use report year and question semantics, not only surface column text.

### 7.7 Execution planning

Generate a structured plan before generating code.

Supported operations:

- direct lookup;
- absolute difference;
- signed difference;
- growth rate;
- ratio;
- sum;
- average;
- minimum;
- maximum;
- comparison.

The planner explicitly records unit conversion.

### 7.8 Pandas generation

Prefer deterministic templates.

Example:

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

Requirements:

- use declared evidence variables;
- use real headers and row labels;
- check that filtered rows are non-empty;
- parse Vietnamese number formats explicitly;
- encode unit conversion in the query;
- return one numeric scalar;
- never hard-code the final answer.

### 7.9 Restricted execution

The executor exposes only:

- approved DataFrames;
- approved Pandas operations;
- numeric and collection builtins required by templates.

Reject:

- imports;
- file access outside declared evidence;
- network access;
- system calls;
- subprocesses;
- unrestricted `eval`;
- infinite or NaN results;
- non-scalar outputs.

### 7.10 Validation

Validate before accepting a record:

- ticker matches document;
- year matches document;
- scope is consistent;
- table reference resolves uniquely;
- CSV exists;
- evidence variables are unique;
- every query variable is declared;
- query executes;
- result is numeric and finite;
- requested unit is satisfied;
- answer is not hard-coded.

## 8. Planned package structure

```text
src/finance_query/
├── schemas/
│   ├── question.py
│   ├── document.py
│   ├── table.py
│   ├── binding.py
│   └── result.py
├── questions/
│   ├── normalizer.py
│   ├── rules.py
│   └── parser.py
├── reports/
│   ├── indexer.py
│   └── retriever.py
├── tables/
│   ├── extractor.py
│   ├── normalizer.py
│   ├── indexer.py
│   └── retriever.py
├── binding/
│   ├── row_matcher.py
│   └── column_matcher.py
├── execution/
│   ├── planner.py
│   ├── pandas_generator.py
│   ├── sandbox.py
│   └── validator.py
└── submission/
    ├── builder.py
    └── packager.py
```

## 9. Evaluation strategy

Evaluate every stage independently.

### Corpus integrity

- report discovery rate;
- page-marker coverage;
- table extraction count;
- malformed-table rate;
- duplicate document IDs;
- table-key collision rate.

### Retrieval

- document Recall@k;
- table Recall@k;
- MRR;
- reranker accuracy.

### Binding

- row exact match;
- row top-k recall;
- column exact match;
- unit accuracy.

### Execution

- plan validity rate;
- query execution rate;
- scalar-result rate;
- numeric accuracy;
- unit-conversion accuracy.

### End to end

- answer accuracy;
- document retrieval score;
- table retrieval score;
- submission-schema validity.

## 10. Development sequence

1. Download and audit the corpus.
2. Build 30-50 manually verified development questions.
3. Verify the table-ID mapping rule.
4. Freeze `DocumentRecord` and `TableRecord` schemas.
5. Implement deterministic question fields.
6. Implement document retrieval.
7. Implement table extraction and indexing.
8. Establish lexical retrieval baseline.
9. Add embeddings and reranking.
10. Implement row/column binding.
11. Implement execution templates.
12. Add restricted execution and validation.
13. Complete one question end to end.
14. Expand the development set by failure category.
15. Build the final submission pipeline.
