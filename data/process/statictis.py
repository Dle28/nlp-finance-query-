#!/usr/bin/env python3
"""Detailed ViFinQA dataset statistics and table-id origin audit.

The script scans:
  financial_statements/{ticker}/{year}/{document}/..._extracted.txt
and joins them against questions.jsonl.

It tests whether a numeric relevant_tables suffix is:
  - local table order (0/1 based)
  - start character offset (0/1 based)
  - UTF-8 byte offset (0/1 based)
  - source line number (0/1 based)
  - page number
  - order on page
  - global table order under deterministic path sorting

It also analyzes:
  - ticker/year/document/scope coverage
  - pages and HTML table counts
  - evidence variable names such as df1/df2
  - pandas_query usage of df1, df, dfs and result
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

TABLE_RE = re.compile(r"<table\b.*?</table>", flags=re.I | re.S)
PAGE_RE = re.compile(r"===== PAGE (\d+) =====")
DF_VAR_RE = re.compile(r"\bdf\d+\b")
SCOPE_RE = re.compile(r"_(consolidated|separate)$", flags=re.I)

def args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-root", type=Path, required=True)
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, default=Path("audit_output"))
    return p.parse_args()

def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL line {line_no}: {exc}") from exc

def infer_document_id(path: Path) -> str:
    name = path.name
    if name.endswith("_extracted.txt"):
        return name[:-len("_extracted.txt")]
    # Usually the parent directory is already the canonical document ID.
    return path.parent.name if "_financial_statements_" in path.parent.name else path.stem

def parse_ref(ref: str) -> tuple[str, int]:
    document_id, sep, raw = str(ref).rpartition("|")
    if not sep:
        raise ValueError(f"Invalid table ref: {ref!r}")
    raw = raw.removeprefix("table_")
    return document_id, int(raw)

def safe_preview(html: str, limit: int = 240) -> str:
    value = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", value).strip()[:limit]

def document_meta(path: Path, dataset_root: Path) -> dict[str, Any]:
    rel = path.relative_to(dataset_root)
    parts = rel.parts
    ticker = parts[0] if len(parts) >= 1 else ""
    year = parts[1] if len(parts) >= 2 else ""
    document_id = infer_document_id(path)
    scope_match = SCOPE_RE.search(document_id)
    scope = scope_match.group(1).lower() if scope_match else "unknown"
    return {
        "ticker": ticker,
        "year": year,
        "document_id": document_id,
        "scope": scope,
        "source_path": str(path),
    }

def parse_document(path: Path, dataset_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    meta = document_meta(path, dataset_root)

    page_matches = list(PAGE_RE.finditer(text))
    page_starts = [m.start() for m in page_matches]
    page_numbers = [int(m.group(1)) for m in page_matches]

    def page_for(pos: int) -> int | None:
        i = bisect.bisect_right(page_starts, pos) - 1
        return page_numbers[i] if i >= 0 else None

    per_page = Counter()
    tables: list[dict[str, Any]] = []
    for order_1, match in enumerate(TABLE_RE.finditer(text), 1):
        start = match.start()
        page = page_for(start)
        per_page[page] += 1
        html = match.group(0)
        tables.append({
            **meta,
            "local_order_0": order_1 - 1,
            "local_order_1": order_1,
            "page_no": page,
            "page_order_0": per_page[page] - 1,
            "page_order_1": per_page[page],
            "start_char_0": start,
            "start_char_1": start + 1,
            "start_byte_0": len(text[:start].encode("utf-8")),
            "start_byte_1": len(text[:start].encode("utf-8")) + 1,
            "start_line_0": text.count("\n", 0, start),
            "start_line_1": text.count("\n", 0, start) + 1,
            "html_length_chars": len(html),
            "table_sha1": hashlib.sha1(html.encode("utf-8")).hexdigest(),
            "preview": safe_preview(html),
        })

    doc = {
        **meta,
        "file_size_bytes": path.stat().st_size,
        "character_count": len(text),
        "utf8_byte_count": len(text.encode("utf-8")),
        "page_count": len(page_matches),
        "table_count": len(tables),
        "first_table_offset": tables[0]["start_char_0"] if tables else None,
        "last_table_offset": tables[-1]["start_char_0"] if tables else None,
    }
    return doc, tables

def descriptive(values: list[int | float]) -> dict[str, Any]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(clean),
        "min": min(clean),
        "median": median(clean),
        "mean": mean(clean),
        "max": max(clean),
    }

def main() -> None:
    cfg = args()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    report_paths = sorted(cfg.dataset_root.rglob("*_extracted.txt"))
    if not report_paths:
        report_paths = sorted(cfg.dataset_root.rglob("*.txt"))
    if not report_paths:
        raise FileNotFoundError(f"No extracted reports found under {cfg.dataset_root}")

    docs: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    by_doc: dict[str, list[dict[str, Any]]] = {}
    duplicate_docs: defaultdict[str, list[str]] = defaultdict(list)

    global_order = 0
    for path in report_paths:
        doc, tables = parse_document(path, cfg.dataset_root)
        duplicate_docs[doc["document_id"]].append(str(path))
        for row in tables:
            row["global_order_0"] = global_order
            row["global_order_1"] = global_order + 1
            global_order += 1
        docs.append(doc)
        inventory.extend(tables)
        if doc["document_id"] not in by_doc:
            by_doc[doc["document_id"]] = tables

    hypotheses = {
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

    q_rows: list[dict[str, Any]] = []
    ref_audit: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    exact = Counter()
    unique = Counter()
    total_refs = 0

    field_presence = Counter()
    evidence_vars = Counter()
    query_patterns = Counter()
    refs_per_question: list[int] = []
    docs_per_question: list[int] = []
    evidence_per_question: list[int] = []

    for q_index, q in enumerate(iter_jsonl(cfg.questions), 1):
        for field in q:
            field_presence[field] += 1

        refs = list(q.get("relevant_tables") or [])
        relevant_docs = list(q.get("relevant_docs") or [])
        evidence = list(q.get("evidence") or [])
        query = str(q.get("pandas_query") or "")

        refs_per_question.append(len(refs))
        docs_per_question.append(len(relevant_docs))
        evidence_per_question.append(len(evidence))

        record_vars = []
        for item in evidence:
            if isinstance(item, dict) and item.get("variable"):
                variable = str(item["variable"])
                evidence_vars[variable] += 1
                record_vars.append(variable)

        found_vars = sorted(set(DF_VAR_RE.findall(query)))
        if found_vars:
            query_patterns["uses_dfN"] += 1
        if re.search(r"\bdf\b", query):
            query_patterns["uses_df"] += 1
        if re.search(r"\bdfs\b", query):
            query_patterns["uses_dfs"] += 1
        if re.search(r"\bresult\s*=", query):
            query_patterns["assigns_result"] += 1
        if not query:
            query_patterns["missing_query"] += 1

        q_rows.append({
            "question_index": q_index,
            "question_id": q.get("id", ""),
            "question": q.get("question", ""),
            "relevant_doc_count": len(relevant_docs),
            "relevant_table_count": len(refs),
            "evidence_count": len(evidence),
            "evidence_variables": ";".join(record_vars),
            "query_df_variables": ";".join(found_vars),
            "query_uses_df": bool(re.search(r"\bdf\b", query)),
            "query_uses_dfs": bool(re.search(r"\bdfs\b", query)),
            "query_assigns_result": bool(re.search(r"\bresult\s*=", query)),
            "query_length": len(query),
        })

        for ref in refs:
            total_refs += 1
            try:
                document_id, table_id = parse_ref(str(ref))
            except Exception as exc:
                anomalies.append({"type": "invalid_reference", "key": str(ref), "detail": str(exc)})
                continue

            candidates = by_doc.get(document_id)
            if candidates is None:
                anomalies.append({"type": "document_not_found", "key": str(ref), "detail": document_id})
                continue

            row: dict[str, Any] = {
                "question_index": q_index,
                "question_id": q.get("id", ""),
                "reference": str(ref),
                "document_id": document_id,
                "table_id": table_id,
                "document_table_count": len(candidates),
            }
            for h_name, field in hypotheses.items():
                matches = [c for c in candidates if c.get(field) == table_id]
                row[f"{h_name}_match_count"] = len(matches)
                row[f"{h_name}_orders_1"] = ";".join(str(c["local_order_1"]) for c in matches)
                if matches:
                    exact[h_name] += 1
                if len(matches) == 1:
                    unique[h_name] += 1
            ref_audit.append(row)

    for document_id, paths in duplicate_docs.items():
        if len(paths) > 1:
            anomalies.append({
                "type": "duplicate_document_id",
                "key": document_id,
                "detail": " | ".join(paths),
            })

    hypothesis_rows = []
    for h_name in hypotheses:
        exact_rate = exact[h_name] / total_refs if total_refs else 0.0
        unique_rate = unique[h_name] / total_refs if total_refs else 0.0
        hypothesis_rows.append({
            "hypothesis": h_name,
            "total_references": total_refs,
            "exact_match_references": exact[h_name],
            "exact_match_rate": exact_rate,
            "unique_match_references": unique[h_name],
            "unique_match_rate": unique_rate,
            "pass_exact_99_9": exact_rate >= 0.999,
            "pass_unique_99_5": unique_rate >= 0.995,
        })
    hypothesis_rows.sort(key=lambda r: (r["exact_match_rate"], r["unique_match_rate"]), reverse=True)

    dataset_summary = [
        {"metric": "ticker_count", "value": len({d["ticker"] for d in docs})},
        {"metric": "year_count", "value": len({d["year"] for d in docs})},
        {"metric": "document_count", "value": len(docs)},
        {"metric": "separate_document_count", "value": sum(d["scope"] == "separate" for d in docs)},
        {"metric": "consolidated_document_count", "value": sum(d["scope"] == "consolidated" for d in docs)},
        {"metric": "unknown_scope_document_count", "value": sum(d["scope"] == "unknown" for d in docs)},
        {"metric": "total_pages", "value": sum(d["page_count"] for d in docs)},
        {"metric": "total_html_tables", "value": len(inventory)},
        {"metric": "question_count", "value": len(q_rows)},
        {"metric": "relevant_table_reference_count", "value": total_refs},
        {"metric": "anomaly_count", "value": len(anomalies)},
    ]

    question_stats = []
    for name, values in [
        ("relevant_tables_per_question", refs_per_question),
        ("relevant_docs_per_question", docs_per_question),
        ("evidence_items_per_question", evidence_per_question),
        ("query_length", [r["query_length"] for r in q_rows]),
    ]:
        stats = descriptive(values)
        question_stats.append({"statistic": name, **stats})

    for field, count in sorted(field_presence.items()):
        question_stats.append({
            "statistic": f"field_presence::{field}",
            "count": count,
            "min": None, "median": None, "mean": None, "max": None,
        })
    for variable, count in sorted(evidence_vars.items()):
        question_stats.append({
            "statistic": f"evidence_variable::{variable}",
            "count": count,
            "min": None, "median": None, "mean": None, "max": None,
        })
    for pattern, count in sorted(query_patterns.items()):
        question_stats.append({
            "statistic": f"query_pattern::{pattern}",
            "count": count,
            "min": None, "median": None, "mean": None, "max": None,
        })

    write_csv(cfg.output_dir / "dataset_summary.csv", dataset_summary)
    write_csv(cfg.output_dir / "document_statistics.csv", docs)
    write_csv(cfg.output_dir / "table_inventory.csv", inventory)
    write_csv(cfg.output_dir / "question_records.csv", q_rows)
    write_csv(cfg.output_dir / "question_statistics.csv", question_stats)
    write_csv(cfg.output_dir / "reference_audit.csv", ref_audit)
    write_csv(cfg.output_dir / "table_id_hypothesis_summary.csv", hypothesis_rows)
    write_csv(cfg.output_dir / "anomalies.csv", anomalies, ["type", "key", "detail"])

    best = hypothesis_rows[0] if hypothesis_rows else None
    md = [
        "# ViFinQA Detailed Dataset Statistics",
        "",
        "## Dataset summary",
        "",
    ]
    for item in dataset_summary:
        md.append(f"- {item['metric']}: **{item['value']:,}**")
    md += [
        "",
        "## Table-ID hypothesis ranking",
        "",
        "| Rank | Hypothesis | Exact match | Unique match |",
        "|---:|---|---:|---:|",
    ]
    for rank, row in enumerate(hypothesis_rows, 1):
        md.append(
            f"| {rank} | {row['hypothesis']} | "
            f"{row['exact_match_rate']:.4%} | {row['unique_match_rate']:.4%} |"
        )

    md += ["", "## Automated conclusion", ""]
    if best and best["pass_exact_99_9"] and best["pass_unique_99_5"]:
        md.append(
            f"The deterministic table-id origin is strongly supported as **{best['hypothesis']}**."
        )
    elif best:
        md.append(
            f"No hypothesis passed the lock threshold. Best candidate is **{best['hypothesis']}** "
            f"with {best['exact_match_rate']:.4%} exact and "
            f"{best['unique_match_rate']:.4%} unique matching."
        )
    else:
        md.append("No relevant table references were available.")

    md += [
        "",
        "## Model architecture implication",
        "",
        "The model should retrieve/segment a table candidate, not predict the raw numeric ID.",
        "A deterministic mapping layer must emit the table ID using the verified preprocessing rule.",
        "If `start_char_0` wins, compute it on the unmodified extracted text.",
        "",
        "## DataFrame variable implication",
        "",
        "`df1`, `df2`, ... are local aliases from evidence metadata.",
        "`df` is the one-table alias in the current runtime.",
        "`dfs` is the multi-table dictionary keyed by table reference.",
        "`result` is the required final scalar in the current runtime.",
    ]
    (cfg.output_dir / "statistics_report.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Audit complete: {cfg.output_dir.resolve()}")

if __name__ == "__main__":
    main()