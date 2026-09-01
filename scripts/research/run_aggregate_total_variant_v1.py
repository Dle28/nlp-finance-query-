#!/usr/bin/env python3
"""Run a strict source-cell ablation for aggregate direct lookups.

Several direct questions contain an exact metric row but the source table has
multiple segment, maturity, currency, or adjustment columns.  Learned
retrieval frequently returns a valid cell from the row while selecting a
bucket instead of the aggregate column.  Other questions ask for a section
total whose label is only ``Cộng``/``Tổng cộng`` or blank.  This adapter binds
the answer to the aggregate row/column and, for blank-label sections, checks
the total against the Decimal sum of its detail rows.

The route is family-based and fail-closed.  It requires a direct lookup with
one ticker/year, a source-declared unit, an exact or semantically bounded
metric row/context, and a current-period cell.  Unscoped separate and
consolidated candidates are accepted only when their Decimal answers agree.
The result is an authorized best-effort candidate, not a strict VERIFIED
release or an official score.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


VARIANT_PROTOCOL = "vifinqa_aggregate_total_strict_v1"
AGGREGATE_TOTAL_TIER = "program_aggregate_total_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_aggregate_total"


def _load_helper() -> Any:
    helper_path = Path(__file__).with_name("run_named_compensation_variant_v1.py")
    spec = importlib.util.spec_from_file_location(HELPER_NAME, helper_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load variant helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[HELPER_NAME] = module
    spec.loader.exec_module(module)
    return module


HELPER = _load_helper()
BUILDER = HELPER.BUILDER
_ORIGINAL_PROGRAM_AWARE = HELPER._ORIGINAL_PROGRAM_AWARE
_ORIGINAL_ROUTE_PRIORITY = HELPER._ORIGINAL_ROUTE_PRIORITY
_ORIGINAL_SOURCE_FIRST_INDEX = BUILDER.build_source_first_direct_lookup_index

_TRACE: list[dict[str, Any]] = []
_STATS: Counter[str] = Counter()
_LAST_SELECTIONS: dict[int, dict[str, Any]] = {}
_EARLY_ATTEMPTED: set[int] = set()
_EARLY_RESULTS: dict[int, tuple[Decimal, list[dict[str, Any]], str, str] | None] = {}

_SAFE_TABLE_KINDS = {
    "balance_sheet",
    "debt_schedule",
    "financial_data_schedule",
    "financial_note",
    "financial_note_detail",
    "income_statement",
    "investment_schedule",
    "related_party_schedule",
    "segment_reporting",
}
_ENDING_CUES = (
    "cuoi nam",
    "cuoi ky",
    "den ngay",
    "tai ngay",
    "ket thuc",
    "31/12",
    "31 thang 12",
)
_AGGREGATE_LABELS = {"tong", "tong cong", "cong", "total", "sum"}
_AGGREGATE_HEADER_MARKERS = {
    "tong",
    "tong cong",
    "cong",
    "total",
    "sum",
}
_METRIC_STRIP_RE = re.compile(
    r"\b(?:tong|so du|so cuoi|so dau|cuoi nam|cuoi ky|dau nam|dau ky|"
    r"den ngay|tai ngay|ket thuc|nam|ngay|thang|19\d{2}|20\d{2}|"
    r"trieu|million|nghin|ngan|thousand|ty|ti|billion|tram|dong|vnd)\b"
)
_SOURCE_PERIOD_DATE_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_METRIC_CONTEXT_TOKENS = {
    "cua",
    "trong",
    "vao",
    "la",
    "bao",
    "nhieu",
    "voi",
}


def _question_id(item: Mapping[str, Any]) -> int | None:
    for key in ("id", "question_id"):
        try:
            if item.get(key) is not None:
                return int(item[key])
        except (TypeError, ValueError):
            continue
    plan = item.get("question_plan")
    if isinstance(plan, Mapping):
        try:
            if plan.get("question_id") is not None:
                return int(plan["question_id"])
        except (TypeError, ValueError):
            pass
    return None


def _normalize(value: Any) -> str:
    return HELPER._normalize(value)


def _row_label(row: Any) -> str:
    return HELPER._row_label(row)


def _single_ticker(plan: Mapping[str, Any]) -> str:
    return HELPER._single_ticker(plan)


def _single_year(plan: Mapping[str, Any]) -> int | None:
    return HELPER._single_year(plan)


def _clean_label(value: Any) -> str:
    label = _normalize(value)
    label = re.sub(r"^(?:\d+|[ivxlcdm]+|[a-z])[.)]?\s+", "", label)
    label = re.sub(r"\s*\(?[*†‡]+\)?\s*$", "", label)
    return re.sub(r"\s+", " ", label).strip()


def _metric_core(value: Any) -> str:
    normalized = _clean_label(value)
    normalized = normalized.replace("cua", " ")
    normalized = _METRIC_STRIP_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _metric_row_match(
    row: list[Any], metric: str, *, ticker: str | None = None
) -> bool:
    row_core = _metric_core(_row_label(row))
    metric_core = _metric_core(metric)
    if not row_core or not metric_core:
        return False
    row_tokens = set(row_core.split())
    metric_tokens = set(metric_core.split())
    metric_tokens -= _METRIC_CONTEXT_TOKENS
    if ticker:
        metric_tokens.discard(_normalize(ticker))
    if "thuan" in metric_tokens and "thuan" not in row_tokens:
        return False
    if "thuong" in metric_tokens and "thuong" not in row_tokens:
        return False
    if "khac" in metric_tokens and "khac" not in row_tokens:
        return False
    if not row_tokens or not row_tokens.issubset(metric_tokens):
        return False
    # Financial statements routinely abbreviate ``giá vốn hàng bán`` to the
    # stable row label ``giá vốn``.  Keep this explicit exception narrow so a
    # generic row cannot satisfy a different requested metric.
    if row_core == "gia von" and {"gia", "von"}.issubset(metric_tokens):
        return True
    # Permit the source's conventional shortening ``giá vốn`` for a question
    # phrased ``giá vốn hàng bán``, but do not let a one-word generic row such
    # as ``doanh thu`` stand in for a requested ``doanh thu thuần``.
    overlap_ratio = len(row_tokens) / max(1, len(metric_tokens))
    # The effective metric may still carry the issuer name and interrogative
    # wording when upstream planning did not canonicalize it.  Requiring all
    # row tokens plus at least a 35% overlap preserves the semantic anchor
    # without making the family depend on a clean planner metric.
    return overlap_ratio >= 0.35


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)
    effective_metric = str(item.get("effective_metric") or "").strip()
    if not effective_metric:
        operands = plan.get("operands") or []
        if operands and isinstance(operands[0], Mapping):
            effective_metric = str(operands[0].get("metric") or "").strip()
    if not effective_metric:
        effective_metric = question

    industry = "du no cho vay theo nganh nghe kinh doanh" in question_norm
    # ``Tổng Công ty`` is an issuer name, not an aggregate request.  Only
    # treat a standalone total cue plus a segment-style revenue/cost metric as
    # this family; generic balance-sheet rows belong to their own route
    # families and must not be reinterpreted here.
    total_question_text = re.sub(r"\btong\s+cong\s+ty\b", " ", question_norm)
    explicit_total = bool(
        re.search(r"\btong(?:\s+(?:so|gia|doanh|chi|loi|nguon|du|tien))?\b", total_question_text)
    )
    topical = any(
        marker in question_norm
        for marker in (
            "quy binh on gia xang dau",
            "gia tri trai phieu",
            "gia tri hop dong cong cu phai sinh",
        )
    )
    row_or_total = explicit_total and any(
        marker in question_norm for marker in ("doanh thu", "gia von")
    )
    if not (industry or topical or row_or_total):
        return None
    if not any(cue in question_norm for cue in _ENDING_CUES) and not re.search(
        r"\b(?:19|20)\d{2}\b", question_norm
    ):
        return None

    ticker = _single_ticker(plan)
    year = _single_year(plan)
    if not ticker or year is None:
        return None
    raw_scope = str(plan.get("scope") or plan.get("reporting_scope") or "")
    scope = raw_scope.strip().lower()
    if scope not in {"", "separate", "consolidated", "aggregated"}:
        return None

    if industry:
        metric_kind = "industry_total"
    elif topical:
        metric_kind = "topic_total"
    else:
        metric_kind = "exact_metric_aggregate"
    return {
        "question_id": _question_id(item),
        "question": question,
        "question_norm": question_norm,
        "ticker": ticker,
        "year": year,
        "scope": scope,
        "metric": effective_metric,
        "metric_kind": metric_kind,
        "output_divisor": BUILDER.requested_divisor(question),
    }


def _table_kind(table: Mapping[str, Any]) -> str:
    classifier = getattr(BUILDER.source_first_lookup_module, "_table_kind", None)
    raw = classifier(table) if callable(classifier) else ""
    if not raw:
        function = table.get("table_function")
        raw = function.get("kind") if isinstance(function, Mapping) else function
    return _normalize(raw).replace("_", " ")


def _table_is_safe(table: Mapping[str, Any]) -> bool:
    return _table_kind(table).replace(" ", "_") in _SAFE_TABLE_KINDS


def _table_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    multiplier = HELPER._table_multiplier(table)
    if multiplier is not None:
        return multiplier
    # ``unit_labels`` is source-side metadata emitted by the table extractor.
    # It is not an inferred magnitude, so it is safe to use as a declared-unit
    # fallback when headers were normalized away.
    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        multiplier_fn = getattr(BUILDER, "_unit_multiplier_from_text", None)
        labels = trace.get("unit_labels") or []
        if callable(multiplier_fn) and isinstance(labels, (list, tuple)):
            for label in labels:
                multiplier = multiplier_fn(str(label or ""))
                if multiplier is not None:
                    return multiplier
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
        unit_labels = trace.get("unit_labels") or []
        if isinstance(unit_labels, list):
            values.extend(str(value or "") for value in unit_labels)
    for field in ("headers", "column_labels"):
        raw = table.get(field) or []
        if isinstance(raw, Mapping):
            values.extend(str(value or "") for value in raw.values())
        elif isinstance(raw, (list, tuple)):
            values.extend(str(value or "") for value in raw)
        elif raw:
            values.append(str(raw))
    section = table.get("table_section") or ""
    if isinstance(section, Mapping):
        values.extend(str(value or "") for value in section.values())
    elif section:
        values.append(str(section))
    for row in (table.get("rows") or [])[:20]:
        if isinstance(row, list):
            values.append(_row_label(row))
    return _normalize(" ".join(values))


def _source_years(value: Any) -> set[int]:
    return {int(match.group(0)) for match in _SOURCE_PERIOD_DATE_RE.finditer(str(value or ""))}


def _source_current_year_matches(table: Mapping[str, Any], year: int) -> bool:
    """Reject comparative tables whose document id carries the current year.

    The structured corpus can retain a comparative schedule under the same
    document id/report year as the filing.  A numbered topic label is the
    strongest source-side period signal, followed by the source title and
    explicit period labels.  Falling back to the indexed report year keeps
    sparse legacy tables eligible.
    """

    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        topic = trace.get("topic") or {}
        topic_label = (
            topic.get("label")
            if isinstance(topic, Mapping)
            else topic
        )
        topic_years = _source_years(topic_label)
        if topic_years:
            return int(year) in topic_years

        title_years = _source_years(trace.get("source_title"))
        if title_years:
            return int(year) in title_years

        period_labels = trace.get("period_labels") or []
        period_years = _source_years(" ".join(str(value or "") for value in period_labels))
        if period_years:
            return int(year) in period_years

    try:
        report_year = int(table.get("report_year"))
    except (TypeError, ValueError):
        report_year = None
    if report_year is not None:
        return report_year == int(year)
    document_year = getattr(BUILDER, "document_year", lambda value: None)(
        table.get("document_id")
    )
    try:
        return document_year is not None and int(document_year) == int(year)
    except (TypeError, ValueError):
        return False


def _choose_cell(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> tuple[int, Decimal, list[dict[str, Any]], str] | None:
    return HELPER.HELPER._choose_cell(
        table,
        row_index=row_index,
        row=row,
        spec=spec,
    )


def _header_aggregate_indices(table: Mapping[str, Any], row: list[Any]) -> set[int]:
    indices: set[int] = set()
    header_rows: list[list[Any]] = []
    for candidate in (table.get("rows") or [])[:6]:
        if isinstance(candidate, list):
            header_rows.append(candidate)
    for field in ("headers", "column_labels"):
        raw = table.get(field) or []
        if isinstance(raw, list):
            header_rows.append(raw)
    for header in header_rows:
        for index, cell in enumerate(header):
            normalized = _normalize(cell)
            if normalized in _AGGREGATE_HEADER_MARKERS:
                indices.add(index)
    numeric_indices = {
        index
        for index, cell in enumerate(row)
        if index > 0 and BUILDER.parse_decimal(cell) is not None
    }
    if indices & numeric_indices:
        return indices & numeric_indices

    # Segment tables sometimes lose the final ``Tổng`` header but retain
    # ``Điều chỉnh và loại trừ``.  Only a question with an explicit aggregate
    # cue can use the last numeric column in that shape.
    header_text = _normalize(
        " ".join(
            str(value)
            for field in ("headers", "column_labels")
            for value in (table.get(field) or [])
        )
    )
    if "loai tru" in header_text or "dieu chinh" in header_text:
        return {max(numeric_indices)} if numeric_indices else set()
    return set()


def _aggregate_choice(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> tuple[int, Decimal, list[dict[str, Any]], str] | None:
    chosen = _choose_cell(table, row_index=row_index, row=row, spec=spec)
    if chosen is None:
        return None
    aggregate_indices = _header_aggregate_indices(table, row)
    if not aggregate_indices:
        return chosen
    if chosen[0] in aggregate_indices:
        return chosen

    compatible: list[tuple[float, int, Decimal]] = []
    for index in sorted(aggregate_indices):
        raw = BUILDER.parse_decimal(row[index])
        if raw is None:
            continue
        period_info_fn = getattr(BUILDER, "_semantic_column_period_info", None)
        info = (
            period_info_fn(
                table,
                chosen[2],
                column_index=int(index),
                row_index=int(row_index),
            )
            if callable(period_info_fn)
            else {}
        )
        years = info.get("years") or set()
        if years and int(spec["year"]) not in years:
            continue
        score = 0.0
        if int(spec["year"]) in years:
            score += 5.0
        if any(marker in str(info.get("context") or "") for marker in ("so cuoi nam", "cuoi nam", "cuoi ky", "31 12")):
            score += 3.0
        if any(marker in str(info.get("context") or "") for marker in ("so dau nam", "dau nam", "dau ky", "1 1")):
            score -= 4.0
        compatible.append((score, index, raw))
    if not compatible:
        return None
    compatible.sort(key=lambda value: (value[0], -value[1]), reverse=True)
    if len(compatible) > 1 and abs(compatible[0][0] - compatible[1][0]) < 0.18:
        return None
    index = compatible[0][1]
    context = str(
        BUILDER.semantic_column_context(
            table,
            chosen[2],
            column_index=int(index),
            row_index=int(row_index),
        )
    )
    return index, compatible[0][2], chosen[2], _normalize(context)


def _row_candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
    program_kind: str,
    force_aggregate: bool,
) -> dict[str, Any] | None:
    chosen = (
        _aggregate_choice(table, row_index=row_index, row=row, spec=spec)
        if force_aggregate
        else _choose_cell(table, row_index=row_index, row=row, spec=spec)
    )
    if chosen is None:
        return None
    column_index, raw_value, evidence, column_context = chosen
    multiplier = _table_multiplier(table)
    if multiplier is None:
        return None
    answer = raw_value * multiplier / spec["output_divisor"]
    return {
        "answer": answer,
        "raw_value": raw_value,
        "source_multiplier": multiplier,
        "row_index": int(row_index),
        "column_index": int(column_index),
        "row_label": _row_label(row),
        "column_context": column_context,
        "evidence": evidence,
        "table": table,
        "kind": _table_kind(table).replace(" ", "_"),
        "program_kind": program_kind,
        "context": _table_context(table),
    }


def _topic_row_match(table: Mapping[str, Any], row: list[Any], spec: Mapping[str, Any]) -> bool:
    context = _table_context(table)
    question = spec["question_norm"]
    if "quy binh on gia xang dau" in question:
        return "quy binh on gia xang dau" in context and _clean_label(_row_label(row)) == "so du cuoi nam"
    if "gia tri trai phieu" in question:
        return (
            "trai phieu thuong" in context
            and "chi phi phai tra" not in context
            and _clean_label(_row_label(row)) in {"tong cong", "cong"}
        )
    if "gia tri hop dong cong cu phai sinh" in question:
        return (
            "cong cu tai chinh phai sinh" in context
            and "tong gia tri hop dong" in context
            and _clean_label(_row_label(row)) in _AGGREGATE_LABELS
        )
    return False


def _section_start(table: Mapping[str, Any], spec: Mapping[str, Any]) -> int | None:
    rows = table.get("rows") or []
    if spec["metric_kind"] == "industry_total":
        context = _table_context(table)
        return 0 if "phan tich du no cho vay theo nganh nghe kinh doanh" in context else None
    metric_core = _metric_core(spec["metric"])
    matches: list[int] = []
    for index, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        label_core = _metric_core(_row_label(row))
        if not label_core or not metric_core:
            continue
        tokens = set(label_core.split())
        metric_tokens = set(metric_core.split())
        if tokens and tokens.issubset(metric_tokens) and len(tokens) >= 3:
            matches.append(index)
    return matches[-1] if matches else None


def _checksum_section_candidate(
    table: Mapping[str, Any], *, spec: Mapping[str, Any]
) -> dict[str, Any] | None:
    section_index = _section_start(table, spec)
    if section_index is None:
        return None
    rows = table.get("rows") or []
    total_candidates: list[int] = []
    for index in range(section_index, len(rows)):
        row = rows[index]
        if not isinstance(row, list):
            continue
        label = _clean_label(_row_label(row))
        if label and label not in _AGGREGATE_LABELS:
            continue
        if any(BUILDER.parse_decimal(cell) is not None for cell in row[1:]):
            if not any(
                isinstance(later, list)
                and any(BUILDER.parse_decimal(cell) is not None for cell in later[1:])
                for later in rows[index + 1 :]
            ):
                total_candidates.append(index)
    if not total_candidates:
        return None
    total_index = total_candidates[-1]
    total_row = rows[total_index]
    chosen_total = _choose_cell(
        table, row_index=total_index, row=total_row, spec=spec
    )
    if chosen_total is None:
        return None
    total_column, total_raw, evidence, column_context = chosen_total
    child_values: list[Decimal] = []
    for index in range(section_index + 1, total_index):
        row = rows[index]
        if not isinstance(row, list) or not _clean_label(_row_label(row)):
            continue
        if not any(BUILDER.parse_decimal(cell) is not None for cell in row[1:]):
            continue
        chosen_child = _choose_cell(table, row_index=index, row=row, spec=spec)
        if chosen_child is None or chosen_child[0] != total_column:
            return None
        child_values.append(chosen_child[1])
    if not child_values or sum(child_values, Decimal(0)) != total_raw:
        _STATS["checksum_rejected"] += 1
        return None
    multiplier = _table_multiplier(table)
    if multiplier is None:
        return None
    answer = total_raw * multiplier / spec["output_divisor"]
    return {
        "answer": answer,
        "raw_value": total_raw,
        "source_multiplier": multiplier,
        "row_index": int(total_index),
        "column_index": int(total_column),
        "row_label": _row_label(total_row),
        "column_context": column_context,
        "evidence": evidence,
        "table": table,
        "kind": _table_kind(table).replace(" ", "_"),
        "program_kind": "section_checksum_total",
        "context": _table_context(table),
        "checksum_child_count": len(child_values),
        "checksum_child_sum": str(sum(child_values, Decimal(0))),
    }


def _candidate(
    table: Mapping[str, Any], *, spec: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if not _table_is_safe(table):
        return []
    if not _source_current_year_matches(table, int(spec["year"])):
        _STATS["table_rejected_stale_source_period"] += 1
        return []
    rows = table.get("rows") or []
    results: list[dict[str, Any]] = []
    if spec["metric_kind"] in {"industry_total"}:
        checked = _checksum_section_candidate(table, spec=spec)
        return [checked] if checked is not None else []

    if spec["metric_kind"] == "topic_total":
        for index, row in enumerate(rows):
            if not isinstance(row, list) or not _topic_row_match(table, row, spec):
                continue
            candidate = _row_candidate(
                table,
                row_index=index,
                row=row,
                spec=spec,
                program_kind="topic_total_row",
                force_aggregate=False,
            )
            if candidate is not None:
                results.append(candidate)
        return results

    metric_core = _metric_core(spec["metric"])
    for index, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        label = _clean_label(_row_label(row))
        if label and _metric_row_match(
            row, spec["metric"], ticker=str(spec.get("ticker") or "")
        ):
            # Primary-statement totals must stay in the balance-sheet family;
            # otherwise similarly named risk/segment schedules can conflict.
            kind = _table_kind(table).replace(" ", "_")
            if "tai san" in metric_core and kind != "balance_sheet":
                continue
            candidate = _row_candidate(
                table,
                row_index=index,
                row=row,
                spec=spec,
                program_kind="exact_metric_row",
                force_aggregate=True,
            )
            if candidate is not None:
                results.append(candidate)
    checked = _checksum_section_candidate(table, spec=spec)
    if checked is not None:
        results.append(checked)
    return results


def _candidate_rank(
    candidate: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[int, int, int, str]:
    table = candidate["table"]
    scope = str(BUILDER.table_reporting_scope(table) or "").lower()
    kind_priority = {
        "section_checksum_total": 4,
        "topic_total_row": 3,
        "exact_metric_row": 2,
    }
    return (
        int(bool(spec.get("scope")) and scope == str(spec.get("scope") or "")),
        kind_priority.get(str(candidate.get("program_kind") or ""), 0),
        int(candidate.get("kind") == "balance_sheet"),
        str(table.get("internal_table_uid") or ""),
    )


def _sources_and_selection(
    candidate: Mapping[str, Any], *, spec: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = candidate["table"]
    document_id = str(table.get("document_id") or "").removesuffix(".txt")
    uid = str(table.get("internal_table_uid") or "")
    source = {
        "raw_value_decimal": str(candidate["raw_value"]),
        "value": candidate["answer"],
        "role": "aggregate_total_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "source_context": str(candidate.get("context") or ""),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "requested_output_divisor": str(spec["output_divisor"]),
        "metric_kind": spec["metric_kind"],
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    selection = {
        "score": 71.1,
        "value": candidate["answer"],
        "raw_value": candidate["raw_value"],
        "source_multiplier": candidate["source_multiplier"],
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "column_context": candidate.get("column_context"),
        "document_id": document_id,
        "internal_table_uid": uid,
        "candidate_rank": 0,
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "program_family": "aggregate_total",
        "program_kind": candidate.get("program_kind"),
        "table_kind": candidate.get("kind"),
        "metric_kind": spec["metric_kind"],
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "promotion_allowed": False,
    }
    if candidate.get("navigation_rank") is not None:
        source["candidate_navigation_rank"] = int(candidate["navigation_rank"])
        source["candidate_navigation_tiebreak"] = bool(
            candidate.get("navigation_tiebreak")
        )
        selection["candidate_navigation_rank"] = int(candidate["navigation_rank"])
        selection["candidate_navigation_tiebreak"] = bool(
            candidate.get("navigation_tiebreak")
        )
    if candidate.get("checksum_child_count") is not None:
        selection["checksum_child_count"] = candidate["checksum_child_count"]
        selection["checksum_child_sum"] = candidate["checksum_child_sum"]
    return [source], selection


def _tables_for_spec(
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    spec: Mapping[str, Any],
    *,
    item: Mapping[str, Any] | None = None,
) -> list[Mapping[str, Any]]:
    tables = HELPER._tables_for_spec(tables_by_uid, spec)
    if not item:
        return tables
    navigation_ranks: dict[str, int] = {}
    for raw_candidate in item.get("candidates") or []:
        if not isinstance(raw_candidate, Mapping):
            continue
        uid = str(raw_candidate.get("internal_table_uid") or "").strip()
        if not uid:
            continue
        try:
            rank = int(
                raw_candidate.get("rank")
                if raw_candidate.get("rank") is not None
                else raw_candidate.get("original_retrieval_rank")
            )
        except (TypeError, ValueError):
            continue
        if rank >= 1:
            navigation_ranks[uid] = min(rank, navigation_ranks.get(uid, rank))
    if not navigation_ranks:
        return tables
    hinted_tables = [
        table
        for table in tables
        if str(table.get("internal_table_uid") or "") in navigation_ranks
    ]
    if not hinted_tables:
        _STATS["candidate_navigation_hints_missing_from_asset"] += 1
        return tables
    _STATS["candidate_navigation_filtered_questions"] += 1
    _STATS["candidate_navigation_tables_dropped"] += len(tables) - len(hinted_tables)
    return hinted_tables


def _resolve_aggregate_total(
    item: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    if not tables_by_uid:
        return None
    tables = _tables_for_spec(tables_by_uid, spec, item=item)
    _STATS["candidate_table_count_total"] += len(tables)
    candidates: list[dict[str, Any]] = []
    for table in tables:
        candidates.extend(_candidate(table, spec=spec))
    if not candidates:
        _STATS["family_no_strict_candidate"] += 1
        _TRACE.append(
            {
                "question_id": spec.get("question_id"),
                "status": "REJECTED_NO_STRICT_SOURCE_CANDIDATE",
                "ticker": spec["ticker"],
                "year": spec["year"],
                "scope": spec.get("scope") or None,
                "metric_kind": spec["metric_kind"],
            }
        )
        return None

    answers = {candidate["answer"] for candidate in candidates}
    navigation_tiebreak = False
    if len(answers) != 1:
        conflicting_answers = set(answers)
        conflicting_candidate_count = len(candidates)
        navigation_ranks: dict[str, int] = {}
        for raw_candidate in item.get("candidates") or []:
            if not isinstance(raw_candidate, Mapping):
                continue
            uid = str(raw_candidate.get("internal_table_uid") or "").strip()
            if not uid:
                continue
            try:
                rank = int(
                    raw_candidate.get("rank")
                    if raw_candidate.get("rank") is not None
                    else raw_candidate.get("original_retrieval_rank")
                )
            except (TypeError, ValueError):
                continue
            if rank >= 1:
                navigation_ranks[uid] = min(rank, navigation_ranks.get(uid, rank))
        ranked_candidates = [
            candidate
            for candidate in candidates
            if str(candidate["table"].get("internal_table_uid") or "") in navigation_ranks
        ]
        if ranked_candidates:
            best_navigation_rank = min(
                navigation_ranks[
                    str(candidate["table"].get("internal_table_uid") or "")
                ]
                for candidate in ranked_candidates
            )
            best_navigation_candidates = [
                candidate
                for candidate in ranked_candidates
                if navigation_ranks[
                    str(candidate["table"].get("internal_table_uid") or "")
                ]
                == best_navigation_rank
            ]
            best_navigation_answers = {
                candidate["answer"] for candidate in best_navigation_candidates
            }
            if len(best_navigation_answers) == 1:
                candidates = best_navigation_candidates
                answers = best_navigation_answers
                navigation_tiebreak = True
                _STATS["accepted_candidate_navigation_rank_tiebreak"] += 1
                for candidate in candidates:
                    candidate["navigation_rank"] = best_navigation_rank
                    candidate["navigation_tiebreak"] = True
            else:
                candidates = []
        if not navigation_tiebreak:
            _STATS["rejected_conflicting_duplicate_answers"] += 1
            _TRACE.append(
                {
                    "question_id": spec.get("question_id"),
                    "status": "REJECTED_CONFLICTING_DUPLICATE_ANSWERS",
                    "ticker": spec["ticker"],
                    "year": spec["year"],
                    "scope": spec.get("scope") or None,
                    "metric_kind": spec["metric_kind"],
                    "answers": sorted(str(value) for value in conflicting_answers),
                    "candidate_count": conflicting_candidate_count,
                }
            )
            return None

    selected = sorted(
        candidates,
        key=lambda value: _candidate_rank(value, spec),
        reverse=True,
    )[0]
    sources, selection = _sources_and_selection(selected, spec=spec)
    if navigation_tiebreak:
        selection["candidate_navigation_tiebreak"] = True
    question_id = spec.get("question_id")
    if question_id is not None:
        _LAST_SELECTIONS[int(question_id)] = selection
    answer = selected["answer"]
    _STATS["family_accepted"] += 1
    _STATS[f"accepted_{selected['program_kind']}"] += 1
    _TRACE.append(
        {
            "question_id": spec.get("question_id"),
            "status": "ACCEPTED",
            "ticker": spec["ticker"],
            "year": spec["year"],
            "scope": spec.get("scope") or None,
            "metric_kind": spec["metric_kind"],
            "candidate_count": len(candidates),
            "answer": str(answer),
            "document_id": sources[0]["document_id"],
            "internal_table_uid": sources[0]["internal_table_uid"],
            "row_index": sources[0]["row_index"],
            "column_index": sources[0]["column_index"],
            "raw_value": sources[0]["raw_value_decimal"],
            "source_to_vnd_multiplier": sources[0]["source_to_vnd_multiplier"],
            "selected_program_kind": selected["program_kind"],
            "selected_table_kind": selected["kind"],
            "row_label": selected["row_label"],
            "column_context": selected.get("column_context"),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", AGGREGATE_TOTAL_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        question_id = spec.get("question_id")
        if question_id is not None and int(question_id) in _EARLY_ATTEMPTED:
            result = _EARLY_RESULTS.get(int(question_id))
        else:
            result = _resolve_aggregate_total(item, spec, tables_by_uid=tables_by_uid)
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == AGGREGATE_TOTAL_TIER:
        return 71.1
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _patched_source_first_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[Any, Any],
    **resolver_kwargs: Any,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Overlay accepted aggregate candidates before the builder's route chain.

    The canonical builder checks ``source_first`` before calling
    ``program_aware_answer``.  Without this overlay, a valid aggregate-column
    candidate can be proven locally but never reach the submission lane.  The
    overlay runs only after the immutable source-first index is built, and it
    replaces an entry only when this route independently replays a strict
    source cell.  Direct/exact replay remains earlier in the canonical chain.
    """

    answers, stats = _ORIGINAL_SOURCE_FIRST_INDEX(
        items_by_question,
        tables_by_pair=tables_by_pair,
        **resolver_kwargs,
    )
    tables_by_uid: dict[str, Mapping[str, Any]] = {}
    for group in tables_by_pair.values():
        if not isinstance(group, (list, tuple)):
            continue
        for table in group:
            if not isinstance(table, Mapping):
                continue
            uid = str(table.get("internal_table_uid") or "").strip()
            if uid:
                tables_by_uid[uid] = table

    injected = 0
    for raw_question_id, item in items_by_question.items():
        spec = _family_spec(item)
        if spec is None:
            continue
        try:
            question_id = int(raw_question_id)
        except (TypeError, ValueError):
            continue
        _STATS["family_seen_early"] += 1
        _EARLY_ATTEMPTED.add(question_id)
        result = _resolve_aggregate_total(item, spec, tables_by_uid=tables_by_uid)
        _EARLY_RESULTS[question_id] = result
        if result is None:
            continue
        selection = _LAST_SELECTIONS.get(question_id)
        if selection is None:
            _STATS["accepted_missing_selection"] += 1
            continue
        answer, sources, _query, tier = result
        answers[question_id] = {
            "answer": answer,
            "sources": sources,
            "selection": selection,
            "tier": tier,
            "protocol": VARIANT_PROTOCOL,
            "diagnostics": {
                "ticker": spec["ticker"],
                "report_year": spec["year"],
                "requested_scope": spec.get("scope") or None,
                "match_mode": "aggregate_total_strict_replay",
                "table_kind": selection.get("table_kind"),
                "candidate_source": VARIANT_PROTOCOL,
                "research_candidate_only": True,
                "promotion_allowed": False,
                "answer_authority": "current_structured_table_decimal_replay",
            },
        }
        injected += 1

    if injected:
        _STATS["early_source_first_injected"] += injected
        nested_stats = Counter(stats.get("stats") or {})
        nested_stats["aggregate_total_variant_injected"] += injected
        stats["stats"] = dict(nested_stats)
        stats["answer_count"] = len(answers)
    return answers, stats


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["aggregate_total_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": AGGREGATE_TOTAL_TIER,
        "contract": {
            "family": "direct_lookup",
            "one_ticker_and_one_report_year": True,
            "safe_financial_and_segment_table_kinds_only": True,
            "exact_or_bounded_metric_row_required": True,
            "aggregate_column_required_when_present": True,
            "topic_total_requires_exact_context_and_total_row": True,
            "blank_or_section_total_requires_decimal_checksum": True,
            "current_period_column_required": True,
            "declared_source_unit_required": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "aggregate_total_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    trace_path = output_dir / "aggregate_total_trace_v1.jsonl"
    trace_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in _TRACE
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = BUILDER.configure_parser(argparse.ArgumentParser())
    args = parser.parse_args()
    BUILDER.program_aware_answer = _patched_program_aware
    BUILDER._route_priority = _patched_route_priority
    BUILDER.build_source_first_direct_lookup_index = _patched_source_first_index
    BUILDER.build(args)
    _write_variant_metadata(args.output)
    print(
        json.dumps(
            {
                "protocol": VARIANT_PROTOCOL,
                "output": str(args.output),
                "stats": dict(sorted(_STATS.items())),
                "trace_count": len(_TRACE),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
