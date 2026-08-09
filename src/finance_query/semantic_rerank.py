"""Auditable semantic inputs for review-bundle candidate reranking.

This module never extracts an answer.  It turns an immutable candidate table
and its canonical context into a compact model input, while retaining an input
digest that lets reviewers trace every score back to the exact source fields.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


SEMANTIC_RERANK_SCHEMA_VERSION = 1


def compact_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _column_labels(context: Mapping[str, Any]) -> str:
    headers = (context.get("canonical_headers") or {}).get("columns") or []
    labels = [
        compact_text(column.get("source_label"), 100)
        for column in headers
        if compact_text(column.get("source_label"), 100)
    ]
    return " | ".join(labels[:12])


def _source_row(candidate: Mapping[str, Any], table: Mapping[str, Any]) -> str:
    validation = candidate.get("structure_validation") or {}
    row_index = validation.get("row_index")
    rows = table.get("rows") or []
    if not isinstance(row_index, int) or not 0 <= row_index < len(rows):
        return ""
    return compact_text(" | ".join(str(cell) for cell in rows[row_index]), 900)


def semantic_candidate_input(
    question: str,
    candidate: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    """Create bounded, source-only text for one cross-encoder pair.

    `direct_evidence` is retained because it contains the projected source row
    and headers; the exact V2 row is also inserted independently so a malformed
    projection cannot be the only model-visible evidence.
    """
    trace = context.get("context_trace") or table.get("context_trace") or {}
    function = context.get("table_function") or table.get("table_function") or {}
    function_name = compact_text(
        function.get("label") if isinstance(function, Mapping) else function,
        120,
    )
    source_title = compact_text(trace.get("source_title"), 420)
    unit_labels = trace.get("unit_labels") or []
    units = " | ".join(compact_text(value, 80) for value in unit_labels if compact_text(value, 80))
    lines = [
        f"Câu hỏi: {compact_text(question, 700)}",
        f"Nguồn: {source_title}",
        f"Chức năng bảng: {function_name}",
        f"Đơn vị: {compact_text(table.get('unit_hint'), 80) or units}",
        f"Cột nguồn: {_column_labels(context)}",
        f"Dòng nguồn exact: {_source_row(candidate, table)}",
        f"Evidence source: {compact_text(candidate.get('direct_evidence'), 1200)}",
    ]
    return "\n".join(line for line in lines if not line.endswith(": "))


def semantic_input_digest(question: str, candidate_input: str) -> str:
    payload = json.dumps(
        {"question": str(question), "candidate_input": str(candidate_input)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

