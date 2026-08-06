#!/usr/bin/env python3
"""Audit ViFinQA corpus structure and investigate numeric table IDs.

All paths are defined in ``project_paths.py``. Run this file directly:

    python data/process/audit_dataset.py

The public questions file contains only ``id`` and ``question``. When a custom
question file includes ``relevant_tables``, the script tests whether each
numeric table ID corresponds to a table order, character offset, byte offset,
line number, page number, or deterministic global order.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from project_paths import (
    AUDIT_OUTPUT_DIR,
    resolve_financial_statements_dir,
    resolve_questions_path,
)


# ---------------------------------------------------------------------------
# Direct source-code paths
# ---------------------------------------------------------------------------
DATASET_ROOT = resolve_financial_statements_dir()
QUESTIONS_FILE = resolve_questions_path()
OUTPUT_DIR = AUDIT_OUTPUT_DIR


TABLE_RE = re.compile(r"<table\b.*?</table>", flags=re.IGNORECASE | re.DOTALL)
PAGE_RE = re.compile(r"===== PAGE (\d+) =====")
DF_VARIABLE_RE = re.compile(r"\bdf\d+\b")
SCOPE_RE = re.compile(r"_(consolidated|separate)$", flags=re.IGNORECASE)

HYPOTHESES: dict[str, str] = {
    "local_order_0": "local_order_0",
    "local_order_1": "local_order_1",
    "start_char_0": "start_char_0",
    "start_char_1": "start_char_1",
    "start_byte_0": "start_byte_0",
    "start_byte_1": "start_byte_1",
    "start_line_0": "start_line_0",
    "start_line_1": "start_line_1",
    "page_no": "page_no",
    "page_order_0": "page_order_0",
    "page_order_1": "page_order_1",
    "global_order_0": "global_order_0",
    "global_order_1": "global_order_1",
}

HYPOTHESIS_FIELDS = [
    "hypothesis",
    "total_references",
    "exact_match_references",
    "exact_match_rate",
    "unique_match_references",
    "unique_match_rate",
    "collision_references",
    "collision_rate",
    "pass_exact_99_9",
    "pass_unique_99_5",
    "pass_zero_collisions",
]


# ---------------------------------------------------------------------------
# Generic I/O helpers
# ---------------------------------------------------------------------------
def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at line {line_number}: {exc}"
                ) from exc

            if not isinstance(value, dict):
                raise ValueError(
                    f"Expected a JSON object at line {line_number}, "
                    f"received {type(value).__name__}"
                )
            yield value


# ---------------------------------------------------------------------------
# Report and table parsing
# ---------------------------------------------------------------------------
def infer_document_id(path: Path) -> str:
    if path.name.endswith("_extracted.txt"):
        return path.name[: -len("_extracted.txt")]
    if "_financial_statements_" in path.parent.name:
        return path.parent.name
    return path.stem


def parse_table_reference(reference: str) -> tuple[str, int]:
    document_id, separator, raw_id = reference.rpartition("|")
    if not separator or not document_id:
        raise ValueError(
            f"Expected '<document_id>|<table_id>', received {reference!r}"
        )

    raw_id = raw_id.removeprefix("table_")
    try:
        table_id = int(raw_id)
    except ValueError as exc:
        raise ValueError(f"Non-integer table ID in {reference!r}") from exc

    if table_id < 0:
        raise ValueError(f"Negative table ID in {reference!r}")
    return document_id, table_id


def safe_preview(table_html: str, limit: int = 240) -> str:
    value = re.sub(r"<[^>]+>", " ", table_html)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def discover_report_paths(dataset_root: Path) -> list[Path]:
    report_paths = sorted(dataset_root.rglob("*_extracted.txt"))
    if not report_paths:
        report_paths = sorted(dataset_root.rglob("*.txt"))
    return [path for path in report_paths if path.is_file()]


def parse_document(
    path: Path,
    dataset_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # Do not normalize this text before computing offsets.
    text = path.read_text(encoding="utf-8", errors="replace")
    relative_path = path.relative_to(dataset_root)
    path_parts = relative_path.parts

    ticker = path_parts[0] if len(path_parts) >= 1 else ""
    year = path_parts[1] if len(path_parts) >= 2 else ""
    document_id = infer_document_id(path)
    scope_match = SCOPE_RE.search(document_id)
    scope = scope_match.group(1).lower() if scope_match else "unknown"

    page_matches = list(PAGE_RE.finditer(text))
    page_starts = [match.start() for match in page_matches]
    page_numbers = [int(match.group(1)) for match in page_matches]

    def page_for_position(position: int) -> int | None:
        index = bisect.bisect_right(page_starts, position) - 1
        return page_numbers[index] if index >= 0 else None

    document_metadata = {
        "ticker": ticker,
        "year": year,
        "document_id": document_id,
        "scope": scope,
        "source_path": str(path),
    }

    page_table_counts: Counter[int | None] = Counter()
    tables: list[dict[str, Any]] = []

    for local_order_1, match in enumerate(TABLE_RE.finditer(text), start=1):
        start_char_0 = match.start()
        page_no = page_for_position(start_char_0)
        page_table_counts[page_no] += 1
        table_html = match.group(0)
        start_byte_0 = len(text[:start_char_0].encode("utf-8"))
        start_line_0 = text.count("\n", 0, start_char_0)

        tables.append(
            {
                **document_metadata,
                "local_order_0": local_order_1 - 1,
                "local_order_1": local_order_1,
                "page_no": page_no,
                "page_order_0": page_table_counts[page_no] - 1,
                "page_order_1": page_table_counts[page_no],
                "start_char_0": start_char_0,
                "start_char_1": start_char_0 + 1,
                "start_byte_0": start_byte_0,
                "start_byte_1": start_byte_0 + 1,
                "start_line_0": start_line_0,
                "start_line_1": start_line_0 + 1,
                "html_length_chars": len(table_html),
                "table_sha1": hashlib.sha1(
                    table_html.encode("utf-8")
                ).hexdigest(),
                "preview": safe_preview(table_html),
            }
        )

    document_row = {
        **document_metadata,
        "file_size_bytes": path.stat().st_size,
        "character_count": len(text),
        "utf8_byte_count": len(text.encode("utf-8")),
        "page_count": len(page_matches),
        "table_count": len(tables),
        "first_table_offset": tables[0]["start_char_0"] if tables else None,
        "last_table_offset": tables[-1]["start_char_0"] if tables else None,
    }
    return document_row, tables


def scan_corpus(
    dataset_root: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
]:
    report_paths = discover_report_paths(dataset_root)
    if not report_paths:
        raise FileNotFoundError(
            f"No extracted report text files found under {dataset_root}"
        )

    document_rows: list[dict[str, Any]] = []
    table_inventory: list[dict[str, Any]] = []
    tables_by_document: dict[str, list[dict[str, Any]]] = {}
    duplicate_paths: defaultdict[str, list[str]] = defaultdict(list)
    anomalies: list[dict[str, Any]] = []

    global_order = 0
    for path in report_paths:
        document_row, tables = parse_document(path, dataset_root)
        document_id = document_row["document_id"]
        duplicate_paths[document_id].append(str(path))

        for table in tables:
            table["global_order_0"] = global_order
            table["global_order_1"] = global_order + 1
            global_order += 1

        document_rows.append(document_row)
        table_inventory.extend(tables)
        tables_by_document.setdefault(document_id, tables)

    for document_id, paths in duplicate_paths.items():
        if len(paths) > 1:
            anomalies.append(
                {
                    "type": "duplicate_document_id",
                    "key": document_id,
                    "detail": " | ".join(paths),
                }
            )

    return document_rows, table_inventory, tables_by_document, anomalies


# ---------------------------------------------------------------------------
# Question and label statistics
# ---------------------------------------------------------------------------
def describe(values: list[int | float]) -> dict[str, Any]:
    clean = [value for value in values if value is not None]
    if not clean:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "mean": None,
            "max": None,
        }
    return {
        "count": len(clean),
        "min": min(clean),
        "median": median(clean),
        "mean": mean(clean),
        "max": max(clean),
    }


def analyze_questions(
    questions_path: Path,
    tables_by_document: dict[str, list[dict[str, Any]]],
    anomalies: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
]:
    question_rows: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []

    field_presence: Counter[str] = Counter()
    evidence_variables: Counter[str] = Counter()
    query_patterns: Counter[str] = Counter()
    exact_matches: Counter[str] = Counter()
    unique_matches: Counter[str] = Counter()
    collisions: Counter[str] = Counter()

    relevant_tables_per_question: list[int] = []
    relevant_docs_per_question: list[int] = []
    evidence_items_per_question: list[int] = []
    query_lengths: list[int] = []
    total_references = 0

    for question_index, question in enumerate(
        iter_jsonl(questions_path),
        start=1,
    ):
        field_presence.update(question.keys())

        relevant_docs = list(question.get("relevant_docs") or [])
        relevant_tables = list(question.get("relevant_tables") or [])
        evidence = list(question.get("evidence") or [])
        pandas_query = str(question.get("pandas_query") or "")

        relevant_tables_per_question.append(len(relevant_tables))
        relevant_docs_per_question.append(len(relevant_docs))
        evidence_items_per_question.append(len(evidence))
        query_lengths.append(len(pandas_query))

        declared_variables: list[str] = []
        for item in evidence:
            if isinstance(item, dict) and item.get("variable"):
                variable = str(item["variable"])
                evidence_variables[variable] += 1
                declared_variables.append(variable)

        query_df_variables = sorted(set(DF_VARIABLE_RE.findall(pandas_query)))
        if query_df_variables:
            query_patterns["uses_dfN"] += 1
        if re.search(r"\bdf\b", pandas_query):
            query_patterns["uses_df"] += 1
        if re.search(r"\bdfs\b", pandas_query):
            query_patterns["uses_dfs"] += 1
        if re.search(r"\bresult\s*=", pandas_query):
            query_patterns["assigns_result"] += 1
        if not pandas_query:
            query_patterns["missing_query"] += 1

        question_rows.append(
            {
                "question_index": question_index,
                "question_id": question.get("id", ""),
                "question": question.get("question", ""),
                "relevant_doc_count": len(relevant_docs),
                "relevant_table_count": len(relevant_tables),
                "evidence_count": len(evidence),
                "evidence_variables": ";".join(declared_variables),
                "query_df_variables": ";".join(query_df_variables),
                "query_uses_df": bool(re.search(r"\bdf\b", pandas_query)),
                "query_uses_dfs": bool(re.search(r"\bdfs\b", pandas_query)),
                "query_assigns_result": bool(
                    re.search(r"\bresult\s*=", pandas_query)
                ),
                "query_length": len(pandas_query),
            }
        )

        for raw_reference in relevant_tables:
            total_references += 1
            reference = str(raw_reference)
            try:
                document_id, table_id = parse_table_reference(reference)
            except ValueError as exc:
                anomalies.append(
                    {
                        "type": "invalid_reference",
                        "key": reference,
                        "detail": str(exc),
                    }
                )
                continue

            candidates = tables_by_document.get(document_id)
            if candidates is None:
                anomalies.append(
                    {
                        "type": "document_not_found",
                        "key": reference,
                        "detail": document_id,
                    }
                )
                continue

            audit_row: dict[str, Any] = {
                "question_index": question_index,
                "question_id": question.get("id", ""),
                "reference": reference,
                "document_id": document_id,
                "table_id": table_id,
                "document_table_count": len(candidates),
            }

            for hypothesis, field_name in HYPOTHESES.items():
                matches = [
                    candidate
                    for candidate in candidates
                    if candidate.get(field_name) == table_id
                ]
                match_count = len(matches)
                audit_row[f"{hypothesis}_match_count"] = match_count
                audit_row[f"{hypothesis}_orders_1"] = ";".join(
                    str(candidate["local_order_1"]) for candidate in matches
                )
                if match_count >= 1:
                    exact_matches[hypothesis] += 1
                if match_count == 1:
                    unique_matches[hypothesis] += 1
                if match_count > 1:
                    collisions[hypothesis] += 1

            reference_rows.append(audit_row)

    hypothesis_rows: list[dict[str, Any]] = []
    if total_references > 0:
        for hypothesis in HYPOTHESES:
            exact_rate = exact_matches[hypothesis] / total_references
            unique_rate = unique_matches[hypothesis] / total_references
            collision_rate = collisions[hypothesis] / total_references
            hypothesis_rows.append(
                {
                    "hypothesis": hypothesis,
                    "total_references": total_references,
                    "exact_match_references": exact_matches[hypothesis],
                    "exact_match_rate": exact_rate,
                    "unique_match_references": unique_matches[hypothesis],
                    "unique_match_rate": unique_rate,
                    "collision_references": collisions[hypothesis],
                    "collision_rate": collision_rate,
                    "pass_exact_99_9": exact_rate >= 0.999,
                    "pass_unique_99_5": unique_rate >= 0.995,
                    "pass_zero_collisions": collisions[hypothesis] == 0,
                }
            )

        hypothesis_rows.sort(
            key=lambda row: (
                row["exact_match_rate"],
                row["unique_match_rate"],
                -row["collision_rate"],
            ),
            reverse=True,
        )

    question_statistics: list[dict[str, Any]] = []
    for name, values in [
        ("relevant_tables_per_question", relevant_tables_per_question),
        ("relevant_docs_per_question", relevant_docs_per_question),
        ("evidence_items_per_question", evidence_items_per_question),
        ("query_length", query_lengths),
    ]:
        question_statistics.append({"statistic": name, **describe(values)})

    for field, count in sorted(field_presence.items()):
        question_statistics.append(
            {
                "statistic": f"field_presence::{field}",
                "count": count,
                "min": None,
                "median": None,
                "mean": None,
                "max": None,
            }
        )
    for variable, count in sorted(evidence_variables.items()):
        question_statistics.append(
            {
                "statistic": f"evidence_variable::{variable}",
                "count": count,
                "min": None,
                "median": None,
                "mean": None,
                "max": None,
            }
        )
    for pattern, count in sorted(query_patterns.items()):
        question_statistics.append(
            {
                "statistic": f"query_pattern::{pattern}",
                "count": count,
                "min": None,
                "median": None,
                "mean": None,
                "max": None,
            }
        )

    return question_rows, reference_rows, question_statistics, total_references


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def build_dataset_summary(
    document_rows: list[dict[str, Any]],
    table_inventory: list[dict[str, Any]],
    question_rows: list[dict[str, Any]],
    total_references: int,
    anomalies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "metric": "ticker_count",
            "value": len({row["ticker"] for row in document_rows}),
        },
        {
            "metric": "year_count",
            "value": len({row["year"] for row in document_rows}),
        },
        {"metric": "document_count", "value": len(document_rows)},
        {
            "metric": "separate_document_count",
            "value": sum(row["scope"] == "separate" for row in document_rows),
        },
        {
            "metric": "consolidated_document_count",
            "value": sum(
                row["scope"] == "consolidated" for row in document_rows
            ),
        },
        {
            "metric": "unknown_scope_document_count",
            "value": sum(row["scope"] == "unknown" for row in document_rows),
        },
        {
            "metric": "total_pages",
            "value": sum(row["page_count"] for row in document_rows),
        },
        {"metric": "total_html_tables", "value": len(table_inventory)},
        {"metric": "question_count", "value": len(question_rows)},
        {
            "metric": "relevant_table_reference_count",
            "value": total_references,
        },
        {"metric": "anomaly_count", "value": len(anomalies)},
    ]


def build_markdown_report(
    dataset_summary: list[dict[str, Any]],
    hypothesis_rows: list[dict[str, Any]],
    total_references: int,
) -> str:
    lines = [
        "# ViFinQA Dataset Audit",
        "",
        "## Source paths",
        "",
        f"- Financial statements: `{DATASET_ROOT}`",
        f"- Questions: `{QUESTIONS_FILE}`",
        f"- Output: `{OUTPUT_DIR}`",
        "",
        "## Dataset summary",
        "",
    ]

    for item in dataset_summary:
        value = item["value"]
        formatted = f"{value:,}" if isinstance(value, int) else str(value)
        lines.append(f"- {item['metric']}: **{formatted}**")

    lines.extend(["", "## Table-ID hypothesis audit", ""])

    if total_references == 0:
        lines.extend(
            [
                "The supplied question file contains no `relevant_tables` labels.",
                "Therefore the origin of numeric table IDs is **not testable** "
                "from this file.",
                "",
                "The public ViFinQA questions contain only `id` and `question`. "
                "Point `QUESTIONS_FILE` to an enriched gold/dev JSONL file to "
                "audit table IDs.",
            ]
        )
    else:
        lines.extend(
            [
                "| Rank | Hypothesis | Exact | Unique | Collision |",
                "|---:|---|---:|---:|---:|",
            ]
        )
        for rank, row in enumerate(hypothesis_rows, start=1):
            lines.append(
                f"| {rank} | {row['hypothesis']} | "
                f"{row['exact_match_rate']:.4%} | "
                f"{row['unique_match_rate']:.4%} | "
                f"{row['collision_rate']:.4%} |"
            )

        best = hypothesis_rows[0]
        lines.extend(["", "## Automated conclusion", ""])
        passed = (
            best["pass_exact_99_9"]
            and best["pass_unique_99_5"]
            and best["pass_zero_collisions"]
        )
        if passed:
            lines.append(
                "The most strongly supported deterministic rule is "
                f"**{best['hypothesis']}**."
            )
        else:
            lines.append(
                "No tested hypothesis passed all lock thresholds. "
                f"Best current candidate: **{best['hypothesis']}** "
                f"({best['exact_match_rate']:.4%} exact, "
                f"{best['unique_match_rate']:.4%} unique, "
                f"{best['collision_rate']:.4%} collision)."
            )

    lines.extend(
        [
            "",
            "## Model implication",
            "",
            "Do not train the model to predict the raw numeric table ID. "
            "Retrieve or segment the correct table candidate, then emit its ID "
            "through the verified deterministic preprocessing rule.",
            "",
            "If `start_char_0` is verified, calculate it on the original "
            "extracted text before trimming, whitespace normalization, OCR "
            "correction, or HTML reserialization.",
            "",
            "## DataFrame variables",
            "",
            "`df1`, `df2`, and similar names are local evidence aliases. "
            "They are not table IDs and do not have a fixed financial meaning.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    dataset_root = DATASET_ROOT.expanduser().resolve()
    questions_path = QUESTIONS_FILE.expanduser().resolve()
    output_dir = OUTPUT_DIR.expanduser().resolve()

    print("Configured paths")
    print(f"  financial statements: {dataset_root}")
    print(f"  questions:            {questions_path}")
    print(f"  audit output:         {output_dir}")

    if not dataset_root.is_dir():
        raise FileNotFoundError(
            "Financial-statements directory not found. "
            f"Configured path: {dataset_root}. "
            "Edit data/process/project_paths.py if the dataset is elsewhere."
        )
    if not questions_path.is_file():
        raise FileNotFoundError(
            "Questions file not found. "
            f"Configured path: {questions_path}. "
            "Edit data/process/project_paths.py if the file is elsewhere."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    (
        document_rows,
        table_inventory,
        tables_by_document,
        anomalies,
    ) = scan_corpus(dataset_root)

    (
        question_rows,
        reference_rows,
        question_statistics,
        total_references,
    ) = analyze_questions(
        questions_path=questions_path,
        tables_by_document=tables_by_document,
        anomalies=anomalies,
    )

    # Reconstruct hypothesis summary from detailed reference rows.
    hypothesis_rows: list[dict[str, Any]] = []
    if total_references > 0:
        for hypothesis in HYPOTHESES:
            match_key = f"{hypothesis}_match_count"
            counts = [int(row.get(match_key, 0)) for row in reference_rows]
            exact = sum(count >= 1 for count in counts)
            unique = sum(count == 1 for count in counts)
            collisions = sum(count > 1 for count in counts)
            exact_rate = exact / total_references
            unique_rate = unique / total_references
            collision_rate = collisions / total_references
            hypothesis_rows.append(
                {
                    "hypothesis": hypothesis,
                    "total_references": total_references,
                    "exact_match_references": exact,
                    "exact_match_rate": exact_rate,
                    "unique_match_references": unique,
                    "unique_match_rate": unique_rate,
                    "collision_references": collisions,
                    "collision_rate": collision_rate,
                    "pass_exact_99_9": exact_rate >= 0.999,
                    "pass_unique_99_5": unique_rate >= 0.995,
                    "pass_zero_collisions": collisions == 0,
                }
            )

        hypothesis_rows.sort(
            key=lambda row: (
                row["exact_match_rate"],
                row["unique_match_rate"],
                -row["collision_rate"],
            ),
            reverse=True,
        )

    dataset_summary = build_dataset_summary(
        document_rows=document_rows,
        table_inventory=table_inventory,
        question_rows=question_rows,
        total_references=total_references,
        anomalies=anomalies,
    )

    write_csv(output_dir / "dataset_summary.csv", dataset_summary)
    write_csv(output_dir / "document_statistics.csv", document_rows)
    write_csv(output_dir / "table_inventory.csv", table_inventory)
    write_csv(output_dir / "question_records.csv", question_rows)
    write_csv(output_dir / "question_statistics.csv", question_statistics)
    write_csv(output_dir / "reference_audit.csv", reference_rows)
    write_csv(
        output_dir / "table_id_hypothesis_summary.csv",
        hypothesis_rows,
        fieldnames=HYPOTHESIS_FIELDS,
    )
    write_csv(
        output_dir / "anomalies.csv",
        anomalies,
        fieldnames=["type", "key", "detail"],
    )

    report = build_markdown_report(
        dataset_summary=dataset_summary,
        hypothesis_rows=hypothesis_rows,
        total_references=total_references,
    )
    (output_dir / "statistics_report.md").write_text(
        report,
        encoding="utf-8",
    )

    print("\nAudit complete")
    print(f"  reports scanned:       {len(document_rows):,}")
    print(f"  HTML tables scanned:   {len(table_inventory):,}")
    print(f"  questions scanned:     {len(question_rows):,}")
    print(f"  gold table references: {total_references:,}")
    if total_references == 0:
        print("  table-ID hypotheses:   not tested (no relevant_tables labels)")
    print(f"  output directory:      {output_dir}")


if __name__ == "__main__":
    main()
