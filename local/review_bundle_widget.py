#!/usr/bin/env python3
"""Local-only human review UI for an offline ViFinQA review bundle.

No FAISS, E5, SQLite retrieval, corpus rebuild, or GPU is used here. The widget
renders candidate summaries/direct evidence already exported on Kaggle, then
opens aligned source rows only when the human needs them.
"""

from __future__ import annotations

import argparse
import html
import importlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

# `%run` re-executes this file but normally leaves imported project modules in
# ``sys.modules``. Reload them so a running notebook never combines a new
# widget with an older operand-matcher signature or table-structure contract.
import finance_query.financial_metrics as financial_metrics  # noqa: E402
import finance_query.report_segments as report_segments  # noqa: E402
import finance_query.table_structure as table_structure  # noqa: E402

financial_metrics = importlib.reload(financial_metrics)
report_segments = importlib.reload(report_segments)
table_structure = importlib.reload(table_structure)

fold_text = financial_metrics.fold_text
formula_is_multi_operand = financial_metrics.formula_is_multi_operand
infer_formula_spec = financial_metrics.infer_formula_spec
operand_match_score = financial_metrics.operand_match_score
validate_structure_sidecar = table_structure.validate_structure_sidecar
validate_report_segment_sidecar = report_segments.validate_report_segment_sidecar

try:
    import ipywidgets as widgets
    from IPython.display import clear_output, display
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Local review requires ipywidgets + IPython/Jupyter.") from exc


TOKEN_RE = re.compile(r"[\w%]+", re.UNICODE)
NUMERIC_RE = re.compile(r"^[\s()\-+\d.,%/]+$")
HTML_TAG_RE = re.compile(r"<[^>]+>")
PAGE_MARKER_RE = re.compile(r"=====\s*PAGE\s*\d+\s*=====", re.IGNORECASE)
SOURCE_GRID_V2_LABEL = "Bảng nguồn đã tái dựng (V2)"
EVIDENCE_CONTEXT_V3_LABEL = "Ngữ cảnh evidence chuẩn hóa (V3)"

STOPWORDS = {
    "cua", "của", "la", "là", "bao", "nhieu", "nhiêu", "nam", "năm",
    "cong", "công", "ty", "vao", "vào", "cuoi", "cuối", "dau", "đầu",
    "tai", "tại", "trong", "dong", "đồng", "trieu", "triệu", "ty", "tỷ",
    "ngay", "ngày", "thang", "tháng", "den", "đến", "mot", "một",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, default=None)
    parser.add_argument("--assistant-reviews", type=Path, default=None)
    parser.add_argument("--queue", type=Path, default=None)
    parser.add_argument(
        "--table-structure",
        type=Path,
        default=None,
        help=(
            "Optional Source Grid V2 sidecar (Bảng nguồn đã tái dựng). Defaults to "
            "<bundle-dir>/tables_structured_v2.jsonl when present."
        ),
    )
    parser.add_argument(
        "--report-segments",
        type=Path,
        default=None,
        help=(
            "Optional hash-bound report_segments_v1.jsonl sidecar. It provides "
            "compact source headings/context only and never replaces raw evidence."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/labels/retriever_verified_60.jsonl"),
    )
    parser.add_argument("--preview-rows", type=int, default=10)
    parser.add_argument("--start-id", type=int, default=None)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL: {path}:{line_number}") from exc
    return rows


def atomic_write(path: Path, rows_by_id: dict[int, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for qid in sorted(rows_by_id):
            file.write(json.dumps(rows_by_id[qid], ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def annotation_needs_structure_refresh(review: dict[str, Any] | None) -> bool:
    """Legacy positive labels remain auditable but must be rechecked on V2."""
    if not review or not review.get("positive_table_uids"):
        return False
    validation = review.get("structure_validation") or {}
    return not bool(validation.get("complete"))


def normalize_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(text)
        if len(token) >= 2 and token.casefold() not in STOPWORDS
    }


def is_numeric(text: str) -> bool:
    value = str(text).strip()
    return bool(value and NUMERIC_RE.fullmatch(value) and any(char.isdigit() for char in value))


def compact_text(value: Any, limit: int | None = None) -> str:
    """Render exported source text readably without adding semantic content."""
    text = html.unescape(str(value or ""))
    text = HTML_TAG_RE.sub(" ", text)
    text = PAGE_MARKER_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n|-")
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def report_context_text(table: dict[str, Any], candidate: dict[str, Any]) -> str:
    """Prefer the projected heading and keep raw report context as fallback only."""
    normalized_heading = compact_text(
        (table.get("report_segment") or {}).get("source_heading"), 420
    )
    if normalized_heading:
        return normalized_heading
    heading = compact_text(candidate.get("context_heading"), 420)
    raw = str(table.get("context_before") or "")
    # The text after the last table is the heading exported by V3. Keeping this
    # boundary avoids showing hundreds of OCR/HTML cells from the prior table.
    tail = raw.rsplit("</table>", 1)[-1]
    source_heading = compact_text(PAGE_MARKER_RE.split(tail)[-1], 420)
    # V3.0 truncated some headings from the left (for example, "y Cổ phần").
    # Prefer the local raw title only for that recognisable truncation pattern.
    if source_heading and (not heading or heading[0].islower()):
        return source_heading
    return heading or source_heading or "Không có tiêu đề/ngữ cảnh nguồn đã lưu."


def evidence_parts(direct_evidence: Any) -> list[tuple[str, str]]:
    """Split V3 evidence labels while preserving each exported source string."""
    labels = {
        "TABLE": "Ngữ cảnh bảng",
        "COLUMNS": "Dòng tiêu đề/cột",
        "VALUE": "Dòng giá trị",
        "ANCHOR": "Dòng neo",
    }
    parts: list[tuple[str, str]] = []
    for raw_part in re.split(r"\s*\|\|\s*", str(direct_evidence or "")):
        if not raw_part.strip():
            continue
        prefix, separator, value = raw_part.partition(":")
        key = prefix.strip().upper()
        if separator and key in labels:
            parts.append((labels[key], compact_text(value)))
        else:
            parts.append(("Evidence nguồn", compact_text(raw_part)))
    return parts


def evidence_paragraph_html(candidate: dict[str, Any]) -> str:
    parts = evidence_parts(candidate.get("direct_evidence"))
    if not parts:
        return "<div style='color:#777'><i>Không có evidence đã chiếu.</i></div>"

    blocks = []
    for label, value in parts:
        is_row = label in {"Dòng tiêu đề/cột", "Dòng giá trị", "Dòng neo"}
        background = "#eef8f0" if is_row else "#f7f9fc"
        border = "#2f855a" if is_row else "#7b8794"
        blocks.append(
            f"<div style='padding:8px 10px;margin:5px 0;background:{background};"
            f"border-left:4px solid {border};border-radius:4px;line-height:1.5'>"
            f"<b>{html.escape(label)}:</b> {html.escape(value)}</div>"
        )
    return "".join(blocks)


def table_classification(table: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read deterministic V2 classification; never invent one in the UI."""
    function = table.get("table_function") or {}
    section = table.get("table_section") or {}
    return (
        {
            "kind": str(function.get("kind") or "unknown"),
            "label": str(function.get("label") or "Chưa xác định chức năng bảng"),
            "confidence": float(function.get("confidence") or 0.0),
            "specificity": str(function.get("specificity") or "unknown"),
            "matched_evidence": str(function.get("matched_evidence") or ""),
        },
        {
            "kind": str(section.get("kind") or "unknown"),
            "label": str(section.get("label") or "Chưa xác định phần bảng"),
            "confidence": float(section.get("confidence") or 0.0),
            "matched_evidence": str(section.get("matched_evidence") or ""),
        },
    )


def table_purpose(table: dict[str, Any]) -> dict[str, Any]:
    purpose = table.get("table_purpose") or {}
    return {
        "kind": str(purpose.get("kind") or "unknown"),
        "label": str(purpose.get("label") or "Chưa xác định cách sử dụng bảng"),
        "confidence": float(purpose.get("confidence") or 0.0),
    }


def has_verified_structure(table: dict[str, Any]) -> bool:
    """True only for a grid reconstructed and UID-verified from raw HTML."""
    quality = table.get("structure_quality") or {}
    return str(quality.get("status") or "") == "reconstructed_from_raw_html"


def context_trace(table: dict[str, Any]) -> dict[str, Any]:
    trace = table.get("context_trace") or {}
    segment = table.get("report_segment") or {}
    topic = trace.get("topic") or {}
    normalized_heading = compact_text(segment.get("source_heading"), 420)
    parent_heading = compact_text(segment.get("source_parent_heading"), 220)
    source_title = normalized_heading or compact_text(trace.get("source_title"), 420)
    if parent_heading and source_title and parent_heading.casefold() not in source_title.casefold():
        source_title = parent_heading + " › " + source_title
    return {
        "source_title": compact_text(
            source_title, 420
        ),
        "topic_label": compact_text(
            (topic.get("label") if isinstance(topic, dict) else "")
            or trace.get("topic_label"),
            240,
        ),
        "period_labels": [str(value) for value in segment.get("period_labels") or trace.get("period_labels") or []],
        "unit_labels": [str(value) for value in segment.get("unit_labels") or trace.get("unit_labels") or []],
        "summary": compact_text(segment.get("compact_descriptor") or trace.get("summary"), 520),
    }


def requested_section(question: str) -> tuple[str, str] | None:
    text = compact_text(question).casefold()
    if any(term in text for term in ("nợ ", "nợ ngắn", "nợ dài", "phải trả")):
        return "liability", "Nợ phải trả"
    if "tài sản" in text:
        return "asset", "Tài sản"
    if "vốn chủ" in text:
        return "equity", "Vốn chủ sở hữu"
    if "lưu chuyển tiền" in text:
        return "cash_flow", "Lưu chuyển tiền tệ"
    if "doanh thu" in text:
        return "revenue", "Doanh thu"
    if "chi phí" in text:
        return "expense", "Chi phí"
    return None


def relevance_assessment(
    table: dict[str, Any],
    question: str,
    formula: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Fail closed on a known accounting-side mismatch, but allow overrides."""
    if formula and candidate:
        matches = candidate_operand_matches(formula, candidate, table)
        if matches:
            return {
                "status": "not_blocked",
                "label": "Có thể hỗ trợ công thức",
                "reason": "Khớp metric+kỳ cho operand: "
                + "; ".join(match["label"] for match in matches),
            }
        if formula_is_multi_operand(formula):
            return {
                "status": "needs_check",
                "label": "Chưa map vào operand",
                "reason": "Không dùng accounting-side chung để loại bảng đa operand; cần kiểm tra exact row.",
            }
    requested = requested_section(question)
    function, section = table_classification(table)
    observed = section["kind"]
    if (
        requested
        and requested[0] == "cash_flow"
        and function["kind"] == "cash_flow_statement"
    ):
        return {
            "status": "not_blocked",
            "label": "Đúng chức năng bảng",
            "reason": "Câu hỏi và table function đều là báo cáo lưu chuyển tiền tệ.",
        }
    whole_statement_support = {
        "income_statement": {"revenue", "expense"},
        "balance_sheet": {"asset", "liability", "equity"},
    }
    if (
        requested
        and requested[0] in whole_statement_support.get(function["kind"], set())
        and observed == function["kind"]
    ):
        return {
            "status": "not_blocked",
            "label": "Đúng chức năng bảng",
            "reason": (
                f"Bảng toàn phần {function['label']} có thể chứa phần "
                f"{requested[1]}; vẫn phải bind exact row."
            ),
        }
    if requested and observed != "unknown" and observed != requested[0]:
        return {
            "status": "mismatch",
            "label": "Không phù hợp",
            "reason": (
                f"Câu hỏi cần phần {requested[1]}; bảng đang là phần {section['label']}."
            ),
        }
    if requested and observed == "unknown":
        return {
            "status": "needs_check",
            "label": "Cần kiểm tra",
            "reason": f"Câu hỏi cần phần {requested[1]}, nhưng chưa xác định được phần bảng.",
        }
    return {
        "status": "not_blocked",
        "label": "Chưa thấy mâu thuẫn cấu trúc",
        "reason": "Vẫn cần kiểm tra metric, kỳ và dòng evidence chính xác.",
    }


def candidate_summary(
    candidate: dict[str, Any],
    table: dict[str, Any] | None = None,
    question: str = "",
    formula: dict[str, Any] | None = None,
) -> str:
    """A concise navigation line, grounded in V2 metadata when available."""
    parts = evidence_parts(candidate.get("direct_evidence"))
    preferred = next(
        (value for label, value in parts if label in {"Dòng giá trị", "Dòng neo"}),
        parts[-1][1] if parts else "Không có evidence",
    )
    identity = " · ".join(
        str(value)
        for value in (
            candidate.get("document_id"),
            candidate.get("report_year"),
            candidate.get("scope"),
        )
        if value not in (None, "")
    )
    if table is not None:
        function, section = table_classification(table)
        purpose = table_purpose(table)
        assessment = relevance_assessment(table, question, formula, candidate)
        function_text = f"{function['label']}; {purpose['label']}"
        if section["kind"] != "unknown":
            function_text += f", phần {section['label']}"
        if assessment["status"] == "mismatch":
            summary = f"{function_text} — {assessment['reason']}"
        elif formula and not candidate_operand_matches(formula, candidate, table):
            summary = f"{function_text} — chưa map được vào operand bắt buộc của công thức"
        else:
            summary = f"{function_text} — dòng candidate: {compact_text(preferred, 240)}"
        return f"{identity} — {summary}" if identity else summary
    return f"{identity} — {compact_text(preferred, 360)}" if identity else compact_text(preferred, 360)


def candidate_operand_matches(
    formula: dict[str, Any] | None,
    candidate: dict[str, Any],
    table: dict[str, Any],
) -> list[dict[str, Any]]:
    if not formula:
        return []
    trace = context_trace(table)
    source_rows = " ".join(
        " | ".join(str(cell) for cell in row)
        for row in table.get("rows") or []
    )
    evidence_text = " ".join(
        [
            str(candidate.get("direct_evidence") or ""),
            str(candidate.get("table_topic") or ""),
            trace["source_title"],
            source_rows,
        ]
    )
    exact_binding_heading = fold_text(
        " ".join(
            [
                str(candidate.get("context_heading") or ""),
                trace["topic_label"],
            ]
        )
    )
    matches: list[dict[str, Any]] = []
    function, _ = table_classification(table)
    for operand in formula.get("operands") or []:
        allowed_functions = {
            str(value) for value in operand.get("allowed_table_functions") or []
        }
        if allowed_functions and function["kind"] not in allowed_functions:
            continue
        column_requirements = operand.get("table_function_column_hints") or {}
        required_column_hints = [
            str(value)
            for value in column_requirements.get(function["kind"], [])
        ]
        if required_column_hints:
            folded_columns = fold_text(
                " | ".join(str(label) for label in table.get("column_labels") or [])
            )
            if not all(fold_text(hint) in folded_columns for hint in required_column_hints):
                continue
        score = operand_match_score(
            operand,
            evidence_text,
            report_year=candidate.get("report_year"),
            period_labels=trace["period_labels"],
            ticker=candidate.get("ticker"),
        )
        row_matches: list[dict[str, Any]] = []
        for row_index, row in enumerate(table.get("rows") or []):
            row_score = operand_match_score(
                operand,
                " | ".join(str(cell) for cell in row),
                report_year=candidate.get("report_year"),
                period_labels=trace["period_labels"],
                ticker=candidate.get("ticker"),
            )
            if row_score >= 0.72:
                row_matches.append(
                    {
                        "row_index": row_index,
                        "row": [str(cell) for cell in row],
                        "column_labels": [
                            str(label) for label in table.get("column_labels") or []
                        ],
                        "score": row_score,
                    }
                )
        exact_metric_in_heading = any(
            fold_text(hint) in exact_binding_heading
            for hint in operand.get("metric_hints") or []
            if fold_text(hint)
        )
        if not row_matches and score >= 0.72 and exact_metric_in_heading:
            best_index = candidate.get("best_row_index")
            source_rows = table.get("rows") or []
            if isinstance(best_index, int) and 0 <= best_index < len(source_rows):
                row_matches.append(
                    {
                        "row_index": best_index,
                        "row": [str(cell) for cell in source_rows[best_index]],
                        "column_labels": [
                            str(label) for label in table.get("column_labels") or []
                        ],
                        "score": score,
                        "binding": "context_heading_plus_value_row",
                        "source_context": trace["source_title"],
                    }
                )
        if score >= 0.72 and row_matches:
            matches.append(
                {
                    "operand_id": operand["operand_id"],
                    "label": operand["label"],
                    "score": score,
                    "source_rows": row_matches[:6],
                }
            )
    return matches


def formula_coverage(
    formula: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    *,
    selected_uids: set[str] | None = None,
) -> dict[str, Any]:
    if not formula:
        return {"complete": True, "operands": {}}
    coverage: dict[str, list[dict[str, Any]]] = {
        str(operand["operand_id"]): [] for operand in formula.get("operands") or []
    }
    for candidate in candidates:
        uid = str(candidate["internal_table_uid"])
        if selected_uids is not None and uid not in selected_uids:
            continue
        table = tables.get(uid)
        if not table:
            continue
        for match in candidate_operand_matches(formula, candidate, table):
            coverage[match["operand_id"]].append(
                {
                    "rank": int(candidate["rank"]),
                    "uid": uid,
                    "score": match["score"],
                    "source_rows": match.get("source_rows") or [],
                    "structure_validated": has_verified_structure(table),
                }
            )
    required_ids = {
        str(operand["operand_id"])
        for operand in formula.get("operands") or []
        if operand.get("required", True)
    }
    complete = bool(required_ids) and all(
        any(match.get("structure_validated") for match in coverage[operand_id])
        for operand_id in required_ids
    )
    if (
        formula.get("formula_id") == "multi_stage_selection_unresolved"
        or formula.get("execution_status") == "stage_binding_required"
    ):
        complete = False
    if formula.get("definition_status") == "ambiguous":
        complete = False
    return {"complete": complete, "operands": coverage}


def formula_context_html(
    formula: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
) -> str:
    if not formula:
        return ""
    coverage = formula_coverage(formula, candidates, tables)
    definition_status = str(formula.get("definition_status") or "unknown")
    status_color = "#b42318" if definition_status == "ambiguous" else "#a15c00" if definition_status == "review_required" else "#2f855a"
    operand_lines: list[str] = []
    by_id = {str(op["operand_id"]): op for op in formula.get("operands") or []}
    for operand_id, operand in by_id.items():
        matches = coverage["operands"].get(operand_id) or []
        ranks = ", ".join(f"#{match['rank']}" for match in matches[:6])
        validated = any(match.get("structure_validated") for match in matches)
        marker = "✓" if validated else "△" if matches else "○"
        detail = (
            f"candidate {ranks}"
            if validated
            else f"candidate {ranks}, nhưng grid chưa xác minh từ raw HTML"
            if matches
            else "chưa thấy candidate đủ khớp"
        )
        operand_lines.append(
            f"<div><b>{marker} {html.escape(str(operand['label']))}</b> — {html.escape(detail)}</div>"
        )
    notes = "".join(
        f"<div>• {html.escape(str(note))}</div>" for note in formula.get("notes") or []
    )
    stages = formula.get("stages") or []
    stage_blocks: list[str] = []
    for stage in stages:
        stage_id = str(stage.get("stage_id") or "")
        stage_operands = [
            operand
            for operand in formula.get("operands") or []
            if str(operand.get("stage_id") or "") == stage_id
        ]
        covered = sum(
            any(
                match.get("structure_validated")
                for match in coverage["operands"].get(str(operand["operand_id"])) or []
            )
            for operand in stage_operands
        )
        ranks = sorted(
            {
                int(match["rank"])
                for operand in stage_operands
                for match in coverage["operands"].get(str(operand["operand_id"])) or []
                if match.get("structure_validated")
            }
        )
        rank_text = ", ".join(f"#{rank}" for rank in ranks[:8]) or "chưa đủ evidence"
        stage_blocks.append(
            "<div style='margin:6px 0;padding:7px 9px;background:#fff;border-left:3px solid #756bb1'>"
            f"<b>{html.escape(str(stage.get('label') or stage_id))}</b> "
            f"— operand {covered}/{len(stage_operands)} · {html.escape(rank_text)}<br>"
            f"<code>{html.escape(str(stage.get('expression') or ''))}</code><br>"
            f"<span>{html.escape(str(stage.get('decision') or ''))}</span></div>"
        )
    operand_html = "".join(operand_lines)
    if stages:
        operand_html = (
            "".join(stage_blocks)
            + "<details style='margin-top:5px'><summary>Chi tiết operand theo entity/kỳ</summary>"
            + operand_html
            + "</details>"
        )
    return (
        "<div style='padding:10px 12px;margin:5px 0 9px;background:#f7f5ff;"
        "border:1px solid #d9d2f0;border-radius:7px;font-size:12px;line-height:1.55'>"
        f"<div><b>Công thức review:</b> {html.escape(str(formula['label']))} "
        f"<span style='color:{status_color}'>[{html.escape(definition_status)}]</span></div>"
        f"<div style='font-family:monospace;margin:3px 0 6px'>{html.escape(str(formula['expression']))}</div>"
        + operand_html
        + (f"<details style='margin-top:5px'><summary>Điều kiện công thức</summary>{notes}</details>" if notes else "")
        + "</div>"
    )


def focused_rows(
    rows: list[list[str]],
    question: str,
    candidate: dict[str, Any],
    limit: int,
) -> list[tuple[int, list[str]]]:
    if not rows:
        return []

    keep: set[int] = set(range(min(3, len(rows))))
    best = candidate.get("best_row_index")
    if isinstance(best, int):
        for index in range(max(0, best - 3), min(len(rows), best + 4)):
            keep.add(index)

    qtokens = normalize_tokens(question)
    scored = []
    for index, row in enumerate(rows):
        tokens = normalize_tokens(" ".join(str(cell) for cell in row))
        overlap = len(tokens & qtokens) / max(1, len(qtokens)) if qtokens else 0.0
        scored.append((overlap, index))
    scored.sort(reverse=True)

    for score, index in scored:
        if score <= 0:
            break
        keep.add(index)
        if len(keep) >= limit:
            break

    return [(index, rows[index]) for index in sorted(keep)[:limit]]


def compact_source_table_html(
    rows: list[list[str]],
    question: str,
    candidate: dict[str, Any],
    *,
    column_labels: list[str] | None = None,
    header_row_indices: list[int] | None = None,
    max_rows: int = 4,
    max_columns: int = 5,
) -> str:
    """Render a small evidence-first preview; the complete grid stays collapsed."""
    if not rows:
        return ""
    header_indices = {int(index) for index in header_row_indices or []}
    selected = [
        (index, row)
        for index, row in focused_rows(rows, question, candidate, max_rows + len(header_indices) + 2)
        if index not in header_indices
    ][:max_rows]
    if not selected:
        return ""

    width = max(len(row) for _, row in selected)
    if width <= max_columns:
        columns = list(range(width))
    else:
        # Preserve the row label and the rightmost period/total columns.
        columns = [0, *range(width - (max_columns - 1), width)]
    labels = list(column_labels or [])
    labels += [f"Cột nguồn {index + 1}" for index in range(len(labels), width)]

    header = "".join(
        "<th style='padding:5px 7px;border-bottom:1px solid #d8dee8;"
        "background:#f1f4f8;text-align:left;font-size:11px'>"
        f"{html.escape(compact_text(labels[column], 70))}</th>"
        for column in columns
    )
    body: list[str] = []
    best = candidate.get("best_row_index")
    for row_index, row in selected:
        cells = []
        for column in columns:
            value = str(row[column]) if column < len(row) else ""
            align = "right" if is_numeric(value) else "left"
            rendered = html.escape(compact_text(value, 110)) if value else "<span style='color:#b7bec8'>—</span>"
            cells.append(
                f"<td style='padding:5px 7px;border-bottom:1px solid #edf0f4;"
                f"text-align:{align};font-size:11px;vertical-align:top'>{rendered}</td>"
            )
        background = "#eef8f0" if row_index == best else "#fff"
        body.append(f"<tr style='background:{background}'>" + "".join(cells) + "</tr>")
    return (
        "<div style='max-height:230px;overflow:auto;margin-top:6px;border:1px solid #d9dee5;border-radius:6px'>"
        "<table style='border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def aligned_table_html(
    rows: list[list[str]],
    question: str,
    candidate: dict[str, Any],
    limit: int,
    *,
    column_labels: list[str] | None = None,
    header_row_indices: list[int] | None = None,
    structure_quality: dict[str, Any] | None = None,
) -> str:
    selected = focused_rows(rows, question, candidate, limit)
    if not selected:
        return "<i>No structured rows stored.</i>"

    qtokens = normalize_tokens(question)
    max_cols = max(len(row) for _, row in selected)
    labels = list(column_labels or [])[:max_cols]
    labels += [f"Cột nguồn {index + 1}" for index in range(len(labels), max_cols)]
    header_indices = {int(index) for index in header_row_indices or []}
    quality = structure_quality or {}
    status = str(quality.get("status") or "legacy_bundle_rows")
    flags = {str(flag) for flag in quality.get("flags") or []}
    if status == "reconstructed_from_raw_html":
        structure_note = (
            "<div style='padding:7px 10px;margin:4px 0 8px;background:#eef8f0;"
            "border-left:4px solid #2f855a;font-size:12px'>"
            f"<b>{SOURCE_GRID_V2_LABEL} từ raw HTML.</b> Ô trống được giữ nguyên vị trí; merged cells được mở rộng."
            "</div>"
        )
    else:
        structure_note = (
            "<div style='padding:7px 10px;margin:4px 0 8px;background:#fff4e5;"
            "border-left:4px solid #c77d20;font-size:12px'>"
            f"<b>Legacy grid.</b> Chưa có {SOURCE_GRID_V2_LABEL}; không dùng bảng này để xác nhận vị trí số liệu."
            "</div>"
        )
    if "header_not_detected" in flags:
        structure_note += (
            "<div style='padding:7px 10px;margin:4px 0 8px;background:#fff4e5;"
            "border-left:4px solid #c77d20;font-size:12px'>"
            "Không xác định được header nguồn; chỉ kiểm tra raw cells, không suy diễn tên cột.</div>"
        )

    head = (
        "<thead><tr>"
        "<th style='position:sticky;left:0;top:0;z-index:5;background:#e7edf5;"
        "padding:7px;border:1px solid #ccd4df'>row</th>"
        + "".join(
            f"<th style='position:sticky;top:0;z-index:4;background:#edf2f7;"
            f"padding:7px 10px;border:1px solid #ccd4df;min-width:"
            f"{'300px' if col == 0 else '135px'}'>{html.escape(labels[col])}</th>"
            for col in range(max_cols)
        )
        + "</tr></thead>"
    )

    body: list[str] = []
    best_index = candidate.get("best_row_index")
    for row_index, row in selected:
        if row_index in header_indices and row_index != best_index:
            continue
        padded = list(row) + [""] * (max_cols - len(row))
        row_bg = "#f2fbf3" if row_index == best_index else "#ffffff"
        cells = [
            f"<td style='position:sticky;left:0;z-index:2;background:#f7f9fb;"
            f"padding:7px;border:1px solid #dde2e8;color:#667085;text-align:center'>"
            f"r{row_index}</td>"
        ]
        for column, cell in enumerate(padded):
            text = str(cell)
            hit = bool(normalize_tokens(text) & qtokens)
            background = "#fff3cd" if hit else row_bg
            align = "right" if is_numeric(text) else "left"
            width = "min-width:300px;max-width:470px;" if column == 0 else "min-width:135px;"
            content = html.escape(text) if text else "<span style='color:#c0c6cf'>—</span>"
            cells.append(
                f"<td style='padding:7px 10px;border:1px solid #dde2e8;"
                f"vertical-align:top;background:{background};text-align:{align};"
                f"{width}overflow-wrap:anywhere;font-size:12px'>{content}</td>"
            )
        body.append("<tr>" + "".join(cells) + "</tr>")

    return (
        structure_note
        + "<div style='max-height:520px;overflow:auto;border:1px solid #d9dee5;"
        "border-radius:7px;background:white'>"
        "<table style='border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;"
        "font-variant-numeric:tabular-nums'>"
        + head + "<tbody>" + "".join(body) + "</tbody></table></div>"
    )


def machine_html(review: dict[str, Any] | None) -> str:
    if not review:
        return "<div style='color:#777'>No machine review loaded.</div>"

    status = str(review.get("consensus_status") or "unknown")
    color = {
        "machine_calibrated": "#e7f6e9",
        "machine_high_confidence": "#e7f6e9",
        "machine_provisional": "#fff8df",
        "needs_human": "#ffecec",
        "retrieval_failure": "#ffecec",
    }.get(status, "#f4f4f4")

    votes = review.get("agent_votes") or {}
    structure = review.get("structure_validation") or {}
    structure_text = (
        f"exact V2 row r{structure.get('row_index')}"
        if structure.get("validated")
        else "chưa có exact V2 row validation"
    )
    vote_text = " &nbsp; ".join(
        f"{html.escape(str(name))}=<code>{html.escape(str(value or 'none'))}</code>"
        for name, value in votes.items()
    )
    evidence = evidence_paragraph_html(
        {"direct_evidence": review.get("machine_candidate_direct_evidence")}
    )

    return (
        f"<div style='padding:10px;border-radius:7px;background:{color};font-size:12px'>"
        f"<b>Machine consensus:</b> {html.escape(status)} &nbsp; | &nbsp; "
        f"confidence={float(review.get('machine_confidence') or 0.0):.3f} &nbsp; | &nbsp; "
        f"agreement={float(review.get('agreement') or 0.0):.2f}<br>"
        f"<b>Recommended rank:</b> {html.escape(str(review.get('machine_candidate_rank')))}"
        f" &nbsp; | &nbsp; source={html.escape(str(review.get('machine_candidate_source') or 'unknown'))}"
        f" &nbsp; | &nbsp; structure={html.escape(structure_text)}<br>"
        f"<b>Verifier:</b> {html.escape(str((review.get('verifier') or {}).get('verdict')))} — "
        f"{html.escape(str((review.get('verifier') or {}).get('reason') or ''))}"
        "<details style='margin-top:6px'><summary style='cursor:pointer'>Machine evidence và votes</summary>"
        f"<div style='margin-top:6px'>{evidence}</div>"
        f"<div style='padding-top:5px'><b>Agent votes:</b> {vote_text}</div></details>"
        f"</div>"
    )


def assistant_html(review: dict[str, Any] | None) -> str:
    if not review:
        return "<div style='color:#777'>Chưa có Codex-assisted review.</div>"

    status = html.escape(str(review.get("annotation_status") or "machine_provisional"))
    completeness = html.escape(str(review.get("evidence_completeness") or "unknown"))
    confidence = float(review.get("review_confidence") or 0.0)
    ranks = ", ".join(str(value) for value in review.get("proposed_ranks") or []) or "none"
    notes = html.escape(str(review.get("review_notes") or ""))
    reason_codes = ", ".join(
        html.escape(str(value)) for value in review.get("reason_codes") or []
    ) or "none"
    evidence_refs = review.get("evidence_refs") or []
    evidence_blocks = []
    for ref in evidence_refs:
        source_rows = "".join(
            "<div style='margin-top:4px'><b>r"
            f"{html.escape(str(source_row.get('row_index')))}:</b> "
            f"{html.escape(' | '.join(str(cell) for cell in source_row.get('row') or []))}</div>"
            for source_row in ref.get("source_rows") or []
        )
        evidence_blocks.append(
            "<div style='margin:5px 0;padding:7px 9px;background:#eef8f0;"
            "border-left:4px solid #2f855a;border-radius:4px'>"
            f"<b>rank #{html.escape(str(ref.get('rank')))}</b> · "
            f"rows={html.escape(str(ref.get('row_indices') or []))}<br>"
            f"{html.escape(str(ref.get('direct_evidence') or ''))}"
            f"{source_rows}</div>"
        )
    evidence_html = "".join(evidence_blocks) or "<i>Không có exact-row reference.</i>"

    return (
        "<div style='padding:10px;border-radius:7px;background:#eef5ff;"
        "border:1px solid #c8d9f0;font-size:12px;line-height:1.5'>"
        f"<b>Codex review round:</b> {html.escape(str(review.get('review_round') or 1))}"
        f" &nbsp; | &nbsp; status={status} &nbsp; | &nbsp; confidence={confidence:.3f}<br>"
        f"<b>Evidence completeness:</b> {completeness} &nbsp; | &nbsp; proposed ranks={ranks}<br>"
        f"<b>Reason codes:</b> {reason_codes}<br>"
        f"<b>Nhận xét:</b> {notes}"
        "<details style='margin-top:6px'><summary style='cursor:pointer'>Exact-row references</summary>"
        f"{evidence_html}</details></div>"
    )
class BundleReviewer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        bundle = args.bundle_dir.resolve()
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        if int(manifest.get("error_count") or 0) != 0:
            raise RuntimeError("Bundle has retrieval errors; human review refused.")

        all_items = load_jsonl(bundle / "review_items.jsonl")
        self.items_by_id = {int(item["id"]): item for item in all_items}
        self.tables = {
            str(row["internal_table_uid"]): row
            for row in load_jsonl(bundle / "tables.jsonl")
        }
        requested_structure = args.table_structure
        default_structure = bundle / "tables_structured_v2.jsonl"
        structure_path = requested_structure or default_structure
        self.structure_path = structure_path.resolve() if structure_path.is_file() else None
        self.structure_count = 0
        if requested_structure and not requested_structure.is_file():
            raise FileNotFoundError(requested_structure)
        if self.structure_path:
            validate_structure_sidecar(bundle, self.structure_path)
            structures = {
                str(row["internal_table_uid"]): row
                for row in load_jsonl(self.structure_path)
            }
            unknown = sorted(set(structures) - set(self.tables))
            if unknown:
                raise RuntimeError(
                    "Table-structure sidecar contains UID absent from bundle: "
                    + ", ".join(unknown[:3])
                )
            for uid, structure in structures.items():
                self.tables[uid] = {**self.tables[uid], **structure}
            self.structure_count = len(structures)
        requested_segments = args.report_segments
        default_segments = bundle / "report_segments_v1.jsonl"
        segment_path = requested_segments or default_segments
        self.segment_count = 0
        if requested_segments and not requested_segments.is_file():
            raise FileNotFoundError(requested_segments)
        if segment_path.is_file():
            segment_path = segment_path.resolve()
            segment_manifest = validate_report_segment_sidecar(bundle, segment_path)
            segments = {str(row.get("internal_table_uid") or ""): row for row in load_jsonl(segment_path)}
            if "" in segments or len(segments) != int(segment_manifest.get("segment_count") or -1):
                raise ValueError("Report-segment UID/count contract is invalid")
            unknown_segments = sorted(set(segments) - set(self.tables))
            if unknown_segments:
                raise RuntimeError("Report-segment sidecar has an unknown table UID: " + unknown_segments[0])
            for uid, segment in segments.items():
                self.tables[uid]["report_segment"] = segment
            self.segment_count = len(segments)
        self.machine = {
            int(row["id"]): row
            for row in load_jsonl(args.machine_reviews)
        } if args.machine_reviews else {}
        assistant_reviews = getattr(args, "assistant_reviews", None)
        self.assistant = {
            int(row["id"]): row
            for row in load_jsonl(assistant_reviews)
        } if assistant_reviews else {}

        if args.queue:
            queue_ids = [int(row["id"]) for row in load_jsonl(args.queue)]
            missing = [qid for qid in queue_ids if qid not in self.items_by_id]
            if missing:
                raise RuntimeError(f"Queue contains IDs missing from bundle: {missing}")
            self.items = [self.items_by_id[qid] for qid in queue_ids]
        else:
            self.items = all_items

        if not self.items:
            raise RuntimeError("Review queue is empty.")

        self.annotations = {
            int(row["id"]): row for row in load_jsonl(args.output)
        }
        self.index = self._initial_index(args.start_id)
        self.checkboxes: list[widgets.Checkbox] = []
        self.current_candidates: list[dict[str, Any]] = []
        self.current_formula: dict[str, Any] | None = None

        self.progress = widgets.IntProgress(min=0, max=len(self.items), description="Reviewed")
        self.counter = widgets.HTML()
        self.question_box = widgets.HTML()
        self.formula_box = widgets.HTML()
        self.machine_box = widgets.HTML()
        self.assistant_box = widgets.HTML()
        self.candidate_box = widgets.VBox()
        self.notes = widgets.Textarea(
            placeholder="Optional human note: why candidate is correct/wrong, planner issue, missing table...",
            layout=widgets.Layout(width="100%", height="68px"),
        )
        self.planner_issue = widgets.Checkbox(description="Planner/metadata issue", indent=False)
        self.formula_confirmed = widgets.Checkbox(
            description="Đã kiểm tra công thức và đủ operand",
            indent=False,
        )

        self.accept_machine = widgets.Button(
            description="Accept machine →", button_style="success", icon="check-circle"
        )
        self.accept_assistant = widgets.Button(
            description="Accept Codex →", button_style="info", icon="user-check"
        )
        self.save_selected = widgets.Button(
            description="Save selected →", button_style="success", icon="check"
        )
        self.no_candidate = widgets.Button(
            description="No candidate in Top-K →", button_style="warning", icon="times"
        )
        self.skip = widgets.Button(description="Skip →", icon="forward")
        self.prev = widgets.Button(description="← Previous", icon="arrow-left")
        self.next = widgets.Button(description="Next →", icon="arrow-right")
        self.status = widgets.HTML()

        self.accept_machine.on_click(self._accept_machine)
        self.accept_assistant.on_click(self._accept_assistant)
        self.save_selected.on_click(self._save_selected)
        self.no_candidate.on_click(self._save_none)
        self.skip.on_click(lambda _: self._advance(1))
        self.prev.on_click(lambda _: self._advance(-1))
        self.next.on_click(lambda _: self._advance(1))

        self.root = widgets.VBox(
            [
                widgets.HBox([self.progress, self.counter]),
                self.question_box,
                self.formula_box,
                widgets.HTML("<b>Machine multi-agent review</b>"),
                self.machine_box,
                widgets.HTML("<b>Codex-assisted review</b> — đề xuất máy, cần human xác nhận"),
                self.assistant_box,
                widgets.HTML(
                    "<b>Candidate tables</b> — chức năng → mức phù hợp → evidence → bảng khi cần"
                ),
                self.candidate_box,
                self.formula_confirmed,
                self.planner_issue,
                self.notes,
                widgets.HBox(
                    [
                        self.prev,
                        self.accept_assistant,
                        self.accept_machine,
                        self.save_selected,
                        self.no_candidate,
                        self.skip,
                        self.next,
                    ]
                ),
                self.status,
            ]
        )
        self.render()

    def _initial_index(self, start_id: int | None) -> int:
        if start_id is not None:
            for index, item in enumerate(self.items):
                if int(item["id"]) >= start_id:
                    return index
        for index, item in enumerate(self.items):
            annotation = self.annotations.get(int(item["id"]))
            if annotation is None or annotation_needs_structure_refresh(annotation):
                return index
        return 0

    def _candidate_panel(
        self,
        candidate: dict[str, Any],
        question: str,
        selected: bool,
    ) -> widgets.Widget:
        rank = int(candidate["rank"])
        uid = str(candidate["internal_table_uid"])
        table = self.tables.get(uid)
        if table is None:
            raise RuntimeError(f"Bundle table payload missing UID: {uid}")

        checkbox = widgets.Checkbox(
            value=selected,
            description=(
                f"Override mismatch — candidate #{rank}"
                if relevance_assessment(table, question, self.current_formula, candidate)["status"] == "mismatch"
                else f"Relevant candidate #{rank}"
            ),
            indent=False,
        )
        self.checkboxes.append(checkbox)

        function, section = table_classification(table)
        purpose = table_purpose(table)
        trace = context_trace(table)
        # A numbered note/topic is the tightest exact source context.  Keep the
        # longer title in the collapsed trace instead of repeating it up front.
        segment = table.get("report_segment") or {}
        normalized_heading = compact_text(segment.get("source_heading"), 420)
        parent_heading = compact_text(segment.get("source_parent_heading"), 220)
        if parent_heading and normalized_heading and parent_heading.casefold() not in normalized_heading.casefold():
            normalized_heading = parent_heading + " › " + normalized_heading
        context_heading = html.escape(
            normalized_heading or trace["topic_label"] or report_context_text(table, candidate)
        )
        operand_matches = candidate_operand_matches(self.current_formula, candidate, table)
        assessment = relevance_assessment(table, question, self.current_formula, candidate)
        assessment_color = {
            "mismatch": ("#fff0f0", "#b42318"),
            "needs_check": ("#fff8df", "#a15c00"),
            "not_blocked": ("#eef8f0", "#2f855a"),
        }[assessment["status"]]
        quality = table.get("structure_quality") or {}
        structure_status = str(quality.get("status") or "legacy_bundle_rows")
        structure_label = (
            f"{SOURCE_GRID_V2_LABEL} từ raw HTML"
            if structure_status == "reconstructed_from_raw_html"
            else "Legacy grid — chưa xác minh căn cột"
        )
        section_text = (
            f" · phần {html.escape(section['label'])}"
            if section["kind"] != "unknown"
            else ""
        )
        meta = (
            f"<b>{html.escape(str(candidate.get('document_id')))}</b> | "
            f"year={candidate.get('report_year')} | scope={html.escape(str(candidate.get('scope')))} | "
            f"page={candidate.get('page_no')} | source={html.escape(str(candidate.get('candidate_source') or 'unknown'))}"
        )
        period_unit = []
        if trace["period_labels"]:
            period_unit.append("kỳ=" + ", ".join(trace["period_labels"]))
        if trace["unit_labels"]:
            period_unit.append("đơn vị=" + ", ".join(trace["unit_labels"]))
        period_unit_text = " · ".join(period_unit) or "chưa bind được kỳ/đơn vị"
        operand_text = ""
        if self.current_formula:
            if operand_matches:
                shown = [match["label"] for match in operand_matches[:4]]
                labels = "; ".join(shown)
                if len(operand_matches) > len(shown):
                    labels += f"; +{len(operand_matches) - len(shown)} operand khác"
                operand_text = (
                    "<div style='margin-top:6px;color:#3b2f6b'><b>Operand có thể hỗ trợ:</b> "
                    f"{html.escape(labels)}</div>"
                )
            else:
                operand_text = (
                    "<div style='margin-top:6px;color:#8a5a00'><b>Operand:</b> "
                    "chưa thấy metric+kỳ khớp đủ trong bảng này.</div>"
                )
        trace_detail = (
            "<details style='margin-top:5px'><summary style='cursor:pointer'>Dấu vết phân loại ngữ cảnh</summary>"
            f"<div>rule evidence={html.escape(function['matched_evidence'] or 'generic structure')}</div>"
            f"<div>confidence={function['confidence']:.2f}; specificity={html.escape(function['specificity'])}</div>"
            f"<div>tiêu đề nguồn đầy đủ={html.escape(trace['source_title'] or 'không có')}</div>"
            f"<div>{html.escape(period_unit_text)}</div></details>"
        )

        summary_html = (
            "<div style='margin:-3px 0 8px;color:#4a5568'><b>Tóm tắt bảng:</b> "
            f"{html.escape(trace['summary'])}</div>"
            if trace["summary"]
            else ""
        )
        context_box = widgets.HTML(
            "<div style='padding:10px 12px;background:#f5f7fb;border:1px solid #d9e0ea;"
            "border-radius:7px;font-size:12px;line-height:1.55'>"
            "<div style='font-size:11px;text-transform:uppercase;letter-spacing:.04em;"
            "color:#667085'><b>Chức năng bảng</b></div>"
            f"<div style='margin:3px 0 7px;font-size:14px;color:#263238'><b>{html.escape(function['label'])}</b>{section_text}</div>"
            f"<div style='margin:-3px 0 7px;color:#4a5568'><b>Cách dùng nhanh:</b> {html.escape(purpose['label'])}</div>"
            "<div style='font-size:11px;text-transform:uppercase;letter-spacing:.04em;"
            "color:#667085'><b>Ngữ cảnh/tiêu đề nguồn</b></div>"
            f"<div style='margin:3px 0 8px;font-size:13px;color:#263238'>{context_heading}</div>"
            f"{summary_html}"
            f"<div style='padding:6px 8px;background:{assessment_color[0]};border-left:4px solid {assessment_color[1]};'>"
            f"<b>{html.escape(assessment['label'])}:</b> {html.escape(assessment['reason'])}</div>"
            f"{operand_text}"
            f"<div style='margin-top:7px;color:#667085'>{html.escape(structure_label)} · {EVIDENCE_CONTEXT_V3_LABEL}: header/kỳ/đơn vị · {meta}</div>"
            f"{trace_detail}"
            "</div>"
        )

        parts = evidence_parts(candidate.get("direct_evidence"))
        evidence_value = next(
            (value for label, value in parts if label in {"Dòng giá trị", "Dòng neo"}),
            "Không có dòng evidence được export.",
        )
        if not has_verified_structure(table) or assessment["status"] == "mismatch" or (
            self.current_formula and not operand_matches
        ):
            hidden_reason = (
                "grid chưa được dựng và xác minh từ raw HTML"
                if not has_verified_structure(table)
                else "chưa map được vào operand bắt buộc của công thức"
                if self.current_formula and not operand_matches
                else "sai phần kế toán"
            )
            quick = widgets.HTML(
                "<div style='padding:7px 10px;margin-top:6px;background:#fff0f0;border-left:4px solid #b42318;font-size:12px'>"
                f"Evidence số liệu được ẩn ở màn hình nhanh vì {html.escape(hidden_reason)}; "
                "mở grid nguồn nếu cần kiểm tra override."
                "</div>"
            )
        else:
            compact_table = compact_source_table_html(
                table.get("rows") or [],
                question,
                candidate,
                column_labels=table.get("column_labels"),
                header_row_indices=table.get("header_row_indices"),
            )
            quick = widgets.HTML(
                "<div style='padding:7px 10px;margin-top:6px;background:#eef8f0;border-left:4px solid #2f855a;font-size:12px;line-height:1.5'>"
                "<b>Dòng evidence được export:</b> "
                f"{html.escape(compact_text(evidence_value, 520))}"
                f"{compact_table}"
                "</div>"
            )

        table_html = aligned_table_html(
            table.get("rows") or [],
            "" if assessment["status"] == "mismatch" else question,
            candidate,
            self.args.preview_rows,
            column_labels=table.get("column_labels"),
            header_row_indices=table.get("header_row_indices"),
            structure_quality=quality,
        )
        detail = widgets.HTML(
            "<details style='margin-top:6px'><summary style='cursor:pointer'><b>Mở grid nguồn đã căn cột</b></summary>"
            + table_html
            + "</details>"
        )
        summary_box = widgets.HTML(
            "<div style='margin-top:7px;padding:8px 10px;background:#fff8df;"
            "border:1px solid #eadca6;border-radius:6px;font-size:12px;line-height:1.45'>"
            f"<b>Tóm tắt một dòng:</b> {html.escape(candidate_summary(candidate, table, question, self.current_formula))}"
            "</div>"
        )
        return widgets.VBox(
            [context_box, checkbox, quick, detail, summary_box]
        )

    def render(self) -> None:
        item = self.items[self.index]
        qid = int(item["id"])
        question = str(item["question"])
        existing = self.annotations.get(qid, {})
        machine = self.machine.get(qid)
        assistant = self.assistant.get(qid)
        self.current_candidates = list(item.get("candidates") or [])
        self.current_formula = infer_formula_spec(question)

        reviewed = sum(
            1
            for item in self.items
            if int(item["id"]) in self.annotations
            and not annotation_needs_structure_refresh(
                self.annotations.get(int(item["id"]))
            )
        )
        self.progress.value = reviewed
        self.counter.value = f"<b>{reviewed}/{len(self.items)}</b> reviewed | position {self.index + 1}/{len(self.items)}"
        self.question_box.value = (
            f"<div style='font-size:17px;padding:8px 0'><b>Q{qid}</b> "
            f"<span style='color:#666'>[{html.escape(str(item.get('weak_family') or ''))}]</span><br>"
            f"{html.escape(question)}</div>"
        )
        self.formula_box.value = formula_context_html(
            self.current_formula,
            self.current_candidates,
            self.tables,
        )
        self.formula_confirmed.layout.display = "flex" if self.current_formula else "none"
        self.formula_confirmed.description = (
            "Đã kiểm tra các công thức; EvidenceSet vẫn partial đến khi bind đủ từng stage/entity"
            if self.current_formula
            and self.current_formula.get("execution_status") == "stage_binding_required"
            else "Đã kiểm tra công thức và đủ operand"
        )
        self.formula_confirmed.value = bool(existing.get("formula_confirmed", False))
        self.save_selected.description = (
            "Save EvidenceSet →" if self.current_formula else "Save selected →"
        )
        self.machine_box.value = machine_html(machine)
        self.assistant_box.value = assistant_html(assistant)
        self.notes.value = str(existing.get("review_notes") or "")
        self.planner_issue.value = bool(existing.get("planner_issue", False))

        selected_uids = set(existing.get("positive_table_uids") or [])
        self.checkboxes = []
        panels = []
        titles = []
        for candidate in self.current_candidates:
            panels.append(
                self._candidate_panel(
                    candidate,
                    question,
                    str(candidate["internal_table_uid"]) in selected_uids,
                )
            )
            table = self.tables[str(candidate["internal_table_uid"])]
            function, section = table_classification(table)
            assessment = relevance_assessment(table, question, self.current_formula, candidate)
            operand_matches = candidate_operand_matches(self.current_formula, candidate, table)
            section_title = f" · {section['label']}" if section["kind"] != "unknown" else ""
            operand_title = (
                " · op=" + ",".join(match["operand_id"] for match in operand_matches)
                if operand_matches
                else ""
            )
            safety = " ⛔" if assessment["status"] == "mismatch" else ""
            title = (
                f"#{candidate['rank']}{safety} {function['label']}{section_title}{operand_title} "
                f"| {candidate.get('document_id')} | {candidate.get('report_year')}"
            )
            titles.append(title[:150])

        if panels:
            accordion = widgets.Accordion(children=panels, selected_index=None)
            for index, title in enumerate(titles):
                accordion.set_title(index, title)
            if machine and machine.get("machine_candidate_rank"):
                rank = int(machine["machine_candidate_rank"])
                if 1 <= rank <= len(panels):
                    machine_candidate = self.current_candidates[rank - 1]
                    machine_table = self.tables[str(machine_candidate["internal_table_uid"])]
                    if relevance_assessment(
                        machine_table,
                        question,
                        self.current_formula,
                        machine_candidate,
                    )["status"] != "mismatch":
                        accordion.selected_index = rank - 1
            self.candidate_box.children = (accordion,)
        else:
            self.candidate_box.children = (widgets.HTML("<i>No candidates in bundle.</i>"),)

        machine_blocked = False
        machine_structure_blocked = False
        if machine and machine.get("machine_candidate_uid"):
            machine_uid = str(machine["machine_candidate_uid"])
            machine_table = self.tables.get(machine_uid)
            machine_candidate = next(
                (
                    candidate
                    for candidate in self.current_candidates
                    if str(candidate["internal_table_uid"]) == machine_uid
                ),
                None,
            )
            machine_blocked = bool(
                machine_table
                and relevance_assessment(
                    machine_table,
                    question,
                    self.current_formula,
                    machine_candidate,
                )["status"] == "mismatch"
            )
            machine_structure_blocked = bool(
                machine_table and not has_verified_structure(machine_table)
            )
        machine_formula_blocked = formula_is_multi_operand(self.current_formula)
        self.accept_machine.disabled = not bool(
            machine
            and machine.get("machine_candidate_uid")
            and not machine_blocked
            and not machine_structure_blocked
            and not machine_formula_blocked
        )
        self.accept_machine.description = (
            "Machine blocked: mismatch"
            if machine_blocked
            else "Machine blocked: cần grid V2"
            if machine_structure_blocked
            else "Machine blocked: cần EvidenceSet"
            if machine_formula_blocked
            else "Accept machine →"
        )
        self.accept_assistant.disabled = not bool(
            assistant
            and (
                assistant.get("proposed_positive_table_uids")
                or assistant.get("proposed_no_candidate")
            )
        )
        if assistant and assistant.get("proposed_no_candidate"):
            self.accept_assistant.description = "Confirm no Top-K →"
        elif assistant and assistant.get("evidence_completeness") != "complete":
            self.accept_assistant.description = "Confirm partial →"
        else:
            self.accept_assistant.description = "Accept Codex →"
        refresh_note = (
            f" · <b style='color:#a15c00'>label cũ cần xác minh lại trên {SOURCE_GRID_V2_LABEL}</b>"
            if annotation_needs_structure_refresh(existing)
            else ""
        )
        self.status.value = (
            f"<span style='color:#666'>Saved status: "
            f"{html.escape(str(existing.get('annotation_status', 'not reviewed')))} · "
            f"{SOURCE_GRID_V2_LABEL}: {self.structure_count}/{len(self.tables)}{refresh_note}</span>"
        )

    def _persist(
        self,
        status: str,
        positive_uids: list[str],
        decision_source: str = "human_selected",
    ) -> None:
        item = self.items[self.index]
        qid = int(item["id"])
        machine = self.machine.get(qid) or {}
        assistant = self.assistant.get(qid) or {}
        ranks = [
            int(candidate["rank"])
            for candidate in self.current_candidates
            if str(candidate["internal_table_uid"]) in set(positive_uids)
        ]
        selected_coverage = formula_coverage(
            self.current_formula,
            self.current_candidates,
            self.tables,
            selected_uids=set(positive_uids),
        )
        formula_confirmed = bool(
            self.current_formula and self.formula_confirmed.value
        )
        formula_complete = bool(
            not self.current_formula
            or (selected_coverage["complete"] and formula_confirmed)
        )
        selected_structure_complete = bool(positive_uids) and all(
            has_verified_structure(self.tables.get(uid) or {}) for uid in positive_uids
        )
        resolved_status = status
        if positive_uids and (not formula_complete or not selected_structure_complete):
            resolved_status = "human_verified_partial"
        evidence_completeness = (
            "missing"
            if not positive_uids
            else "complete"
            if formula_complete and selected_structure_complete
            else "partial"
        )
        self.annotations[qid] = {
            "id": qid,
            "question": item["question"],
            "annotation_status": resolved_status,
            "human_verified": True,
            "positive_table_uids": positive_uids,
            "selected_ranks": sorted(ranks),
            "planner_issue": bool(self.planner_issue.value),
            "review_notes": self.notes.value.strip(),
            "machine_candidate_uid": machine.get("machine_candidate_uid"),
            "machine_consensus_status": machine.get("consensus_status"),
            "machine_confidence": machine.get("machine_confidence"),
            "decision_source": decision_source,
            "evidence_completeness": evidence_completeness,
            "structure_validation": {
                "complete": selected_structure_complete,
                "structure_version": 2 if selected_structure_complete else 1,
                "source": str(self.structure_path) if self.structure_path else None,
            },
            "formula_confirmed": formula_confirmed,
            "formula_spec": self.current_formula,
            "operand_coverage": selected_coverage["operands"],
            "assistant_reviewer_type": assistant.get("reviewer_type"),
            "assistant_review_round": assistant.get("review_round"),
            "assistant_proposed_positive_table_uids": assistant.get(
                "proposed_positive_table_uids"
            ),
            "assistant_annotation_status": assistant.get("annotation_status"),
            "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write(self.args.output, self.annotations)
        self.status.value = f"<b style='color:#176b2c'>Saved Q{qid}: {resolved_status}</b>"

    def _accept_machine(self, _button: widgets.Button) -> None:
        item = self.items[self.index]
        machine = self.machine.get(int(item["id"])) or {}
        selected = machine.get("machine_candidate_uid")
        if not selected:
            self.status.value = "<b style='color:#a00'>No machine candidate.</b>"
            return
        self._persist("human_verified", [str(selected)], "human_accepted_machine")
        self._advance(1)

    def _accept_assistant(self, _button: widgets.Button) -> None:
        item = self.items[self.index]
        assistant = self.assistant.get(int(item["id"])) or {}
        selected = [
            str(uid) for uid in assistant.get("proposed_positive_table_uids") or []
        ]
        if selected:
            completeness = str(assistant.get("evidence_completeness") or "unknown")
            status = "human_verified" if completeness == "complete" else "human_verified_partial"
            decision_source = (
                "human_accepted_codex"
                if completeness == "complete"
                else "human_confirmed_codex_partial"
            )
            self._persist(status, selected, decision_source)
            self._advance(1)
            return
        if assistant.get("proposed_no_candidate"):
            self._persist(
                "verified_no_candidate",
                [],
                "human_accepted_codex_no_candidate",
            )
            self._advance(1)
            return
        self.status.value = "<b style='color:#a00'>Codex review chưa có quyết định để xác nhận.</b>"

    def _save_selected(self, _button: widgets.Button) -> None:
        selected = [
            str(candidate["internal_table_uid"])
            for candidate, checkbox in zip(self.current_candidates, self.checkboxes)
            if checkbox.value
        ]
        if not selected:
            self.status.value = "<b style='color:#a00'>Select at least one candidate or use No candidate.</b>"
            return
        self._persist("human_verified", selected, "human_selected")
        self._advance(1)

    def _save_none(self, _button: widgets.Button) -> None:
        self._persist("verified_no_candidate", [], "human_no_candidate_in_topk")
        self._advance(1)

    def _advance(self, delta: int) -> None:
        self.index = min(max(self.index + delta, 0), len(self.items) - 1)
        self.render()


def main() -> None:
    args = parse_args()
    reviewer = BundleReviewer(args)
    clear_output(wait=True)
    display(reviewer.root)
    print("Local static review only — no dense model/index is loaded.")
    if reviewer.structure_path:
        print(f"Using {SOURCE_GRID_V2_LABEL}:", reviewer.structure_path)
    else:
        print(f"No {SOURCE_GRID_V2_LABEL} found — values remain unsafe for column-level review.")
    print("Human labels persist atomically to:", args.output)


if __name__ == "__main__":
    main()
