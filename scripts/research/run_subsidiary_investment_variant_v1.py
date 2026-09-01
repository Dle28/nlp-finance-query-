#!/usr/bin/env python3
"""Run a strict source-cell ablation for the subsidiary-investment family.

The ordinary direct resolver loses a small but high-confidence family because
Vietnamese ``đầu tư vào công ty con`` is partly treated as metric noise.  This
adapter supplies only the missing family executor.  It keeps the canonical
builder responsible for loading tables, replaying coordinates, writing
evidence, validating the 1,012-record submission, and assigning the local
``PARTIAL``/``UNRESOLVED`` class.

The route is deliberately family-level rather than Question-ID-specific.  It
requires an explicit parent-company/separate-report scope, one ticker, one
report year, an exact source row (or an independently checked section total),
an explicitly declared source unit, and a current-period column.  Duplicate
source tables are allowed only when their Decimal answers agree.  A successful
run is an authorized best-effort candidate lane; it is not a strict VERIFIED
release without a separate complete E2E certificate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping


VARIANT_PROTOCOL = "vifinqa_subsidiary_investment_strict_v1"
SUBSIDIARY_TIER = "program_subsidiary_investment_v1"
BUILDER_NAME = "_vifinqa_canonical_submission_builder_subsidiary_investment"


def _preload_source_first_lookup() -> None:
    """Pin the source-first module used by a frozen builder snapshot."""

    configured_path = os.environ.get("VIFINQA_SUBSIDIARY_SOURCE_FIRST_LOOKUP_PATH")
    if not configured_path:
        return
    source_path = Path(configured_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(
            f"configured source-first lookup does not exist: {source_path}"
        )
    module_name = "finance_query.e2e.core.source_first_lookup"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load configured source-first lookup: {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _load_builder() -> Any:
    _preload_source_first_lookup()
    configured_path = os.environ.get("VIFINQA_SUBSIDIARY_BUILDER_PATH")
    builder_path = (
        Path(configured_path).expanduser()
        if configured_path
        else Path(__file__).resolve().parents[1]
        / "e2e"
        / "build_competition_submission_v1.py"
    )
    spec = importlib.util.spec_from_file_location(BUILDER_NAME, builder_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load canonical builder: {builder_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()
_ORIGINAL_PROGRAM_AWARE = BUILDER.program_aware_answer
_ORIGINAL_ROUTE_PRIORITY = BUILDER._route_priority

_TRACE: list[dict[str, Any]] = []
_STATS: Counter[str] = Counter()
_TABLE_INDEX_KEY: int | None = None
_TABLE_INDEX_OWNER: Mapping[str, Mapping[str, Any]] | None = None
_TABLE_INDEX: dict[tuple[str, int, str], list[dict[str, Any]]] = {}

_SUBSIDIARY_PHRASES = (
    "dau tu vao cong ty con",
    "dau tu vao cac cong ty con",
)
_EXPLICIT_SEPARATE_CUES = (
    "cong ty me",
    "bao cao tai chinh rieng",
    "du lieu cong ty me",
    "pham vi cong ty me",
)
_UNSAFE_TABLE_KINDS = {
    "governance",
    "governance roster",
    "segment reporting",
    "financial data schedule",
}
_SECTION_STOP_MARKERS = (
    "cong ty lien doanh",
    "cong ty lien ket",
    "dau tu dai han khac",
    "du phong giam gia",
    "tong cong",
    "cong",
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:?")


def _normalize(value: Any) -> str:
    return _compact(BUILDER.normalize(value))


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


def _single_ticker(plan: Mapping[str, Any]) -> str:
    values = plan.get("tickers") or plan.get("entities") or []
    tickers = [str(value).strip().upper() for value in values if str(value).strip()]
    return tickers[0] if len(set(tickers)) == 1 else ""


def _single_year(plan: Mapping[str, Any]) -> int | None:
    values = plan.get("years") or []
    years: list[int] = []
    for value in values:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    return years[0] if len(years) == 1 else None


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    normalized_question = _normalize(question)
    if not any(phrase in normalized_question for phrase in _SUBSIDIARY_PHRASES):
        return None
    if not any(cue in normalized_question for cue in _EXPLICIT_SEPARATE_CUES):
        return None
    ticker = _single_ticker(plan)
    year = _single_year(plan)
    if not ticker or year is None:
        return None
    scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if scope != "separate":
        return None
    metric = str(item.get("effective_metric") or "").strip()
    if not metric:
        operands = plan.get("operands") or []
        if operands and isinstance(operands[0], Mapping):
            metric = str(operands[0].get("metric") or "").strip()
    metric = metric or question
    original_cost = "nguyen gia" in normalized_question or "nguyen gia" in _normalize(metric)
    return {
        "question_id": _question_id(item),
        "question": question,
        "question_norm": normalized_question,
        "ticker": ticker,
        "year": year,
        "scope": scope,
        "metric": metric,
        "original_cost": original_cost,
        "output_divisor": BUILDER.requested_divisor(question),
    }


def _table_year(table: Mapping[str, Any]) -> int | None:
    try:
        if table.get("report_year") is not None:
            return int(table["report_year"])
    except (TypeError, ValueError):
        pass
    return BUILDER.document_year(table.get("document_id"))


def _table_scope(table: Mapping[str, Any]) -> str:
    return str(BUILDER.table_reporting_scope(table) or "").strip().lower()


def _table_index(
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    global _TABLE_INDEX_KEY, _TABLE_INDEX_OWNER, _TABLE_INDEX
    key = id(tables_by_uid)
    if _TABLE_INDEX_KEY == key and _TABLE_INDEX_OWNER is tables_by_uid:
        return _TABLE_INDEX
    index: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for raw_table in tables_by_uid.values():
        table = dict(raw_table)
        ticker = str(BUILDER.table_ticker(table) or "").strip().upper()
        year = _table_year(table)
        scope = _table_scope(table)
        if ticker and year is not None and scope:
            index[(ticker, year, scope)].append(table)
    _TABLE_INDEX_KEY = key
    _TABLE_INDEX_OWNER = tables_by_uid
    _TABLE_INDEX = dict(index)
    _STATS["indexed_table_groups"] = len(_TABLE_INDEX)
    return _TABLE_INDEX


def _row_label(row: Any) -> str:
    if not isinstance(row, (list, tuple)):
        return ""
    parts = [
        str(cell).strip()
        for cell in row
        if (
            str(cell).strip()
            and str(cell).strip(" -–—")
            and BUILDER.parse_decimal(cell) is None
        )
    ]
    return _compact(" ".join(parts))


def _row_label_without_numbering(label: str) -> str:
    value = _normalize(label)
    value = re.sub(r"^(?:\d+|[ivxlcdm]+)[.)]?\s+", "", value)
    value = re.sub(r"\s*\(?\s*thuyet minh\b.*$", "", value)
    value = re.sub(r"\s*\(?\s*so\s*\d+[a-z]?\s*\)?\s*$", "", value)
    return _compact(value)


def _is_exact_subsidiary_row(label: str) -> bool:
    normalized = _row_label_without_numbering(label)
    if not normalized:
        return False
    if any(marker in normalized for marker in ("cong ty lien doanh", "cong ty lien ket")):
        return False
    return any(
        re.search(rf"\b{re.escape(phrase)}\b", normalized)
        for phrase in _SUBSIDIARY_PHRASES
    )


def _evidence_window(table: Mapping[str, Any], row_index: int) -> list[dict[str, Any]]:
    rows = table.get("rows") or []
    indices = set(range(min(8, len(rows))))
    indices.update(range(max(0, row_index - 2), min(len(rows), row_index + 3)))
    evidence = [
        {"index": index, "row": rows[index]}
        for index in sorted(indices)
        if isinstance(rows[index], list)
    ]
    headers = table.get("column_labels") or table.get("headers") or []
    if headers:
        evidence.append({"index": -1, "row": list(headers)})
    return evidence


def _table_context(table: Mapping[str, Any]) -> str:
    values: list[str] = []
    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        for key in ("source_title", "summary", "topic"):
            value = trace.get(key)
            if isinstance(value, Mapping):
                values.extend(str(value.get(field) or "") for field in ("label", "kind"))
            else:
                values.append(str(value or ""))
    for field in ("headers", "column_labels", "table_section"):
        value = table.get(field)
        if isinstance(value, Mapping):
            values.extend(str(part or "") for part in value.values())
        elif isinstance(value, (list, tuple)):
            values.extend(str(part or "") for part in value)
        elif value:
            values.append(str(value))
    return _normalize(" ".join(values))


def _explicit_source_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    """Read only a unit declared by the source-side table/header.

    ``unit_hint`` is often inferred from magnitudes in the corpus.  It is
    useful for navigation but cannot authorize a strict answer, especially
    when a note table omits its unit.  This helper intentionally does not use
    that fallback.
    """

    multiplier_fn = getattr(BUILDER, "_unit_multiplier_from_text", None)
    if not callable(multiplier_fn):
        return None
    header_text = " ".join(
        str(value)
        for field in ("column_labels", "headers")
        for value in (table.get(field) or [])
    )
    multiplier = multiplier_fn(header_text)
    if multiplier is not None:
        return multiplier
    for raw_row in (table.get("rows") or [])[:8]:
        if not isinstance(raw_row, (list, tuple)):
            continue
        row_text = " ".join(str(cell) for cell in raw_row)
        multiplier = multiplier_fn(row_text)
        if multiplier is None:
            continue
        normalized = _normalize(row_text)
        has_numeric = any(BUILDER.parse_decimal(cell) is not None for cell in raw_row)
        if "don vi" in normalized or "unit" in normalized or not has_numeric:
            return multiplier
    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        source_title = _normalize(trace.get("source_title") or "")
        if "don vi" in source_title:
            multiplier = multiplier_fn(source_title.rsplit("don vi", 1)[-1])
            if multiplier is not None:
                return multiplier
    return None


def _table_is_safe(table: Mapping[str, Any]) -> bool:
    raw_kind = getattr(BUILDER.source_first_lookup_module, "_table_kind", None)
    kind = _normalize(raw_kind(table) if callable(raw_kind) else "")
    if kind in _UNSAFE_TABLE_KINDS:
        return False
    # Unknown reconstructed tables remain eligible only when their source
    # cells and headers carry the exact subsidiary phrase.  The row-level
    # contract below is the authority; this guard only removes known unsafe
    # schedule/governance segments.
    return True


def _choose_cell(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> tuple[int, Decimal, list[dict[str, Any]], str] | None:
    evidence = _evidence_window(table, row_index)
    chooser = getattr(BUILDER, "choose_semantic_column", None)
    if not callable(chooser):
        return None
    chosen = chooser(
        table,
        evidence,
        row_index,
        row,
        int(spec["year"]),
        str(spec["question"]),
        str(spec["metric"]),
        parser=BUILDER.parse_decimal,
    )
    if chosen is None:
        return None
    column_index, raw_value = chosen
    if not isinstance(raw_value, Decimal):
        raw_value = BUILDER.parse_decimal(raw_value)
    if raw_value is None:
        return None
    column_context = str(
        BUILDER.semantic_column_context(
            table,
            evidence,
            column_index=int(column_index),
            row_index=int(row_index),
        )
    )
    period_info_fn = getattr(BUILDER, "_semantic_column_period_info", None)
    if callable(period_info_fn):
        period_info = period_info_fn(
            table,
            evidence,
            column_index=int(column_index),
            row_index=int(row_index),
        )
        if period_info.get("start") and any(
            marker in spec["question_norm"]
            for marker in ("cuoi nam", "den ngay", "tai ngay", "ket thuc")
        ):
            return None
        if period_info.get("years") and int(spec["year"]) not in period_info["years"]:
            return None
    return int(column_index), raw_value, evidence, _normalize(column_context)


def _original_cost_is_bound(
    table: Mapping[str, Any],
    *,
    row_label: str,
    column_context: str,
) -> bool:
    source_context = _table_context(table)
    return any(
        phrase in f"{_normalize(row_label)} {column_context} {source_context}"
        for phrase in ("nguyen gia", "gia goc")
    )


def _unit_anchor_for_raw_value(
    table: Mapping[str, Any],
    *,
    raw_value: Decimal,
    spec: Mapping[str, Any],
    source_tables: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Find an independently declared unit for a unit-less note cell.

    The anchor must be in the same issuer/year/scope cohort, must expose the
    same exact subsidiary row and current-period raw value, and must have a
    source-side unit declaration.  A conflicting anchor set is rejected.
    """

    anchors: list[dict[str, Any]] = []
    for other in source_tables:
        if str(other.get("internal_table_uid") or "") == str(
            table.get("internal_table_uid") or ""
        ):
            continue
        if not _table_is_safe(other):
            continue
        multiplier = _explicit_source_multiplier(other)
        if multiplier is None:
            continue
        for row_index, row in enumerate(other.get("rows") or []):
            if not isinstance(row, list) or not _is_exact_subsidiary_row(_row_label(row)):
                continue
            chosen = _choose_cell(other, row_index=row_index, row=row, spec=spec)
            if chosen is None:
                continue
            column_index, observed, _evidence, column_context = chosen
            if observed != raw_value:
                continue
            anchors.append(
                {
                    "table": other,
                    "row_index": row_index,
                    "column_index": column_index,
                    "raw_value": observed,
                    "source_multiplier": multiplier,
                    "row_label": _row_label(row),
                    "column_context": column_context,
                }
            )
    if not anchors:
        return None
    multipliers = {anchor["source_multiplier"] for anchor in anchors}
    if len(multipliers) != 1:
        return None
    return sorted(
        anchors,
        key=lambda anchor: str(
            anchor["table"].get("internal_table_uid") or ""
        ),
    )[0]


def _direct_candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
    source_tables: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    label = _row_label(row)
    if not _is_exact_subsidiary_row(label):
        return None
    chosen = _choose_cell(table, row_index=row_index, row=row, spec=spec)
    if chosen is None:
        return None
    column_index, raw_value, evidence, column_context = chosen
    if spec["original_cost"] and not _original_cost_is_bound(
        table,
        row_label=label,
        column_context=column_context,
    ):
        return None
    multiplier = _explicit_source_multiplier(table)
    unit_anchor = None
    if multiplier is None:
        unit_anchor = _unit_anchor_for_raw_value(
            table,
            raw_value=raw_value,
            spec=spec,
            source_tables=source_tables,
        )
        if unit_anchor is not None:
            multiplier = unit_anchor["source_multiplier"]
    if multiplier is None:
        return None
    answer = raw_value * multiplier / spec["output_divisor"]
    return {
        "answer": answer,
        "raw_value": raw_value,
        "source_multiplier": multiplier,
        "row_index": row_index,
        "column_index": column_index,
        "row_label": label,
        "column_context": column_context,
        "evidence": evidence,
        "table": table,
        "kind": "exact_source_row",
        "unit_anchor": unit_anchor,
    }


def _aggregate_candidate(
    table: Mapping[str, Any],
    *,
    section_index: int,
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Recover a blank-label section total only after a checksum."""

    rows = table.get("rows") or []
    section_row = rows[section_index]
    section_label = _row_label(section_row)
    if not _is_exact_subsidiary_row(section_label):
        return None
    child_values: list[tuple[int, int, Decimal]] = []
    total: tuple[int, int, Decimal, list[dict[str, Any]], str] | None = None
    for row_index in range(section_index + 1, min(len(rows), section_index + 20)):
        row = rows[row_index]
        if not isinstance(row, list):
            continue
        label = _row_label(row)
        normalized = _normalize(label)
        if normalized and (
            normalized in {"cong", "tong cong"}
            or any(
                normalized.startswith(marker)
                for marker in _SECTION_STOP_MARKERS
                if marker not in {"cong", "tong cong"}
            )
        ):
            break
        has_numeric = any(BUILDER.parse_decimal(cell) is not None for cell in row[1:])
        if not has_numeric:
            continue
        chosen = _choose_cell(table, row_index=row_index, row=row, spec=spec)
        if chosen is None:
            continue
        column_index, raw_value, evidence, column_context = chosen
        if not label:
            total = (row_index, column_index, raw_value, evidence, column_context)
            break
        child_values.append((row_index, column_index, raw_value))
    if total is None or not child_values:
        return None
    total_row_index, total_column_index, total_raw, evidence, column_context = total
    if any(column_index != total_column_index for _, column_index, _ in child_values):
        return None
    if sum((value for _, _, value in child_values), Decimal(0)) != total_raw:
        return None
    multiplier = _explicit_source_multiplier(table)
    if multiplier is None:
        return None
    answer = total_raw * multiplier / spec["output_divisor"]
    return {
        "answer": answer,
        "raw_value": total_raw,
        "source_multiplier": multiplier,
        "row_index": total_row_index,
        "column_index": total_column_index,
        "row_label": f"{section_label} (tổng đã kiểm tra)",
        "column_context": column_context,
        "evidence": evidence,
        "table": table,
        "kind": "section_total_checksum",
        "child_rows": [
            {"row_index": row_index, "column_index": column_index, "raw_value": value}
            for row_index, column_index, value in child_values
        ],
    }


def _candidate_rank(candidate: Mapping[str, Any], *, original_cost: bool) -> tuple[int, int, str]:
    table = candidate["table"]
    context = _table_context(table)
    # Prefer an explicit financial-note cost layout for ``Nguyên giá`` and a
    # named balance-sheet row for ordinary reported balance questions.  This
    # ordering only chooses among equal Decimal answers; it never authorizes a
    # conflicting value.
    explicit_cost = int("gia goc" in context or "nguyen gia" in context)
    known_kind = int(
        _normalize(
            getattr(BUILDER.source_first_lookup_module, "_table_kind", lambda _: "")(table)
        )
        not in {"", "unknown"}
    )
    return (
        explicit_cost if original_cost else known_kind,
        int(candidate.get("kind") == "exact_source_row"),
        str(table.get("internal_table_uid") or ""),
    )


def _source_and_selection(
    candidate: Mapping[str, Any],
    *,
    spec: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = candidate["table"]
    document_id = str(table.get("document_id") or "").removesuffix(".txt")
    uid = str(table.get("internal_table_uid") or "")
    raw_value = candidate["raw_value"]
    answer = candidate["answer"]
    source_multiplier = candidate["source_multiplier"]
    row_index = int(candidate["row_index"])
    column_index = int(candidate["column_index"])
    row_label = str(candidate.get("row_label") or "")
    source = {
        "raw_value_decimal": str(raw_value),
        "value": answer,
        "role": "subsidiary_investment_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": row_index,
        "column_index": column_index,
        "row_label": row_label,
        "source_to_vnd_multiplier": str(source_multiplier),
        "requested_output_divisor": str(spec["output_divisor"]),
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    sources = [source]
    unit_anchor = candidate.get("unit_anchor")
    if isinstance(unit_anchor, Mapping):
        anchor_table = unit_anchor["table"]
        sources.append(
            {
                "raw_value_decimal": str(unit_anchor["raw_value"]),
                "role": "subsidiary_investment_unit_anchor",
                "document_id": str(
                    anchor_table.get("document_id") or ""
                ).removesuffix(".txt"),
                "internal_table_uid": str(
                    anchor_table.get("internal_table_uid") or ""
                ),
                "row_index": int(unit_anchor["row_index"]),
                "column_index": int(unit_anchor["column_index"]),
                "row_label": str(unit_anchor.get("row_label") or ""),
                "source_to_vnd_multiplier": str(unit_anchor["source_multiplier"]),
                "candidate_source": VARIANT_PROTOCOL,
                "unit_anchor_for": uid,
                "research_candidate_only": True,
                "promotion_allowed": False,
            }
        )
    selection = {
        "score": 70.5,
        "value": answer,
        "raw_value": raw_value,
        "source_multiplier": source_multiplier,
        "row_index": row_index,
        "column_index": column_index,
        "row_label": row_label,
        "column_context": candidate.get("column_context"),
        "document_id": document_id,
        "internal_table_uid": uid,
        "candidate_rank": 0,
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "program_family": "explicit_parent_subsidiary_investment",
        "program_kind": candidate.get("kind"),
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(source_multiplier),
        "promotion_allowed": False,
    }
    return sources, selection


def _resolve_subsidiary_investment(
    item: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    if not tables_by_uid:
        return None
    tables = _table_index(tables_by_uid).get(
        (str(spec["ticker"]), int(spec["year"]), str(spec["scope"])),
        [],
    )
    if not tables:
        _STATS["family_no_exact_table_group"] += 1
        return None
    candidates: list[dict[str, Any]] = []
    for table in tables:
        if not _table_is_safe(table):
            _STATS["table_rejected_unsafe_kind"] += 1
            continue
        rows = table.get("rows") or []
        for row_index, row in enumerate(rows):
            if not isinstance(row, list):
                continue
            direct = _direct_candidate(
                table,
                row_index=row_index,
                row=row,
                spec=spec,
                source_tables=tables,
            )
            if direct is not None:
                candidates.append(direct)
                continue
            if _is_exact_subsidiary_row(_row_label(row)):
                aggregate = _aggregate_candidate(
                    table,
                    section_index=row_index,
                    spec=spec,
                )
                if aggregate is not None:
                    candidates.append(aggregate)
    if not candidates:
        _STATS["family_no_strict_candidate"] += 1
        _TRACE.append(
            {
                "question_id": spec.get("question_id"),
                "status": "REJECTED_NO_STRICT_SOURCE_CANDIDATE",
                "ticker": spec["ticker"],
                "year": spec["year"],
                "candidate_table_count": len(tables),
                "original_cost": spec["original_cost"],
            }
        )
        return None

    answers = {candidate["answer"] for candidate in candidates}
    if len(answers) != 1:
        _STATS["rejected_conflicting_duplicate_answers"] += 1
        _TRACE.append(
            {
                "question_id": spec.get("question_id"),
                "status": "REJECTED_CONFLICTING_DUPLICATE_ANSWERS",
                "ticker": spec["ticker"],
                "year": spec["year"],
                "answers": sorted(str(value) for value in answers),
                "candidate_count": len(candidates),
            }
        )
        return None

    selected = sorted(
        candidates,
        key=lambda candidate: _candidate_rank(
            candidate,
            original_cost=bool(spec["original_cost"]),
        ),
        reverse=True,
    )[0]
    sources, selection = _source_and_selection(selected, spec=spec)
    answer = selected["answer"]
    query = "float(df1.loc[0, 'value'])"
    _STATS["family_accepted"] += 1
    _STATS[f"accepted_{selected['kind']}"] += 1
    _TRACE.append(
        {
            "question_id": spec.get("question_id"),
            "status": "ACCEPTED",
            "ticker": spec["ticker"],
            "year": spec["year"],
            "scope": spec["scope"],
            "metric": spec["metric"],
            "original_cost": spec["original_cost"],
            "candidate_count": len(candidates),
            "answer": str(answer),
            "selected_kind": selected["kind"],
            "document_id": sources[0]["document_id"],
            "internal_table_uid": sources[0]["internal_table_uid"],
            "row_index": sources[0]["row_index"],
            "column_index": sources[0]["column_index"],
            "raw_value": str(sources[0]["raw_value_decimal"]),
            "source_to_vnd_multiplier": sources[0]["source_to_vnd_multiplier"],
            "unit_anchor_uid": (
                sources[1]["internal_table_uid"] if len(sources) > 1 else None
            ),
            "child_rows": selected.get("child_rows") or [],
        }
    )
    return answer, sources, query, SUBSIDIARY_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_subsidiary_investment(
            item,
            spec,
            tables_by_uid=tables_by_uid,
        )
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == SUBSIDIARY_TIER:
        # Keep exact/source-first lanes above this candidate and let the
        # independent verifier rerank it above generic semantic fallback.
        return 70.5
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["subsidiary_investment_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": SUBSIDIARY_TIER,
        "contract": {
            "family": "direct_lookup",
            "explicit_parent_or_separate_scope_required": True,
            "one_ticker_and_one_report_year": True,
            "exact_subsidiary_row_or_checksum_total": True,
            "current_period_column_required": True,
            "declared_source_unit_required": True,
            "original_cost_requires_cost_column_binding": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "subsidiary_investment_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "subsidiary_investment_trace_v1.jsonl"
    trace_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in _TRACE
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = BUILDER.configure_parser(argparse.ArgumentParser())
    args = parser.parse_args()
    BUILDER.program_aware_answer = _patched_program_aware
    BUILDER._route_priority = _patched_route_priority
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
