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


SEMANTIC_RERANK_SCHEMA_VERSION = 2
SEMANTIC_INPUT_RENDERER_VERSION = 2


def compact_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _column_labels(context: Mapping[str, Any]) -> str:
    headers = (context.get("canonical_headers") or {}).get("columns") or []
    labels = [
        compact_text(column.get("source_label"), 28)
        for column in headers
        if compact_text(column.get("source_label"), 28)
    ]
    return " | ".join(labels[:6])


def exact_source_row(candidate: Mapping[str, Any], table: Mapping[str, Any]) -> str:
    """Return a bounded, explicitly cell-indexed V2 row or an empty string.

    Semantic scoring is allowed only when this returns a row.  The bounded
    representation keeps the metric label and every likely value cell inside
    the cross-encoder window; an ellipsis is an explicit truncation marker,
    never a synthesized table value.
    """
    rows = table.get("rows") or []
    row_index = None
    # V3 candidates predate the local reviewer and keep their raw table
    # coordinate under these retrieval fields rather than under a later
    # ``structure_validation`` object.  Accept one only if its stored
    # evidence window reproduces the V2 row exactly; this prevents an index
    # from becoming a loose, unverified pointer after table reconstruction.
    evidence_window = candidate.get("evidence_window") or []
    for key in ("value_row_index", "best_row_index", "anchor_row_index"):
        candidate_index = candidate.get(key)
        if not isinstance(candidate_index, int) or not 0 <= candidate_index < len(rows):
            continue
        expected = next(
            (
                entry.get("row")
                for entry in evidence_window
                if isinstance(entry, Mapping) and entry.get("index") == candidate_index
            ),
            None,
        )
        if isinstance(expected, list) and [str(cell) for cell in expected] == [
            str(cell) for cell in rows[candidate_index]
        ]:
            row_index = candidate_index
            break
    if row_index is None:
        return ""
    row = rows[row_index]
    # In statement tables the descriptive metric is not always c0 (the first
    # cell can be a line code). Preserve the first substantive text label,
    # retain numeric cells, and make all other verbose note cells short and
    # visibly truncated. This keeps the actual metric/value relation within
    # the model's fixed context window without inventing a row summary.
    label_index = next(
        (
            index
            for index, cell in enumerate(row)
            if re.search(r"[A-Za-zÀ-ỹà-ỹĐđ]", str(cell))
        ),
        None,
    )
    cells: list[str] = []
    for column_index, cell in enumerate(row):
        normalized = compact_text(cell, 10_000)
        numeric_like = bool(re.fullmatch(r"[\s()\-+\d.,%/]+", normalized))
        limit = 120 if column_index == label_index else 72 if numeric_like else 20
        cells.append(f"c{column_index}={compact_text(cell, limit)}")
    return " | ".join(cells)


def semantic_candidate_input(
    question: str,
    candidate: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    """Create bounded, source-only text for one cross-encoder pair.

    The input contains no V3 projected evidence.  It is built only from the
    immutable V2 source row and canonical context that is itself traceable to
    raw-header cells, so a stale projection cannot influence the score.
    """
    source_row = exact_source_row(candidate, table)
    if not source_row:
        return ""
    trace = context.get("context_trace") or table.get("context_trace") or {}
    function = context.get("table_function") or table.get("table_function") or {}
    function_name = compact_text(
        function.get("label") if isinstance(function, Mapping) else function,
        120,
    )
    source_title = compact_text(str(trace.get("source_title") or "").replace("□", " "), 70)
    unit_labels = trace.get("unit_labels") or []
    units = " | ".join(compact_text(value, 80) for value in unit_labels if compact_text(value, 80))
    # Put the question and the attributable V2 row first.  Cross-encoders
    # impose a token limit; headings, titles and metadata remain useful but
    # must not push that exact row out of the model window.
    lines = [
        f"Câu hỏi: {compact_text(question, 280)}",
        f"Dòng nguồn exact (V2): {source_row}",
        f"Cột nguồn: {_column_labels(context)}",
        f"Chức năng bảng: {compact_text(function_name, 80)}",
        f"Đơn vị: {compact_text(table.get('unit_hint'), 60) or compact_text(units, 60)}",
        f"Nguồn: {source_title}",
    ]
    return "\n".join(line for line in lines if not line.endswith(": "))


def semantic_input_digest(question: str, candidate_input: str) -> str:
    payload = json.dumps(
        {"question": str(question), "candidate_input": str(candidate_input)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
