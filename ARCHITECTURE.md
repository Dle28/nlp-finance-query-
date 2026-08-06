# Architecture and Implementation Plan

## Vietnamese Financial Question → Table Retrieval → Pandas Query → Verified Answer

This document describes a complete architecture for the ViFinQA project in simple language. It explains:

- what the system must do;
- why the system should be built as several modules instead of one large model;
- where PhoBERT, RAG, retrieval, reranking, and Pandas execution are used;
- the proposed repository tree;
- the purpose of every important Python file;
- the input and output of each module;
- a practical development plan from a simple baseline to a strong competition system.

The main engineering rule is:

```text
Never return a number that cannot be reproduced from the submitted CSV evidence.
```

---

# 1. Problem definition

The input is a Vietnamese financial question.

Example:

```text
Lãi tiền gửi năm 2018 của công ty mẹ CTCP Hàng không Vietjet (VJC)
là bao nhiêu triệu đồng?
```

The final output must contain:

```json
{
  "id": 1,
  "question": "Lãi tiền gửi năm 2018 của công ty mẹ CTCP Hàng không Vietjet (VJC) là bao nhiêu triệu đồng?",
  "answer": 0.0,
  "relevant_docs": [
    "VJC_financial_statements_2018_separate"
  ],
  "relevant_tables": [
    "VJC_financial_statements_2018_separate|350"
  ],
  "evidence": [
    {
      "variable": "df1",
      "csv_path": "data/VJC_financial_statements_2018_separate_table_350.csv"
    }
  ],
  "pandas_query": "float(df1.loc[df1['item'] == 'Lãi tiền gửi', '2018'].iloc[0])"
}
```

The values above are only an example. The real system must use data that actually exists in the reports.

To produce this output, the system must solve several different problems:

1. Understand the question.
2. Find the correct company report.
3. Find the correct table.
4. Find the correct row and column.
5. Understand the requested time and unit.
6. Decide whether the answer is a direct lookup or a calculation.
7. Generate executable Pandas code.
8. Execute and validate the code.
9. Package the answer and evidence into the required JSON.

One model should not be responsible for all nine tasks.

---

# 2. Architecture summary

The recommended architecture is a **hybrid hierarchical RAG system**.

Hybrid means the system combines:

- deterministic rules;
- PhoBERT-based language understanding;
- lexical retrieval such as BM25;
- dense semantic retrieval;
- cross-encoder reranking;
- optional open LLM fallback;
- deterministic Pandas templates;
- execution-based verification.

Hierarchical means retrieval happens in levels:

```text
all reports
→ candidate reports
→ candidate tables
→ candidate rows
→ candidate columns
→ executable answer
```

RAG means the language model does not answer from memory. It receives retrieved report and table context and must stay grounded in that context.

The complete flow is:

```text
Question
   |
   v
Rule parser + PhoBERT semantic parser
   |
   v
QuestionIR
   |
   v
Report metadata filter
   |
   v
Candidate reports
   |
   v
Hybrid table retrieval
   |
   v
Cross-encoder reranker
   |
   v
Top tables
   |
   v
Row, column, time, and unit grounding
   |
   v
ExecutionPlan
   |
   v
Pandas query generator
   |
   v
Safe execution
   |
   v
Answer verifier
   |
   v
Submission JSON + evidence CSV files
```

---

# 3. Why this architecture

## 3.1 Why not use only rules?

Rules are excellent for structured information:

```text
(VJC)          → ticker
2018           → year
công ty mẹ     → separate report
triệu đồng     → requested unit
31/12/2023     → closing date
```

Rules are weak for open financial language:

```text
Giá trị còn lại của quyền sử dụng đất
Số dư cho vay khách hàng ngành Thương mại
Chi phí khấu hao TSCĐ thuộc chi phí quản lý doanh nghiệp
Khoản phải thu từ Bảo Việt Nhân thọ
```

There are too many possible financial terms, partner names, projects, people, assets, and table row descriptions. A rule-only system becomes difficult to maintain.

## 3.2 Why not use only PhoBERT?

PhoBERT is useful for understanding Vietnamese text, extracting a metric, and identifying qualifiers.

However, PhoBERT cannot by itself:

- know which report file exists;
- know the real table position;
- create an evidence CSV;
- guarantee a correct numeric value;
- safely execute Pandas;
- verify the final result.

PhoBERT should understand the question, not invent the final answer.

## 3.3 Why not use only an LLM?

An LLM can produce fluent JSON and code, but it can also invent:

- a document ID;
- a table ID;
- a column name;
- a row name;
- an answer;
- an evidence path.

The competition requires reproducible execution. Therefore, the LLM may help with ambiguous reasoning, but it must not be the source of truth.

## 3.4 Why use RAG?

The reports are the source of truth.

RAG gives the model only the most relevant tables and nearby context. This makes the task smaller:

```text
bad task:
answer from 1,973 reports

better task:
choose from five candidate tables

best task:
choose the correct row and column from one or two verified tables
```

## 3.5 Why use hybrid retrieval?

Financial questions contain both exact and semantic information.

Exact search is strong for:

```text
VJC
31/12/2018
Lãi tiền gửi
TCTD
HĐQT
```

Semantic search is strong when the wording differs:

```text
Question: doanh thu thuần
Table row: doanh thu thuần về bán hàng và cung cấp dịch vụ
```

A strong retriever combines both.

## 3.6 Why use an intermediate representation?

The system first converts the question into a structured object called `QuestionIR`.

This separates language understanding from retrieval.

Instead of passing the full Vietnamese sentence to every module, later modules receive:

```json
{
  "ticker": "VJC",
  "year": 2018,
  "scope": "separate",
  "metric": "lãi tiền gửi",
  "unit": "million_vnd",
  "operation": "direct_lookup"
}
```

This makes the system easier to test and debug.

## 3.7 Why generate an execution plan before Pandas code?

Free-form code is difficult to validate.

A structured plan is easier to inspect:

```json
{
  "operation": "direct_lookup",
  "dataframe": "df1",
  "row": "Lãi tiền gửi",
  "column": "2018",
  "source_unit": "million_vnd",
  "target_unit": "million_vnd"
}
```

The Pandas query is generated from this plan using templates.

---

# 4. Two major pipelines

The system has two different pipelines.

## 4.1 Offline indexing pipeline

This pipeline runs before answering questions.

It processes all reports once:

```text
download reports
→ build report index
→ extract tables
→ normalize tables
→ build lexical index
→ build dense vector index
→ save metadata
```

The purpose is to avoid scanning all raw reports for every question.

## 4.2 Online question-answering pipeline

This pipeline runs for each question:

```text
parse question
→ retrieve reports
→ retrieve tables
→ rerank
→ ground rows and columns
→ build execution plan
→ generate Pandas
→ execute
→ validate
→ build output
```

---

# 5. Core data structures

## 5.1 QuestionIR

`QuestionIR` is the normalized meaning of a question.

```json
{
  "id": 1,
  "question": "Lãi tiền gửi năm 2018 của công ty mẹ CTCP Hàng không Vietjet (VJC) là bao nhiêu triệu đồng?",
  "company": {
    "ticker": "VJC",
    "name": "CTCP Hàng không Vietjet",
    "scope": "separate"
  },
  "time": {
    "report_year": 2018,
    "anchor": "period",
    "date": null
  },
  "target": {
    "metric_head": "Lãi tiền gửi",
    "target_full": "Lãi tiền gửi",
    "qualifiers": []
  },
  "answer_spec": {
    "quantity_type": "money",
    "unit": "million_vnd"
  },
  "operation": {
    "type": "direct_lookup"
  },
  "retrieval_hints": {
    "statement_families": [
      "financial_income_note"
    ]
  },
  "confidence": {
    "ticker": 1.0,
    "year": 1.0,
    "scope": 1.0,
    "metric": 0.95,
    "operation": 0.98
  }
}
```

Important fields:

- `ticker`: stock code.
- `scope`: separate, consolidated, aggregated, or unspecified.
- `report_year`: report year.
- `anchor`: period, opening balance, closing balance, or exact date.
- `metric_head`: core financial concept.
- `target_full`: full phrase used for retrieval.
- `qualifiers`: sector, person, related company, asset, project, maturity, currency, or other filters.
- `unit`: requested output unit.
- `operation`: direct lookup, difference, growth, ratio, sum, average, comparison, maximum, or minimum.

## 5.2 ReportRecord

One record per report:

```json
{
  "ticker": "VJC",
  "company_name": "CTCP Hàng không Vietjet",
  "year": 2018,
  "document_id": "VJC_financial_statements_2018_separate",
  "statement_type": "separate",
  "relative_path": "VJC/2018/VJC_financial_statements_2018_separate/..."
}
```

## 5.3 TableRecord

One record per extracted table:

```json
{
  "document_id": "VJC_financial_statements_2018_separate",
  "ticker": "VJC",
  "year": 2018,
  "scope": "separate",
  "table_position": 350,
  "page": 72,
  "caption": "Doanh thu hoạt động tài chính",
  "unit": "million_vnd",
  "headers": [
    "Chỉ tiêu",
    "2018",
    "2017"
  ],
  "row_labels": [
    "Lãi tiền gửi",
    "Lãi chênh lệch tỷ giá"
  ],
  "context_before": "...",
  "context_after": "...",
  "csv_path": "data/processed/tables/...csv"
}
```

## 5.4 RetrievalCandidate

```json
{
  "document_id": "VJC_financial_statements_2018_separate",
  "table_position": 350,
  "lexical_score": 11.8,
  "dense_score": 0.84,
  "metadata_score": 1.0,
  "rerank_score": 0.93
}
```

## 5.5 ExecutionPlan

```json
{
  "operation": "direct_lookup",
  "inputs": [
    {
      "variable": "df1",
      "csv_path": "data/VJC_table_350.csv",
      "row_label": "Lãi tiền gửi",
      "column_label": "2018"
    }
  ],
  "source_unit": "million_vnd",
  "target_unit": "million_vnd",
  "conversion_factor": 1.0
}
```

## 5.6 SubmissionRecord

This is the final competition format:

```json
{
  "id": 1,
  "question": "...",
  "answer": 12345.0,
  "relevant_docs": ["..."],
  "relevant_tables": ["...|350"],
  "evidence": [
    {
      "variable": "df1",
      "csv_path": "data/table.csv"
    }
  ],
  "pandas_query": "..."
}
```

---

# 6. Proposed repository tree

```text
nlp-finance-query-/
├── README.md
├── ARCHITECTURE.md
├── .gitignore
├── requirements.txt
├── pyproject.toml
│
├── configs/
│   ├── paths.yaml
│   ├── parser.yaml
│   ├── retrieval.yaml
│   ├── reranker.yaml
│   ├── execution.yaml
│   └── logging.yaml
│
├── data/
│   ├── extract_data.py
│   ├── build_report_index.py
│   ├── samples/
│   │   ├── questions_sample.jsonl
│   │   └── report_sample.txt
│   ├── raw/                         # ignored by Git
│   ├── processed/                   # ignored by Git
│   │   ├── report_index.parquet
│   │   ├── table_index.parquet
│   │   ├── tables/
│   │   ├── lexical_index/
│   │   └── vector_index/
│   └── annotations/
│       ├── parser_train.jsonl
│       ├── parser_dev.jsonl
│       └── parser_test.jsonl
│
├── src/
│   └── finance_query/
│       ├── __init__.py
│       ├── config.py
│       ├── constants.py
│       │
│       ├── schemas/
│       │   ├── __init__.py
│       │   ├── question.py
│       │   ├── report.py
│       │   ├── table.py
│       │   ├── retrieval.py
│       │   ├── execution.py
│       │   └── submission.py
│       │
│       ├── common/
│       │   ├── io.py
│       │   ├── text.py
│       │   ├── numbers.py
│       │   ├── logging_utils.py
│       │   └── exceptions.py
│       │
│       ├── ingestion/
│       │   ├── question_loader.py
│       │   ├── stock_loader.py
│       │   ├── report_scanner.py
│       │   └── report_indexer.py
│       │
│       ├── tables/
│       │   ├── html_table_extractor.py
│       │   ├── table_cleaner.py
│       │   ├── header_resolver.py
│       │   ├── unit_detector.py
│       │   ├── table_normalizer.py
│       │   └── table_indexer.py
│       │
│       ├── parsing/
│       │   ├── normalizer.py
│       │   ├── rules.py
│       │   ├── company_resolver.py
│       │   ├── word_segmenter.py
│       │   ├── phobert_model.py
│       │   ├── phobert_parser.py
│       │   ├── operation_classifier.py
│       │   ├── confidence.py
│       │   └── parser.py
│       │
│       ├── retrieval/
│       │   ├── document_retriever.py
│       │   ├── lexical_retriever.py
│       │   ├── dense_retriever.py
│       │   ├── hybrid_retriever.py
│       │   ├── reranker.py
│       │   └── context_builder.py
│       │
│       ├── grounding/
│       │   ├── metric_aliases.py
│       │   ├── metric_matcher.py
│       │   ├── qualifier_matcher.py
│       │   ├── row_matcher.py
│       │   ├── column_matcher.py
│       │   ├── time_resolver.py
│       │   └── unit_resolver.py
│       │
│       ├── planning/
│       │   ├── financial_formulas.py
│       │   ├── plan_builder.py
│       │   ├── plan_validator.py
│       │   └── llm_fallback.py
│       │
│       ├── execution/
│       │   ├── query_templates.py
│       │   ├── pandas_generator.py
│       │   ├── safe_executor.py
│       │   ├── result_validator.py
│       │   └── repair.py
│       │
│       ├── verification/
│       │   ├── source_verifier.py
│       │   ├── consistency_checker.py
│       │   ├── answer_verifier.py
│       │   └── confidence_router.py
│       │
│       ├── pipeline/
│       │   ├── state.py
│       │   ├── single_question.py
│       │   ├── batch_runner.py
│       │   └── checkpoint_store.py
│       │
│       ├── submission/
│       │   ├── builder.py
│       │   ├── schema_validator.py
│       │   └── packager.py
│       │
│       ├── training/
│       │   ├── annotation_builder.py
│       │   ├── parser_dataset.py
│       │   ├── train_phobert.py
│       │   ├── train_reranker.py
│       │   └── evaluate_models.py
│       │
│       └── evaluation/
│           ├── parsing_metrics.py
│           ├── retrieval_metrics.py
│           ├── execution_metrics.py
│           ├── pipeline_metrics.py
│           └── error_analysis.py
│
├── scripts/
│   ├── 01_download_data.py
│   ├── 02_build_report_index.py
│   ├── 03_extract_tables.py
│   ├── 04_build_table_index.py
│   ├── 05_build_retrieval_indexes.py
│   ├── 06_parse_questions.py
│   ├── 07_run_retrieval.py
│   ├── 08_run_pipeline.py
│   ├── 09_validate_results.py
│   └── 10_build_submission.py
│
├── tests/
│   ├── unit/
│   │   ├── test_rules.py
│   │   ├── test_numbers.py
│   │   ├── test_unit_resolver.py
│   │   ├── test_column_matcher.py
│   │   └── test_query_templates.py
│   ├── integration/
│   │   ├── test_report_index.py
│   │   ├── test_table_extraction.py
│   │   ├── test_retrieval_pipeline.py
│   │   └── test_execution_pipeline.py
│   └── end_to_end/
│       └── test_sample_questions.py
│
├── models/                            # ignored by Git
├── logs/                              # ignored by Git
├── outputs/                           # ignored by Git
└── submissions/                       # ignored by Git
```

---

# 7. Detailed purpose of every important Python file

## 7.1 Configuration

### `src/finance_query/config.py`

Purpose:

- load YAML configuration;
- resolve project paths;
- validate required settings;
- expose one typed configuration object.

Main interface:

```python
def load_config(config_dir: Path) -> AppConfig:
    ...
```

It should not contain business logic.

### `src/finance_query/constants.py`

Purpose:

- store fixed names used across modules;
- define supported scopes, operations, units, and time anchors.

Example:

```python
REPORT_SCOPES = {
    "separate",
    "consolidated",
    "aggregated",
    "unspecified",
}

OPERATIONS = {
    "direct_lookup",
    "difference",
    "growth_rate",
    "sum",
    "average",
    "ratio",
    "maximum",
    "minimum",
    "comparison",
}
```

---

## 7.2 Schemas

Use Pydantic models or dataclasses. Schemas prevent modules from passing unstructured dictionaries with inconsistent keys.

### `schemas/question.py`

Defines:

- `CompanyConstraint`;
- `TimeConstraint`;
- `TargetSpec`;
- `AnswerSpec`;
- `QuestionIR`.

Main class:

```python
class QuestionIR(BaseModel):
    id: int
    question: str
    company: CompanyConstraint
    time: TimeConstraint
    target: TargetSpec
    answer_spec: AnswerSpec
    operation: OperationSpec
    confidence: dict[str, float]
```

### `schemas/report.py`

Defines the structure of one report metadata record.

```python
class ReportRecord(BaseModel):
    ticker: str
    company_name: str | None
    year: int
    document_id: str
    statement_type: str
    relative_path: str
```

### `schemas/table.py`

Defines:

- table metadata;
- table headers;
- normalized row labels;
- detected unit;
- evidence path.

### `schemas/retrieval.py`

Defines a retrieval candidate and score breakdown.

```python
class RetrievalCandidate(BaseModel):
    document_id: str
    table_position: int
    lexical_score: float
    dense_score: float
    metadata_score: float
    rerank_score: float | None
```

### `schemas/execution.py`

Defines `ExecutionInput`, `ExecutionPlan`, and `ExecutionResult`.

### `schemas/submission.py`

Defines the exact competition output and validates field types.

---

## 7.3 Common utilities

### `common/io.py`

Purpose:

- read and write JSON;
- read and write JSONL;
- read Parquet and CSV safely;
- create directories;
- use UTF-8 consistently.

Functions:

```python
def read_jsonl(path: Path) -> list[dict]:
    ...

def write_jsonl(path: Path, records: list[dict]) -> None:
    ...

def ensure_directory(path: Path) -> Path:
    ...
```

### `common/text.py`

Purpose:

- normalize Unicode;
- remove duplicate whitespace;
- lowercase for search;
- normalize punctuation;
- normalize common OCR text errors without changing the original text.

Keep two versions:

```text
raw_text
normalized_text
```

Never destroy the raw text.

### `common/numbers.py`

Purpose:

- parse Vietnamese financial numbers;
- handle thousand separators;
- handle negative values in parentheses;
- handle dashes and empty cells;
- convert units.

Examples:

```text
"1.234.567"   → 1234567
"(125.000)"   → -125000
"-"           → missing value
```

### `common/logging_utils.py`

Purpose:

- configure structured logs;
- include question ID, document ID, table ID, stage, and error type.

### `common/exceptions.py`

Defines clear exceptions:

```python
class QuestionParseError(Exception):
    pass

class RetrievalError(Exception):
    pass

class GroundingError(Exception):
    pass

class UnsafeQueryError(Exception):
    pass
```

---

## 7.4 Ingestion and report indexing

### `ingestion/question_loader.py`

Reads `questions.jsonl`.

```python
def load_questions(path: Path) -> list[QuestionRecord]:
    ...
```

Checks:

- ID is integer;
- question is non-empty;
- IDs are unique.

### `ingestion/stock_loader.py`

Loads `code_stock.csv` and builds:

```text
ticker → company name
company alias → ticker
normalized company name → ticker
```

### `ingestion/report_scanner.py`

Scans this structure:

```text
TICKER/YEAR/DOCUMENT/FILE.txt
```

It produces raw metadata from paths.

### `ingestion/report_indexer.py`

Combines scanned report paths with company metadata and saves:

```text
report_index.parquet
```

It also reports:

- number of documents;
- missing company names;
- invalid years;
- distribution of separate/consolidated/other reports.

---

## 7.5 Table extraction

### `tables/html_table_extractor.py`

Purpose:

- read OCR report text;
- detect `<table>...</table>`;
- preserve table order;
- detect current page;
- save raw table HTML and parsed cells.

Main output:

```python
ExtractedTable(
    document_id=...,
    table_position=...,
    page=...,
    raw_html=...,
    rows=...,
)
```

### `tables/table_cleaner.py`

Purpose:

- remove empty rows and columns;
- repair repeated whitespace;
- merge obvious wrapped cells;
- keep a trace of every transformation.

Do not aggressively repair uncertain tables.

### `tables/header_resolver.py`

Purpose:

- identify header rows;
- flatten multi-row headers;
- preserve original header text;
- create unique column names.

Example:

```text
Original:
Năm nay | Năm trước

Resolved for a 2023 report:
2023 | 2022
```

The mapping must consider the report year and table type.

### `tables/unit_detector.py`

Searches:

- table caption;
- rows above the table;
- table cells;
- nearby text.

It returns:

```json
{
  "unit": "million_vnd",
  "confidence": 0.94,
  "evidence_text": "Đơn vị tính: Triệu đồng"
}
```

### `tables/table_normalizer.py`

Creates a clean CSV while preserving provenance.

Recommended columns for a long-format normalized table:

```text
row_id
row_label_raw
row_label_normalized
column_raw
column_normalized
value_raw
value_numeric
unit
```

Long format is useful for retrieval and row/column matching.

A wide CSV may still be produced for final evidence if it makes the Pandas query simpler.

### `tables/table_indexer.py`

Creates one searchable record per table.

Text for retrieval may combine:

```text
caption
headers
row labels
context before
context after
unit
```

---

## 7.6 Question parsing

### `parsing/normalizer.py`

Normalizes the question before parsing while keeping character offsets.

It should protect:

- tickers;
- dates;
- percentages;
- abbreviations;
- company names when possible.

### `parsing/rules.py`

High-precision rules for:

- ticker;
- year;
- date;
- report scope;
- requested unit;
- obvious time anchor.

Example:

```python
def parse_scope(question: str) -> tuple[str, float]:
    text = question.lower()

    if "công ty mẹ" in text or "báo cáo riêng" in text:
        return "separate", 1.0

    if "hợp nhất" in text:
        return "consolidated", 1.0

    return "unspecified", 0.5
```

### `parsing/company_resolver.py`

Uses:

- ticker rules;
- exact company names;
- aliases;
- fuzzy matching.

It must distinguish the main company from a related company mentioned inside the target.

### `parsing/word_segmenter.py`

Wraps Vietnamese word segmentation for PhoBERT.

Responsibilities:

- protect special tokens;
- segment Vietnamese text;
- maintain a mapping back to original character positions.

### `parsing/phobert_model.py`

Defines the shared PhoBERT encoder and task heads.

Recommended heads:

```text
metric start head
metric end head
target-full start head
target-full end head
qualifier token head
operation classification head
statement-family multi-label head
```

PhoBERT should not predict the numeric answer.

### `parsing/phobert_parser.py`

Converts model outputs into spans and labels.

Example output:

```json
{
  "metric_head": "cho vay khách hàng",
  "target_full": "Số dư cho vay khách hàng ngành Thương mại",
  "qualifiers": [
    {
      "type": "sector",
      "text": "Thương mại"
    }
  ],
  "operation": "direct_lookup"
}
```

### `parsing/operation_classifier.py`

Classifies the calculation type.

Initial classes:

```text
direct_lookup
difference
growth_rate
sum
average
ratio
maximum
minimum
comparison
ambiguous
```

### `parsing/confidence.py`

Combines:

- model probabilities;
- rule confidence;
- rule-model agreement;
- schema consistency;
- company-ticker consistency.

### `parsing/parser.py`

This is the public interface for question parsing.

```python
class QuestionParser:
    def parse(self, question: QuestionRecord) -> QuestionIR:
        ...
```

Flow:

```text
normalize
→ apply rules
→ resolve company
→ run PhoBERT
→ merge predictions
→ validate QuestionIR
→ calculate confidence
```

---

## 7.7 Retrieval

### `retrieval/document_retriever.py`

Uses hard metadata filters:

```text
ticker
year
scope
```

If scope is unspecified, it should keep multiple valid report types.

### `retrieval/lexical_retriever.py`

Uses BM25 or another lexical method.

Strong for exact words, abbreviations, and rare names.

Input text:

```text
metric + target_full + qualifiers
```

### `retrieval/dense_retriever.py`

Uses an embedding model to compare the question meaning with table meaning.

The embedding model should be configurable. Before official competition use, verify that its release date, size, and license follow the competition rules.

### `retrieval/hybrid_retriever.py`

Combines scores.

Example:

```python
final_score = (
    0.30 * lexical_score
    + 0.35 * dense_score
    + 0.20 * metadata_score
    + 0.15 * unit_and_time_score
)
```

These weights are only an initial baseline. Tune them on a development set.

### `retrieval/reranker.py`

A cross-encoder reads the full pair:

```text
question + table metadata + important rows
```

It reranks only the top candidates, for example top 20 to top 5.

This is slower but more accurate than embedding retrieval.

### `retrieval/context_builder.py`

Builds a compact RAG context.

It should include:

- document ID;
- table position;
- page;
- caption;
- unit;
- headers;
- top matching rows;
- nearby text.

It should not send the full report to the model.

---

## 7.8 Grounding

Grounding means connecting words in the question to real rows and columns.

### `grounding/metric_aliases.py`

Stores controlled financial aliases.

Example:

```python
METRIC_ALIASES = {
    "doanh_thu_thuan": [
        "doanh thu thuần",
        "doanh thu thuần về bán hàng và cung cấp dịch vụ",
    ],
}
```

Do not attempt to manually define every financial term. Use aliases only for frequent or important terms.

### `grounding/metric_matcher.py`

Matches `metric_head` to table rows.

It combines:

- normalized exact match;
- alias match;
- fuzzy score;
- embedding score;
- table context.

### `grounding/qualifier_matcher.py`

Checks whether the candidate row matches:

- sector;
- person;
- related company;
- asset or project;
- maturity;
- currency;
- loan group;
- other filters.

### `grounding/row_matcher.py`

Ranks rows inside candidate tables.

Output:

```json
{
  "row_index": 5,
  "row_label": "Lãi tiền gửi",
  "score": 0.97
}
```

### `grounding/column_matcher.py`

Maps the requested year and time anchor to a real column.

Example:

```text
question: cuối năm 2023
candidate columns: đầu năm | cuối năm
chosen column: cuối năm
```

### `grounding/time_resolver.py`

Handles:

```text
năm 2023
trong năm 2023
cuối năm 2023
đầu năm 2023
31/12/2023
01/01/2023
```

### `grounding/unit_resolver.py`

Determines:

- source unit;
- target unit;
- conversion factor.

Example:

```text
source = million_vnd
target = billion_vnd
factor = 0.001
```

---

## 7.9 Planning

### `planning/financial_formulas.py`

Stores known formula templates.

Examples:

```python
def growth_rate(current: float, previous: float) -> float:
    return (current - previous) / previous * 100.0

def average_balance(begin: float, end: float) -> float:
    return (begin + end) / 2.0
```

This file contains financial formulas, not DataFrame access code.

### `planning/plan_builder.py`

Builds an `ExecutionPlan` from grounded rows and columns.

### `planning/plan_validator.py`

Checks:

- every input has evidence;
- all required years are present;
- denominator is not zero;
- units are compatible;
- the operation has the correct number of inputs.

### `planning/llm_fallback.py`

Optional fallback for low-confidence or ambiguous questions.

The LLM receives:

- QuestionIR;
- top retrieved tables;
- rows and columns;
- allowed operation schema.

It returns only a structured plan, not unrestricted Python and not the final answer.

The fallback should be used only when deterministic planning is insufficient.

---

## 7.10 Pandas generation and execution

### `execution/query_templates.py`

Contains safe templates for:

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

### `execution/pandas_generator.py`

Transforms an `ExecutionPlan` into a Pandas expression.

Example:

```python
def generate_direct_lookup(plan: ExecutionPlan) -> str:
    item = repr(plan.inputs[0].row_label)
    column = repr(plan.inputs[0].column_label)
    factor = plan.conversion_factor

    return (
        f"float(df1.loc[df1['item'] == {item}, {column}].iloc[0])"
        f" * {factor}"
    )
```

### `execution/safe_executor.py`

Do not run unrestricted Python.

The executor should:

- parse the expression with `ast`;
- allow only approved syntax;
- block imports;
- block attribute access outside approved objects;
- block file, network, and system access;
- use only declared DataFrames;
- enforce a timeout.

### `execution/result_validator.py`

Checks:

- result is a scalar;
- result is numeric;
- result is not NaN;
- result is finite;
- unit conversion was applied;
- result magnitude is not obviously impossible.

Magnitude checks should warn, not automatically reject, unless the rule is certain.

### `execution/repair.py`

Handles recoverable failures:

```text
column name mismatch
numeric string not parsed
duplicate row
missing unit conversion
```

Repair must be logged. It must not silently change the question meaning.

---

## 7.11 Verification

### `verification/source_verifier.py`

Verifies that:

- document exists;
- table exists;
- evidence CSV came from that table;
- table belongs to the expected ticker and year.

### `verification/consistency_checker.py`

Checks consistency between:

```text
QuestionIR
retrieved report
retrieved table
execution plan
Pandas query
answer
```

### `verification/answer_verifier.py`

May run multiple candidate plans and compare results.

Possible strategy:

```text
top table 1 → answer A
top table 2 → answer B
alternate row → answer C
```

The verifier selects the result with the strongest source and consistency evidence.

### `verification/confidence_router.py`

Routes the question:

```text
high confidence → accept
medium confidence → run additional verification
low confidence → use fallback or manual review
```

---

## 7.12 Pipeline

### `pipeline/state.py`

Stores all intermediate outputs for one question:

```python
class PipelineState(BaseModel):
    question: QuestionRecord
    question_ir: QuestionIR | None
    candidate_reports: list[ReportRecord]
    candidate_tables: list[RetrievalCandidate]
    selected_grounding: GroundingResult | None
    execution_plan: ExecutionPlan | None
    pandas_query: str | None
    result: float | None
    errors: list[str]
```

### `pipeline/single_question.py`

Runs the full pipeline for one question.

```python
def solve_question(question: QuestionRecord) -> SubmissionRecord:
    ...
```

### `pipeline/batch_runner.py`

Processes all questions.

Features:

- resume after failure;
- save intermediate states;
- parallelize safe stages;
- keep deterministic ordering;
- isolate question failures.

### `pipeline/checkpoint_store.py`

Saves:

```text
question_ir.jsonl
retrieval_results.jsonl
execution_plans.jsonl
execution_results.jsonl
```

This prevents rerunning the entire pipeline after one small code change.

---

## 7.13 Submission

### `submission/builder.py`

Builds one final record from a verified pipeline state.

### `submission/schema_validator.py`

Validates:

- all question IDs are present;
- IDs are unique;
- answer is numeric;
- `relevant_docs` is a list;
- `relevant_tables` uses the correct format;
- evidence variables appear in the query;
- evidence paths begin with `data/`.

### `submission/packager.py`

Creates:

```text
submission.zip
├── submission.json
└── data/
```

It copies only used evidence CSV files.

---

## 7.14 Training

### `training/annotation_builder.py`

Creates an annotation file for question parsing.

It can pre-label easy fields using rules:

```text
ticker
year
date
unit
scope
```

Humans review harder fields:

```text
metric
target_full
qualifiers
operation
statement families
```

### `training/parser_dataset.py`

Tokenizes and aligns spans with PhoBERT tokens.

### `training/train_phobert.py`

Trains the multi-task parser.

It should save:

- model weights;
- tokenizer configuration;
- label maps;
- training configuration;
- metrics;
- random seed.

### `training/train_reranker.py`

Trains a table reranker using:

```text
question
positive table
hard negative tables
```

Hard negatives are tables that look similar but are wrong.

### `training/evaluate_models.py`

Evaluates parser and reranker checkpoints on fixed development data.

---

## 7.15 Evaluation

### `evaluation/parsing_metrics.py`

Metrics:

```text
ticker exact match
year exact match
scope accuracy
metric span F1
qualifier span F1
operation accuracy
full QuestionIR exact match
```

### `evaluation/retrieval_metrics.py`

Metrics:

```text
Recall@1
Recall@3
Recall@5
Precision
Recall
F2
MRR
```

### `evaluation/execution_metrics.py`

Metrics:

```text
query parse rate
query execution rate
numeric result rate
answer accuracy
unit conversion accuracy
```

### `evaluation/pipeline_metrics.py`

End-to-end metrics:

```text
valid submission record rate
full grounded answer rate
answer accuracy
execution accuracy
```

### `evaluation/error_analysis.py`

Groups errors:

```text
wrong ticker
wrong report year
wrong scope
wrong table
wrong row
wrong column
wrong unit
wrong operation
OCR number error
execution error
```

---

# 8. Scripts and the order to run them

The files under `scripts/` are thin command-line entry points. Business logic stays in `src/`.

## `01_download_data.py`

Calls the existing dataset downloader.

Output:

```text
data/raw/ViFinQA/
```

## `02_build_report_index.py`

Creates:

```text
data/processed/report_index.parquet
```

## `03_extract_tables.py`

Extracts all HTML tables and stores raw and cleaned versions.

Output:

```text
data/processed/tables/
```

## `04_build_table_index.py`

Creates:

```text
data/processed/table_index.parquet
```

## `05_build_retrieval_indexes.py`

Creates:

```text
BM25 index
dense vector index
metadata lookup structures
```

## `06_parse_questions.py`

Creates:

```text
outputs/question_ir.jsonl
```

## `07_run_retrieval.py`

Creates:

```text
outputs/retrieval_results.jsonl
```

## `08_run_pipeline.py`

Runs grounding, planning, Pandas generation, execution, and verification.

## `09_validate_results.py`

Runs all schema and consistency checks.

## `10_build_submission.py`

Creates the final ZIP.

---

# 9. Recommended model stack

The architecture should be model-configurable. Do not lock the project to one model name.

## 9.1 Question understanding

Primary:

```text
PhoBERT encoder
```

Tasks:

- metric span extraction;
- full target extraction;
- qualifier extraction;
- operation classification;
- statement-family prediction.

Rules still handle ticker, date, year, unit, and obvious scope.

## 9.2 Dense table retrieval

Use an open embedding model with:

- multilingual or Vietnamese capability;
- support for short queries and long table text;
- reproducible model files;
- competition-compliant release date and parameter count.

The exact model should be selected by development-set retrieval results.

## 9.3 Reranking

Use a cross-encoder or sequence-classification model that scores:

```text
question + candidate table
```

A reranker is more important than using a very large generative model.

## 9.4 Optional LLM fallback

Use only for:

- ambiguous operation planning;
- complex multi-table reasoning;
- repair suggestions;
- low-confidence semantic parsing.

Constraints:

- open model;
- competition-compliant;
- structured output only;
- retrieved context only;
- no direct answer without execution;
- no unrestricted Python.

---

# 10. Retrieval strategy in detail

## 10.1 Step 1: hard metadata filtering

Use:

```text
ticker
year
scope
```

This reduces the corpus drastically.

## 10.2 Step 2: lexical retrieval

Search:

```text
caption
headers
row labels
nearby context
```

Lexical retrieval is important for rare financial terms and names.

## 10.3 Step 3: dense retrieval

Search by meaning.

## 10.4 Step 4: hybrid score

Combine lexical, dense, metadata, time, and unit scores.

## 10.5 Step 5: rerank

Rerank top 20 or top 30 candidates.

## 10.6 Step 6: row-aware reranking

A table should receive a higher score when one or more rows closely match the target and qualifiers.

## 10.7 Step 7: execution-aware selection

When two tables remain plausible, build and execute plans for both. Prefer the plan that:

- executes successfully;
- has consistent units;
- has exact year columns;
- matches all qualifiers;
- produces a scalar;
- has stronger source evidence.

---

# 11. RAG context design

A RAG context should be compact and structured.

Bad context:

```text
the entire 300-page report
```

Good context:

```text
Document: VJC_financial_statements_2018_separate
Table position: 350
Page: 72
Caption: Doanh thu hoạt động tài chính
Unit: Triệu đồng
Headers: Chỉ tiêu | 2018 | 2017
Matching rows:
- Lãi tiền gửi | 123.456 | 98.765
- Lãi chênh lệch tỷ giá | ...
Nearby text:
...
```

The model should receive candidate IDs. It must choose from candidates instead of inventing IDs.

---

# 12. Modern reliability features

## 12.1 Multi-stage confidence

Keep confidence at every stage:

```text
question parse confidence
document confidence
table confidence
row confidence
column confidence
unit confidence
execution confidence
```

Do not hide uncertainty inside one final score.

## 12.2 Candidate preservation

Keep top candidates until execution. Early hard decisions create unrecoverable errors.

## 12.3 Provenance tracking

Every normalized value should store:

```text
source document
page
table position
original row
original column
original cell
normalization steps
```

## 12.4 Execution-guided repair

Use execution failures as feedback.

Example:

```text
generated column = "2023"
actual column = "Năm nay"
```

The repair module can use the column mapping and regenerate the query.

## 12.5 Active learning

After the first parser model:

1. Run it on all questions.
2. Sort by lowest confidence.
3. Manually annotate uncertain and diverse questions.
4. Retrain.
5. Repeat.

This is more efficient than annotating random questions.

## 12.6 Hard-negative mining

For reranking, use wrong tables that are highly similar to the correct table.

Examples:

```text
same metric, wrong year
same metric, wrong report scope
same company and year, wrong note
same table family, wrong qualifier
```

---

# 13. Development plan

## Phase 0: Repository foundation

Tasks:

- remove `.venv` from Git;
- ignore downloaded data and generated files;
- add `requirements.txt` or `pyproject.toml`;
- create `src/`, `scripts/`, and `tests/`;
- add typed schemas.

Acceptance criteria:

```text
pytest runs
configuration loads
sample data can be read
```

## Phase 1: Deterministic question baseline

Implement:

- ticker extraction;
- company resolution;
- year/date extraction;
- scope rules;
- unit rules;
- time anchor rules;
- simple metric extraction using sentence patterns.

Acceptance criteria:

```text
all questions produce valid QuestionIR
no crashes
field coverage report is generated
```

This baseline is important even if PhoBERT will be added later.

## Phase 2: Table extraction and indexing

Implement:

- page detection;
- HTML table extraction;
- CSV creation;
- header normalization;
- unit detection;
- table metadata index.

Acceptance criteria:

```text
every detected table has a stable ID
every CSV can be opened by Pandas
every table links back to its report and page
```

## Phase 3: Basic retrieval

Implement:

- metadata report filter;
- BM25 table search;
- row-label search;
- top-k output.

Acceptance criteria:

```text
manual development questions have strong Recall@5
```

## Phase 4: PhoBERT semantic parser

Create annotations for a diverse subset.

Train:

- metric span;
- target-full span;
- qualifiers;
- operation;
- statement family.

Acceptance criteria:

```text
parser improves metric and qualifier extraction over rules
full QuestionIR validity remains 100%
```

## Phase 5: Dense retrieval and reranking

Add:

- table embeddings;
- hybrid retrieval;
- hard-negative reranker.

Acceptance criteria:

```text
Recall@5 improves
top-1 accuracy improves
wrong-year and wrong-scope errors decrease
```

## Phase 6: Grounding and Pandas execution

Implement:

- row matcher;
- column matcher;
- unit resolver;
- plan builder;
- query templates;
- safe executor.

Acceptance criteria:

```text
at least one diverse development batch runs end to end
all accepted answers have reproducible evidence
```

## Phase 7: RAG fallback and verification

Add:

- compact context builder;
- optional open LLM planner;
- multi-candidate execution;
- consistency verification;
- confidence routing.

Acceptance criteria:

```text
fallback improves difficult cases without increasing hallucinated sources
```

## Phase 8: Batch processing and submission

Implement:

- checkpointed batch runner;
- error reports;
- submission validator;
- ZIP packager.

Acceptance criteria:

```text
all required question IDs are present
all evidence files exist
all queries execute in the final package
```

---

# 14. First implementation milestone

Do not begin by training a large model.

The first complete milestone should solve one question end to end:

```text
question
→ QuestionIR
→ correct report
→ correct table
→ correct row
→ correct column
→ correct unit
→ Pandas query
→ numeric answer
→ valid submission record
```

Then test ten diverse questions:

```text
direct income statement value
balance-sheet closing value
opening value
banking table with sector filter
related-party row
person compensation
asset or project row
percentage
share count
unit conversion
```

Only after this should the pipeline expand to all questions.

---

# 15. Testing strategy

## Unit tests

Test small deterministic functions:

```text
date parsing
unit conversion
number parsing
scope rules
header mapping
query templates
```

## Integration tests

Test module pairs:

```text
report scanner + report indexer
table extractor + normalizer
retriever + reranker
plan builder + executor
```

## End-to-end tests

Use a small manually verified dataset.

Each test should verify:

```text
document
table
row
column
unit
query
answer
```

---

# 16. Error analysis loop

For every wrong or failed question, assign one primary error type.

```text
PARSER_TICKER
PARSER_YEAR
PARSER_SCOPE
PARSER_METRIC
PARSER_QUALIFIER
RETRIEVAL_DOCUMENT
RETRIEVAL_TABLE
GROUNDING_ROW
GROUNDING_COLUMN
GROUNDING_UNIT
PLANNING_OPERATION
EXECUTION
OCR_VALUE
SUBMISSION_FORMAT
```

This avoids random model changes.

Example:

```text
If most failures are GROUNDING_COLUMN,
training a larger PhoBERT model will not solve the real problem.
```

---

# 17. Important anti-patterns

Do not:

- let an LLM invent document IDs;
- let an LLM invent table positions;
- generate the numeric answer directly;
- hard-code answers;
- use unrestricted `eval`;
- treat unspecified scope as always consolidated;
- destroy original OCR text during normalization;
- select a table only from its title;
- ignore units;
- use only one retrieval method;
- run all 1,012 questions before one end-to-end example works;
- mix data downloading, model code, retrieval, and submission code in one file.

---

# 18. Minimum viable architecture

A strong project can still start small.

Version 1:

```text
rules
+ report index
+ table extraction
+ BM25
+ fuzzy row matching
+ deterministic Pandas templates
+ validation
```

Version 2:

```text
Version 1
+ PhoBERT semantic parser
+ dense retrieval
+ reranker
```

Version 3:

```text
Version 2
+ RAG planner fallback
+ multi-candidate execution
+ answer verification
+ active learning
```

This order reduces risk. Every version remains usable and testable.

---

# 19. Final architecture recommendation

Use this final system:

```text
Rule parser
    +
PhoBERT multi-task semantic parser
    ↓
QuestionIR
    ↓
Metadata document filter
    ↓
BM25 + dense table retrieval
    ↓
Cross-encoder reranking
    ↓
Row/column/time/unit grounding
    ↓
Structured execution plan
    ↓
Deterministic Pandas generator
    ↓
Safe executor
    ↓
Source and answer verifier
    ↓
Submission builder
```

The strongest part of this architecture is not one specific model. The strength comes from:

- dividing the problem correctly;
- grounding every decision in source data;
- preserving multiple candidates;
- generating code from a validated plan;
- using execution as a test;
- keeping full provenance;
- measuring every stage separately.

That is the correct foundation for a modern, reproducible financial question-answering system.
