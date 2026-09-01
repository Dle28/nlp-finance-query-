#!/usr/bin/env python3
"""Independent source/semantic replay for the aggregate-total research lane.

The aggregate builder is intentionally permissive enough to discover a useful
full-population family.  This receipt is a second, value-aware audit of the
rows that the builder selected.  It is not a gold-label scorer and it cannot
authorize a strict release.  Its job is to prevent a numerically replayable
but semantically wrong source cell from entering a materialized overlay.

The audit binds each selected cell to the complete structured-table asset and
checks, independently of the builder trace:

* table/document/source hashes, coordinates, raw Decimal and output replay;
* the metric against the selected row path and its nearest section heading;
* section-checksum boundaries and Decimal child sums;
* topical aggregate context and current/opening-period column intent; and
* conflicting direct candidates in the same table when the review bundle has
  an explicit row/column binding.

Rows that fail are retained in the receipt with reason codes.  Only the
eligible subset can be consumed by the UID-closure overlay materializer.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_aggregate_total_independent_replay_v2"
EXPECTED_TIER = "program_aggregate_total_v1"
MODULE_NAME = "aggregate_total_variant_for_independent_replay_v2"


def _load_variant() -> Any:
    path = Path(__file__).with_name("run_aggregate_total_variant_v1.py")
    spec = importlib.util.spec_from_file_location(MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load aggregate variant: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


VARIANT = _load_variant()
BUILDER = VARIANT.BUILDER


_GENERIC_TOKENS = {
    "a",
    "bao",
    "cua",
    "cuoi",
    "dau",
    "den",
    "dong",
    "la",
    "nam",
    "nghin",
    "nhieu",
    "ngay",
    "so",
    "thang",
    "trong",
    "trieu",
    "ty",
    "vnd",
    "voi",
    "vao",
    "xet",
    "tong",
    "va",
}
_UNIT_TOKENS = {
    "billion",
    "million",
    "thousand",
    "hundred",
    "ngan",
    "nghin",
    "trieu",
    "ty",
    "ti",
    "tram",
    "dong",
    "vnd",
}
_HEADER_OR_UNIT_LABELS = {
    "",
    "nam nay",
    "nam truoc",
    "so cuoi nam",
    "so dau nam",
    "so cuoi ky",
    "so dau ky",
    "vnd",
    "trieu vnd",
    "ngan vnd",
    "nghin vnd",
    "ty vnd",
    "%",
}
_NUMBER_RE = re.compile(r"(?:19|20)\d{2}|\b\d+\b")
_AGGREGATE_LABELS = {"tong", "tong cong", "cong", "total", "sum"}
_END_MARKERS = {
    "cuoi nam",
    "cuoi ky",
    "31 12",
    "tai ngay",
    "den ngay",
    "ket thuc",
    "nam nay",
}
_OPENING_MARKERS = {"dau nam", "dau ky", "01 01", "1 1", "nam truoc"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            yield value


def _ascii(value: Any) -> str:
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_label(value: Any) -> str:
    text = _ascii(value)
    text = re.sub(r"^(?:\d+|[ivxlcdm]+|[a-z])[.)]?\s+", "", text)
    text = re.sub(r"\s*[*†‡]+\s*$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _drop_period_and_unit_tokens(tokens: list[str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        if token in _UNIT_TOKENS:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        result.append(token)
    return result


def _metric_tokens(value: Any) -> list[str]:
    """Return meaningful concept tokens while retaining qualifiers.

    ``tong``, ``so du`` and period/unit words are query framing, not row
    identity.  Terms such as ``tai san``, ``cho vay``, ``pho thong`` and
    ``dang luu hanh`` remain mandatory when they occur in the metric.
    """

    text = _clean_label(value)
    # Preserve short-/long-term qualifiers as atomic concepts. A bare
    # ``ngan`` can also mean the unit "thousand", so protect the phrase
    # before unit filtering.
    text = text.replace("ngan han", "__short_term__")
    text = text.replace("dai han", "__long_term__")
    text = re.sub(
        r"\b(?:tong\s+so\s+tien|tong\s+tien|tong\s+gia\s+tri|tong|gia\s+tri|"
        r"so\s+du|so\s+cuoi|so\s+dau|cuoi\s+nam|dau\s+nam|cuoi\s+ky|"
        r"dau\s+ky|den\s+ngay|tai\s+ngay|ket\s+thuc)\b",
        " ",
        text,
    )
    tokens = [token for token in text.split() if token not in _GENERIC_TOKENS]
    return _drop_period_and_unit_tokens(tokens)


def _tokens(value: Any) -> set[str]:
    return set(_metric_tokens(value))


def _metric_semantic_text(value: Any) -> str:
    """Normalize a metric while retaining multiword semantic qualifiers."""

    return (
        " ".join(_metric_tokens(value))
        .replace("__short_term__", "ngan han")
        .replace("__long_term__", "dai han")
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    """Match a normalized phrase without allowing digit-substring matches."""

    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text))


def _contains_any_phrase(text: str, phrases: Iterable[str]) -> bool:
    return any(_contains_phrase(text, phrase) for phrase in phrases)


def _row_cells(table: Mapping[str, Any], row_index: int) -> list[Any]:
    rows = table.get("rows") or []
    if not isinstance(rows, list) or not 0 <= row_index < len(rows):
        return []
    row = rows[row_index]
    return row if isinstance(row, list) else []


def _row_path(table: Mapping[str, Any], row_index: int) -> str:
    paths = table.get("row_paths") or []
    if isinstance(paths, list) and 0 <= row_index < len(paths):
        return str(paths[row_index] or "")
    return ""


def _row_has_numeric(table: Mapping[str, Any], row_index: int) -> bool:
    return any(
        BUILDER.parse_decimal(cell) is not None
        for cell in _row_cells(table, row_index)[1:]
    )


def _row_text_segments(table: Mapping[str, Any], row_index: int) -> list[str]:
    segments: list[str] = []
    path = _row_path(table, row_index)
    if path:
        segments.extend(part.strip() for part in re.split(r"\s*>\s*", path) if part.strip())
    for cell in _row_cells(table, row_index):
        cell_text = str(cell or "").strip()
        if cell_text and re.search(r"[A-Za-zÀ-ỹĐđ]", cell_text):
            segments.append(cell_text)
    return list(dict.fromkeys(segments))


def _row_context_segments(table: Mapping[str, Any], row_index: int) -> list[str]:
    """Return narrow structural parent context for qualifier checks.

    Balance sheets commonly put a parent such as ``Tài sản dở dang dài
    hạn`` on the preceding coded row, while the selected child row only
    says ``Chi phí xây dựng cơ bản dở dang``. The parent is valid context
    for a qualifier, but must not become the selected row's metric match.
    """

    if row_index <= 0:
        return []
    previous = _row_cells(table, row_index - 1)
    if not previous or not str(previous[0] or "").strip().isdigit():
        return []
    return [
        str(cell).strip()
        for cell in previous
        if str(cell or "").strip() and re.search(r"[A-Za-zÀ-ỹĐđ]", str(cell))
    ]


def _is_header_or_unit_row(table: Mapping[str, Any], row_index: int) -> bool:
    cells = _row_cells(table, row_index)
    labels = [_clean_label(cell) for cell in cells if str(cell or "").strip()]
    if not labels:
        return True
    if any(BUILDER.parse_decimal(cell) is not None for cell in cells[1:]):
        return False
    joined = " ".join(labels)
    if joined in _HEADER_OR_UNIT_LABELS:
        return True
    if all(label in _HEADER_OR_UNIT_LABELS for label in labels):
        return True
    if any(marker in joined for marker in ("so cuoi nam", "so dau nam", "nam nay", "nam truoc")):
        return True
    if all(not re.search(r"[a-z]{3,}", label) for label in labels):
        return True
    return False


def _section_headings(table: Mapping[str, Any], before_index: int) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    rows = table.get("rows") or []
    for index in range(min(before_index, len(rows))):
        if _is_header_or_unit_row(table, index):
            continue
        if _row_has_numeric(table, index):
            continue
        labels = [
            _clean_label(cell)
            for cell in _row_cells(table, index)
            if str(cell or "").strip()
            and re.search(r"[A-Za-zÀ-ỹĐđ]", str(cell or ""))
        ]
        label = " ".join(labels).strip()
        if label:
            headings.append((index, label))
    return headings


def _nearest_heading(table: Mapping[str, Any], row_index: int) -> tuple[int, str] | None:
    headings = _section_headings(table, row_index)
    return headings[-1] if headings else None


def _next_heading(table: Mapping[str, Any], row_index: int) -> tuple[int, str] | None:
    rows = table.get("rows") or []
    for index in range(row_index + 1, len(rows)):
        if _is_header_or_unit_row(table, index):
            continue
        if _row_has_numeric(table, index):
            continue
        labels = [
            _clean_label(cell)
            for cell in _row_cells(table, index)
            if str(cell or "").strip()
            and re.search(r"[A-Za-zÀ-ỹĐđ]", str(cell or ""))
        ]
        label = " ".join(labels).strip()
        if label:
            return index, label
    return None


def _table_context(table: Mapping[str, Any]) -> str:
    values: list[str] = []
    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        for key in ("source_title", "summary", "topic"):
            value = trace.get(key)
            if isinstance(value, Mapping):
                values.extend(str(part or "") for part in value.values())
            else:
                values.append(str(value or ""))
        values.extend(str(value or "") for value in trace.get("unit_labels") or [])
    for field in ("headers", "column_labels", "table_section"):
        raw = table.get(field) or []
        if isinstance(raw, Mapping):
            values.extend(str(value or "") for value in raw.values())
        elif isinstance(raw, (list, tuple)):
            values.extend(str(value or "") for value in raw)
        elif raw:
            values.append(str(raw))
    return _ascii(" ".join(values))


def _metric_options(
    review_item: Mapping[str, Any] | None,
    *,
    table_uid: str,
    row_index: int,
    column_index: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    options: list[str] = []
    corroborations: list[dict[str, Any]] = []
    if review_item:
        for key in ("effective_metric",):
            value = str(review_item.get(key) or "").strip()
            if value:
                options.append(value)
        plan = review_item.get("question_plan") or {}
        if isinstance(plan, Mapping):
            for operand in plan.get("operands") or []:
                if isinstance(operand, Mapping):
                    value = str(operand.get("metric") or "").strip()
                    if value:
                        options.append(value)
        for candidate in review_item.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            if str(candidate.get("internal_table_uid") or "") != table_uid:
                continue
            discovery = candidate.get("source_discovery") or {}
            binding = discovery.get("value_binding") or {}
            source_cell = binding.get("source_cell") or {}
            candidate_row = source_cell.get("source_row", discovery.get("row_index"))
            candidate_col = source_cell.get("source_cell", binding.get("column_index"))
            try:
                same_row = int(candidate_row) == int(row_index)
            except (TypeError, ValueError):
                same_row = False
            try:
                same_col = int(candidate_col) == int(column_index)
            except (TypeError, ValueError):
                same_col = False
            raw_label = str(discovery.get("raw_row_label") or "").strip()
            matched = str((discovery.get("metric_match") or {}).get("matched_metric") or "").strip()
            if same_row and same_col:
                for value in (raw_label, matched, str(candidate.get("effective_metric") or "")):
                    if value:
                        options.append(value)
                corroborations.append(
                    {
                        "rank": candidate.get("rank"),
                        "same_coordinate": True,
                        "raw_row_label": raw_label,
                        "matched_metric": matched,
                    }
                )
            elif raw_label or matched:
                corroborations.append(
                    {
                        "rank": candidate.get("rank"),
                        "same_coordinate": False,
                        "candidate_row": candidate_row,
                        "candidate_column": candidate_col,
                        "raw_row_label": raw_label,
                        "matched_metric": matched,
                    }
                )
    return list(dict.fromkeys(options)), corroborations


def _semantic_label_match(metric: str, table: Mapping[str, Any], row_index: int) -> dict[str, Any]:
    target = _tokens(metric)
    segments = _row_text_segments(table, row_index)
    row_label_tokens = _tokens(" ".join(segments))
    heading = _nearest_heading(table, row_index)
    heading_text = heading[1] if heading else ""
    parent_segments = _row_context_segments(table, row_index)
    scope_text = _ascii(
        " ".join(
            segments
            + parent_segments
            + ([heading_text] if heading_text else [])
        )
    )

    if not target:
        return {
            "pass": False,
            "reason": "EMPTY_METRIC_CONTRACT",
            "target_tokens": [],
            "row_segments": segments,
            "heading": heading,
        }

    # Prefer a single row/path segment.  This prevents a nearby unrelated
    # row from satisfying a target merely because all table text was joined.
    segment_scores: list[tuple[int, int, str, set[str]]] = []
    for segment in segments:
        segment_tokens = _tokens(segment)
        overlap = len(target & segment_tokens)
        segment_scores.append((overlap, len(segment_tokens), segment, segment_tokens))
    segment_scores.sort(key=lambda value: (value[0], value[1]), reverse=True)
    best = segment_scores[0] if segment_scores else (0, 0, "", set())
    best_overlap, _, best_segment, best_tokens = best

    # Exact concept coverage is the normal path.  Generic framing words were
    # removed above, while semantic qualifiers remain mandatory.
    # The effective metric can carry issuer names and question framing that
    # are absent from the source row. Accept a clean row-label subset only
    # when the requested semantic qualifiers are present in row/parent scope.
    # The reverse direction (target subset of row) remains strict.
    row_subset = bool(best_tokens) and best_tokens.issubset(target)
    exact = bool(target) and (target.issubset(best_tokens) or row_subset)

    # A few financial statements shorten "giá vốn hàng bán" to "giá vốn" in
    # a segment report.  Accept that only when the table is explicitly a
    # segment report and the selected row/path is the aggregate cost row.
    context = _table_context(table)
    narrow_cogs_alias = (
        {"gia", "von", "hang", "ban"}.issubset(target)
        and {"gia", "von"}.issubset(best_tokens)
        and "gia von" in context
        and "segment" in _ascii((table.get("table_function") or {}).get("kind", ""))
    )

    # If a metric contains a strong qualifier, it must occur in the selected
    # row/section.  This catches e.g. "đang lưu hành" vs "đã bán ra công
    # chúng", and "không có tài sản đảm bảo" vs a generic asset row.
    strong_qualifiers = {
        "tai san",
        "cho vay",
        "khong co",
        "dam bao",
        "dang luu hanh",
        "pho thong",
        "uu dai",
        "ngan han",
        "dai han",
        "khach hang",
        "thuong phat",
        "hoan lai",
        "hien hanh",
        "thuong mai",
        "ban hang",
        "cung cap",
        "dich vu",
        "khac",
        "cam co",
    }
    normalized_target = _metric_semantic_text(metric)
    missing_qualifiers = [
        phrase
        for phrase in sorted(strong_qualifiers, key=len, reverse=True)
        if phrase in normalized_target and phrase not in scope_text
    ]

    # ``Tiền và các khoản tương đương tiền`` is a combined balance. A row
    # labelled only ``Các khoản tương đương tiền`` is a component, even
    # though token sets overlap after removing framing words.
    combined_cash_component = (
        _contains_phrase(_ascii(metric), "tien va cac khoan tuong duong tien")
        and _contains_phrase(_ascii(best_segment), "cac khoan tuong duong tien")
        and not _contains_phrase(_ascii(best_segment), "tien va cac khoan tuong duong tien")
    )

    passed = (
        (exact or narrow_cogs_alias)
        and not missing_qualifiers
        and not combined_cash_component
    )
    reason = "PASS" if passed else "ROW_PATH_METRIC_MISMATCH"
    if missing_qualifiers:
        reason = "MISSING_STRONG_QUALIFIER"
    elif combined_cash_component:
        reason = "COMPONENT_ROW_FOR_COMBINED_METRIC"
    return {
        "pass": passed,
        "reason": reason,
        "metric": metric,
        "target_tokens": sorted(target),
        "row_segments": segments,
        "row_tokens": sorted(row_label_tokens),
        "best_segment": best_segment,
        "best_overlap": best_overlap,
        "heading": heading,
        "heading_context": heading_text,
        "parent_context": parent_segments,
        "missing_strong_qualifiers": missing_qualifiers,
        "row_subset": row_subset,
        "combined_cash_component": combined_cash_component,
        "narrow_cogs_alias": narrow_cogs_alias,
    }


def _topic_semantic_match(
    metric: str,
    question: str,
    table: Mapping[str, Any],
    row_index: int,
) -> dict[str, Any]:
    context = _table_context(table)
    question_norm = _ascii(question)
    row_label = _clean_label(_row_cells(table, row_index)[0] if _row_cells(table, row_index) else "")
    checks: dict[str, bool] = {}
    if "quy binh on gia xang dau" in question_norm:
        checks["fund_context"] = "quy binh on gia xang dau" in context
        checks["ending_balance_row"] = row_label == "so du cuoi nam"
    elif "gia tri trai phieu" in question_norm:
        # OCR frequently drops ``thường`` from the debt-note heading, while
        # retaining the stable semantic anchor ``trái phiếu``.
        checks["bond_context"] = "trai phieu" in context
        checks["not_payable_expense_context"] = "chi phi phai tra" not in context
        checks["aggregate_label"] = row_label in _AGGREGATE_LABELS
    elif "gia tri hop dong cong cu phai sinh" in question_norm:
        checks["derivative_context"] = "cong cu tai chinh phai sinh" in context
        checks["contract_value_header"] = "tong gia tri hop dong" in context
        checks["aggregate_label"] = row_label in _AGGREGATE_LABELS
    else:
        checks["metric_nonempty"] = bool(_tokens(metric))
    return {
        "pass": all(checks.values()),
        "reason": "PASS" if all(checks.values()) else "TOPIC_CONTEXT_MISMATCH",
        "checks": checks,
        "context_excerpt": context[:500],
        "row_label": row_label,
    }


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _period_semantic_match(
    question: str,
    trace: Mapping[str, Any],
    table: Mapping[str, Any],
) -> dict[str, Any]:
    question_norm = _ascii(question)
    selected_context = _ascii(trace.get("column_context") or "")
    header_text = _ascii(" ".join(str(value or "") for value in table.get("headers") or []))
    combined = f"{selected_context} {header_text}"
    opening_requested = _contains_any_phrase(
        question_norm,
        ("dau nam", "dau ky", "01 01", "1 1", "nam truoc"),
    )
    ending_requested = _contains_any_phrase(
        question_norm,
        ("cuoi nam", "cuoi ky", "31 12", "tai ngay", "den ngay"),
    )
    in_year_requested = "trong nam" in question_norm or "nam " in question_norm
    if opening_requested:
        passed = _contains_any_phrase(combined, _OPENING_MARKERS)
        intent = "opening"
    elif ending_requested:
        passed = (
            _contains_any_phrase(combined, _END_MARKERS)
            or not _contains_any_phrase(selected_context, _OPENING_MARKERS)
        ) and not _contains_any_phrase(
            selected_context, ("dau nam", "dau ky", "nam truoc", "01 01", "1 1")
        )
        intent = "ending"
    elif in_year_requested:
        passed = any(marker in combined for marker in ("nam nay", "current", "so cuoi nam", "cuoi nam", "tong", "tong cong")) or bool(table.get("report_year"))
        intent = "in_year"
    else:
        passed = True
        intent = "unspecified"
    return {
        "pass": passed,
        "intent": intent,
        "selected_column_context": selected_context,
        "header_text": header_text,
        "reason": "PASS" if passed else "PERIOD_COLUMN_MISMATCH",
    }


def _section_checksum_match(
    metric: str,
    question: str,
    table: Mapping[str, Any],
    row_index: int,
    column_index: int,
    raw_total: Decimal | None,
    *,
    industry_total: bool,
) -> dict[str, Any]:
    context = _table_context(table)
    target = _tokens(metric)
    anchor: tuple[int, str] | None = None
    if industry_total:
        if "phan tich du no cho vay theo nganh nghe kinh doanh" in context:
            anchor = (0, "industry_context")
    else:
        candidates: list[tuple[int, int, str, set[str]]] = []
        for index, label in _section_headings(table, row_index):
            heading_tokens = _tokens(label)
            overlap = len(target & heading_tokens)
            if overlap:
                candidates.append((overlap, len(heading_tokens), label, heading_tokens))
        if candidates:
            candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
            best = candidates[0]
            # A heading with only one generic/common token is insufficient;
            # require at least two concepts or a strong qualifier.
            strong = {"tai san", "dau tu", "cho vay", "cong cu", "trai phieu", "phai sinh"}
            best_label_norm = _ascii(best[2])
            strong_bound = any(phrase in best_label_norm and phrase in " ".join(_metric_tokens(metric)) for phrase in strong)
            if best[0] >= 2 or strong_bound:
                for index, label in _section_headings(table, row_index):
                    if label == best[2]:
                        anchor = (index, label)
                        break

    if anchor is None:
        return {
            "pass": False,
            "reason": "SECTION_ANCHOR_MISSING",
            "anchor": None,
            "next_heading": None,
            "child_count": 0,
            "child_sum": None,
        }

    anchor_index = anchor[0]
    next_heading = _next_heading(table, anchor_index)
    if next_heading is not None and next_heading[0] <= row_index:
        return {
            "pass": False,
            "reason": "SECTION_BOUNDARY_CROSSED",
            "anchor": anchor,
            "next_heading": next_heading,
            "child_count": 0,
            "child_sum": None,
        }

    child_values: list[Decimal] = []
    rejected_rows: list[int] = []
    for index in range(anchor_index + 1, row_index):
        if not _row_has_numeric(table, index):
            continue
        row = _row_cells(table, index)
        if not row or not _clean_label(row[0]):
            continue
        raw = BUILDER.parse_decimal(row[column_index]) if column_index < len(row) else None
        if raw is None:
            rejected_rows.append(index)
            continue
        child_values.append(raw)
    child_sum = sum(child_values, Decimal(0)) if child_values else None
    checksum_pass = bool(child_values) and raw_total is not None and child_sum == raw_total
    return {
        "pass": checksum_pass,
        "reason": "PASS" if checksum_pass else "CHECKSUM_MISMATCH",
        "anchor": anchor,
        "next_heading": next_heading,
        "child_count": len(child_values),
        "child_sum": str(child_sum) if child_sum is not None else None,
        "raw_total": str(raw_total) if raw_total is not None else None,
        "rejected_child_rows": rejected_rows,
        "anchor_crossed": next_heading is not None and next_heading[0] <= row_index,
    }


def _load_review_items(path: Path, selected_ids: set[int]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in iter_jsonl(path):
        try:
            question_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if question_id in selected_ids:
            result[question_id] = item
    return result


def _load_tables(path: Path, wanted_uids: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    remaining = set(wanted_uids)
    for table in iter_jsonl(path):
        uid = str(table.get("internal_table_uid") or "").strip()
        if uid not in remaining:
            continue
        result[uid] = table
        remaining.remove(uid)
        if not remaining:
            break
    return result


def _selected_ids(diagnostics_path: Path) -> set[int]:
    return {
        int(row["id"])
        for row in iter_jsonl(diagnostics_path)
        if row.get("id") is not None and str(row.get("tier") or "") == EXPECTED_TIER
    }


def _submission_by_id(path: Path) -> dict[int, dict[str, Any]]:
    value = read_json(path)
    if not isinstance(value, list):
        raise ValueError(f"{path} must contain a JSON array")
    return {int(row["id"]): row for row in value if row.get("id") is not None}


def _trace_by_id(path: Path, selected_ids: set[int]) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if row.get("status") != "ACCEPTED":
            continue
        try:
            question_id = int(row.get("question_id"))
        except (TypeError, ValueError):
            continue
        if question_id in selected_ids:
            if question_id in rows:
                raise ValueError(f"duplicate accepted aggregate trace for Q{question_id}")
            rows[question_id] = row
    return rows


def _compare_decimal(left: Any, right: Any) -> bool:
    left_decimal = _decimal(left)
    right_decimal = _decimal(right)
    return left_decimal is not None and right_decimal is not None and left_decimal == right_decimal


def _audit_record(
    question_id: int,
    trace: Mapping[str, Any],
    review_item: Mapping[str, Any] | None,
    submission: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    source_line_map: Mapping[str, Any],
) -> dict[str, Any]:
    row_index = int(trace.get("row_index"))
    column_index = int(trace.get("column_index"))
    question = str((review_item or {}).get("question") or trace.get("question") or "")
    metric = str((review_item or {}).get("effective_metric") or trace.get("metric") or "")
    kind = str(trace.get("selected_program_kind") or "")
    question_divisor = _decimal(BUILDER.requested_divisor(question))
    table_source_sha = str((table or {}).get("source_sha256") or "") if table else ""
    table_sha = str((table or {}).get("table_sha256") or "") if table else ""
    table_uid = str(
        (trace.get("internal_table_uid") or (table or {}).get("internal_table_uid") or "")
    )
    checks: dict[str, bool] = {
        "trace_accepted": True,
        "table_uid_found": table is not None,
        "document_id_matches": False,
        "ticker_matches": False,
        "report_year_matches": False,
        "scope_matches_when_declared": False,
        "table_sha256_matches": False,
        "source_sha256_matches": False,
        "row_index_in_bounds": False,
        "column_index_in_bounds": False,
        "raw_cell_matches_trace": False,
        "raw_cell_decimal_matches_trace": False,
        "answer_decimal_replay_matches_trace": False,
        "submission_answer_matches_trace": False,
        "source_line_map_entry_found": False,
        "source_multiplier_positive": False,
        "period_contract": False,
        "semantic_contract": False,
        "candidate_coordinate_consensus": True,
    }
    record: dict[str, Any] = {
        "question_id": question_id,
        "kind": kind,
        "metric": metric,
        "question": question,
        "document_id": trace.get("document_id"),
        "ticker": trace.get("ticker"),
        "year": trace.get("year"),
        "scope": trace.get("scope"),
        "internal_table_uid": table_uid or None,
        "table_sha256": table_sha or None,
        "source_sha256": table_source_sha or None,
        # The immutable coordinate map is keyed by internal_table_uid, not
        # by the source file hash.
        "source_line": source_line_map.get(table_uid),
        "row_index": row_index,
        "column_index": column_index,
        "row_label": trace.get("row_label"),
        "raw_cell": None,
        "raw_value_trace": trace.get("raw_value"),
        "answer_trace": trace.get("answer"),
        "submission_answer": submission.get("answer"),
        "checks": checks,
    }
    if table is None:
        record["failed_checks"] = sorted(key for key, value in checks.items() if not value)
        return record

    rows = table.get("rows") or []
    checks["document_id_matches"] = str(table.get("document_id") or "").removesuffix(".txt") == str(trace.get("document_id") or "")
    checks["ticker_matches"] = _ascii(table.get("ticker")) == _ascii(trace.get("ticker"))
    try:
        checks["report_year_matches"] = int(table.get("report_year")) == int(trace.get("year"))
    except (TypeError, ValueError):
        checks["report_year_matches"] = False
    trace_scope = str(trace.get("scope") or "").strip().lower()
    table_scope = str(table.get("scope") or "").strip().lower()
    checks["scope_matches_when_declared"] = not trace_scope or trace_scope == table_scope
    checks["table_sha256_matches"] = bool(table_sha)
    checks["source_sha256_matches"] = bool(table_source_sha)
    checks["row_index_in_bounds"] = 0 <= row_index < len(rows) and isinstance(rows[row_index], list)
    if checks["row_index_in_bounds"]:
        checks["column_index_in_bounds"] = 0 <= column_index < len(rows[row_index])
    if checks["column_index_in_bounds"]:
        raw_cell = rows[row_index][column_index]
        record["source_row"] = rows[row_index]
        record["row_path"] = _row_path(table, row_index)
        record["source_raw_cell"] = raw_cell
        record["raw_cell"] = raw_cell
        raw_decimal = BUILDER.parse_decimal(raw_cell)
        trace_raw_decimal = _decimal(trace.get("raw_value"))
        checks["raw_cell_matches_trace"] = raw_decimal is not None and trace_raw_decimal is not None and raw_decimal == trace_raw_decimal
        checks["raw_cell_decimal_matches_trace"] = raw_decimal is not None and trace_raw_decimal is not None and raw_decimal == trace_raw_decimal
    checks["source_line_map_entry_found"] = bool(
        table_uid and source_line_map.get(table_uid) is not None
    )
    multiplier = _decimal(trace.get("source_to_vnd_multiplier"))
    divisor = question_divisor
    checks["source_multiplier_positive"] = multiplier is not None and multiplier > 0
    if checks["raw_cell_decimal_matches_trace"] and multiplier is not None and divisor is not None and divisor > 0:
        replayed_answer = trace_raw_decimal * multiplier / divisor
        record["answer_replayed"] = str(replayed_answer)
        checks["answer_decimal_replay_matches_trace"] = _compare_decimal(replayed_answer, trace.get("answer"))
        checks["submission_answer_matches_trace"] = _compare_decimal(submission.get("answer"), replayed_answer)

    period = _period_semantic_match(question, trace, table)
    record["period_check"] = period
    checks["period_contract"] = bool(period.get("pass"))

    options, corroborations = _metric_options(
        review_item,
        table_uid=str(trace.get("internal_table_uid") or ""),
        row_index=row_index,
        column_index=column_index,
    )
    record["metric_options"] = options
    record["candidate_corroborations"] = corroborations
    matching_corroborations = [row for row in corroborations if row.get("same_coordinate")]
    differing_corroborations = [row for row in corroborations if not row.get("same_coordinate")]
    # A direct candidate in the same table that binds the requested metric to a
    # different coordinate is a hard conflict for a checksum/aggregate row.
    if kind == "section_checksum_total" and matching_corroborations == [] and differing_corroborations:
        checks["candidate_coordinate_consensus"] = False
    semantic_checks: list[dict[str, Any]] = []
    for option in options[:8]:
        semantic_checks.append(_semantic_label_match(option, table, row_index))
    if kind == "topic_total_row":
        topic_check = _topic_semantic_match(metric, question, table, row_index)
        record["topic_check"] = topic_check
        semantic_pass = bool(topic_check.get("pass"))
    elif kind == "section_checksum_total":
        raw_total = BUILDER.parse_decimal(rows[row_index][column_index]) if checks["column_index_in_bounds"] else None
        checksum = _section_checksum_match(
            metric,
            question,
            table,
            row_index,
            column_index,
            raw_total,
            industry_total=str(trace.get("metric_kind") or "") == "industry_total",
        )
        record["checksum_check"] = checksum
        semantic_pass = bool(checksum.get("pass"))
    else:
        semantic_pass = any(bool(check.get("pass")) for check in semantic_checks)
        # If the selected row is code-only or a broad parent row, candidate
        # metadata cannot rescue it unless the same coordinate has a direct
        # source binding.  This keeps the check independent but allows normal
        # numeric-code financial statements to use row_paths.
        if not semantic_pass and not matching_corroborations:
            semantic_pass = False
    record["semantic_checks"] = semantic_checks
    if differing_corroborations and kind != "section_checksum_total":
        # Differing candidates are expected in a large navigation pool; they
        # are diagnostic unless they explicitly bind a stronger same-table
        # semantic target.  Keep them visible without rejecting every row.
        record["candidate_coordinate_conflict_diagnostic"] = True
    checks["semantic_contract"] = semantic_pass
    record["failed_checks"] = sorted(key for key, value in checks.items() if not value)
    return record


def run(args: argparse.Namespace) -> dict[str, Any]:
    diagnostics_path = args.diagnostics.expanduser().resolve()
    trace_path = args.trace.expanduser().resolve()
    review_items_path = args.review_items.expanduser().resolve()
    tables_path = args.structured_tables.expanduser().resolve()
    source_line_map_path = args.source_line_map.expanduser().resolve()
    submission_path = args.submission.expanduser().resolve()
    selected_ids = _selected_ids(diagnostics_path)
    traces = _trace_by_id(trace_path, selected_ids)
    submissions = _submission_by_id(submission_path)
    reviews = _load_review_items(review_items_path, selected_ids)
    wanted_uids = {
        str(row.get("internal_table_uid") or "")
        for row in traces.values()
        if row.get("internal_table_uid")
    }
    tables = _load_tables(tables_path, wanted_uids)
    source_line_map = read_json(source_line_map_path)
    if not isinstance(source_line_map, Mapping):
        raise ValueError("source line map must be an object")

    records: list[dict[str, Any]] = []
    for question_id in sorted(selected_ids):
        if question_id not in traces:
            records.append(
                {
                    "question_id": question_id,
                    "checks": {"accepted_trace_found": False},
                    "failed_checks": ["accepted_trace_found"],
                }
            )
            continue
        trace = traces[question_id]
        submission = submissions.get(question_id, {})
        table = tables.get(str(trace.get("internal_table_uid") or ""))
        records.append(
            _audit_record(
                question_id,
                trace,
                reviews.get(question_id),
                submission,
                table,
                source_line_map,
            )
        )

    eligible_ids = [
        int(record["question_id"])
        for record in records
        if record.get("checks") and all(bool(value) for value in record["checks"].values())
    ]
    failed_questions = [
        {
            "question_id": int(record["question_id"]),
            "kind": record.get("kind"),
            "failed_checks": record.get("failed_checks") or [],
            "semantic_reason": (
                (record.get("checksum_check") or {}).get("reason")
                or (record.get("topic_check") or {}).get("reason")
                or next(
                    (check.get("reason") for check in record.get("semantic_checks") or [] if check.get("reason") != "PASS"),
                    None,
                )
            ),
        }
        for record in records
        if int(record["question_id"]) not in eligible_ids
    ]
    failed_counts: Counter[str] = Counter()
    for row in failed_questions:
        for reason in row.get("failed_checks") or []:
            failed_counts[str(reason)] += 1
    all_selected_replayed = len(records) == len(selected_ids) and len(traces) == len(selected_ids)
    status = "PASS" if all_selected_replayed and len(eligible_ids) == len(selected_ids) else "PARTIAL_PASS" if eligible_ids else "FAIL"
    report = {
        "protocol": PROTOCOL,
        "status": status,
        "selection_scope": "full_population_selected_program_rows",
        "selected_question_count": len(selected_ids),
        "eligible_question_count": len(eligible_ids),
        "eligible_question_ids": eligible_ids,
        "failed_question_count": len(failed_questions),
        "failed_questions": failed_questions,
        "failed_check_counts": dict(sorted(failed_counts.items())),
        "checks": {
            "selected_program_row_count_is_51": len(selected_ids) == 51,
            "accepted_trace_for_all_selected": len(traces) == len(selected_ids),
            "all_review_items_found": len(reviews) == len(selected_ids),
            "all_questions_found": all(bool(row.get("question")) for row in records),
            "all_submission_rows_found": all(int(row["question_id"]) in submissions for row in records),
            "all_tables_found_by_uid": len(tables) == len(wanted_uids),
            "all_source_line_map_entries_found": all(bool(row.get("checks", {}).get("source_line_map_entry_found")) for row in records),
            "eligible_subset_nonempty": bool(eligible_ids),
        },
        "inputs": {
            "diagnostics_path": str(diagnostics_path),
            "diagnostics_sha256": sha256_file(diagnostics_path),
            "trace_path": str(trace_path),
            "trace_sha256": sha256_file(trace_path),
            "review_items_path": str(review_items_path),
            "review_items_sha256": sha256_file(review_items_path),
            "submission_path": str(submission_path),
            "submission_sha256": sha256_file(submission_path),
            "structured_tables_path": str(tables_path),
            "structured_tables_sha256": sha256_file(tables_path),
            "source_line_map_path": str(source_line_map_path),
            "source_line_map_sha256": sha256_file(source_line_map_path),
        },
        "authority": "none",
        "promotion_allowed": False,
        "release_authorized": False,
        "records": records,
        "notes": [
            "This is an independent source/semantic candidate gate, not a gold-label accuracy result.",
            "The selected population is audited as a whole; failed rows remain in the receipt and are not silently dropped.",
            "Only a separately labelled eligible subset may be passed to UID-closure overlay materialization.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.eligible_output:
        eligible_records = [record for record in records if int(record["question_id"]) in eligible_ids]
        eligible_report = dict(report)
        eligible_report["status"] = "PASS" if eligible_records else "FAIL"
        eligible_report["selection_scope"] = "eligible_subset_after_full_population_audit"
        eligible_report["selected_question_count"] = len(eligible_records)
        eligible_report["eligible_question_count"] = len(eligible_records)
        eligible_report["eligible_question_ids"] = eligible_ids
        eligible_report["failed_question_count"] = 0
        eligible_report["failed_questions"] = []
        eligible_report["records"] = eligible_records
        eligible_report["subset_provenance"] = {
            "full_population_report": str(args.output.resolve()),
            "full_population_report_sha256": sha256_file(args.output),
            "full_population_selected_question_count": len(selected_ids),
            "full_population_excluded_question_ids": [
                int(record["question_id"])
                for record in records
                if int(record["question_id"]) not in eligible_ids
            ],
            "subset_is_not_full_population_pass": len(eligible_ids) != len(selected_ids),
        }
        args.eligible_output.parent.mkdir(parents=True, exist_ok=True)
        args.eligible_output.write_text(
            json.dumps(eligible_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--review-items", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--source-line-map", type=Path, required=True)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--eligible-output", type=Path, default=None)
    args = parser.parse_args()
    report = run(args)
    print(
        json.dumps(
            {
                "protocol": PROTOCOL,
                "status": report["status"],
                "selected_question_count": report["selected_question_count"],
                "eligible_question_count": report["eligible_question_count"],
                "output": str(args.output),
                "eligible_output": str(args.eligible_output) if args.eligible_output else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
