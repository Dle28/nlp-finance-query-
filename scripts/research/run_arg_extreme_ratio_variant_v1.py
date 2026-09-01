#!/usr/bin/env python3
"""Run a guarded arg-extreme ratio-family ablation.

The canonical builder already has a narrow executor for a one-cell period
extreme.  It deliberately leaves derived ratios and other multi-step
questions unresolved because a broad semantic numerator/denominator pair is
not an answer contract.  This adapter supplies one reusable family-level
contract for three source layouts observed in the hydrated population:

* a disclosed leased-land cost divided by the income-statement cost of sales;
* deposit-interest expense divided by the disclosed total interest expense;
* a segment's transport-service assets divided by total assets;
* a depreciation-factor cost divided by the production-factor cost total;
* a certificate-of-deposit maturity bucket divided by the certificate total;
* USD long-term loans divided by the disclosed long-term-loan total;
* net on-balance-sheet currency position divided by total assets; and
* fixed-asset depreciation divided by the management-expense total.

The implementation is source-first and fail-closed.  It does not use a
Question-ID lookup to select an answer.  It recognizes a question by its
metric phenomenon, then requires exact ticker/year/scope tables, explicit
source units, current-period columns, a stable row/header contract, and a
unique arg-extreme winner.  The canonical builder still owns proposal
verification, evidence writing, Decimal/Pandas replay, and authority policy.

This is an authorized best-effort research candidate.  Local replay is not an
independent accuracy evaluation and cannot establish official score or
promotion.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


VARIANT_PROTOCOL = "vifinqa_arg_extreme_ratio_period_v2"
STRICT_VARIANT_PROTOCOL = "vifinqa_arg_extreme_ratio_period_strict_source_v2"
RATIO_TIER = "program_arg_extreme_ratio_period_v1"
MODULE_NAME = "_vifinqa_arg_extreme_ratio_period_variant_v1"


def _load_period_runner() -> Any:
    runner_path = Path(__file__).resolve().with_name(
        "run_arg_extreme_period_variant_v1.py"
    )
    spec = importlib.util.spec_from_file_location(MODULE_NAME, runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load period runner: {runner_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARG = _load_period_runner()
BUILDER = ARG.BUILDER
_ORIGINAL_PROGRAM_AWARE = BUILDER.program_aware_answer
_ORIGINAL_ROUTE_PRIORITY = BUILDER._route_priority

_TYPED_PLANS: dict[int, dict[str, Any]] = {}
_TRACE: list[dict[str, Any]] = []
_STATS: Counter[str] = Counter()
_STRICT_SOURCE_CONTRACT = False
_RAW_SOURCE_UNIT_CACHE: dict[tuple[str, int], Decimal | None] = {}

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def _normal(value: Any) -> str:
    return " ".join(BUILDER.normalize(value).split())


def _question_id(item: Mapping[str, Any]) -> int | None:
    return ARG._question_id(item)


def _typed_plan(item: Mapping[str, Any]) -> dict[str, Any] | None:
    question_id = _question_id(item)
    return _TYPED_PLANS.get(question_id) if question_id is not None else None


def _family_for_question(question: str) -> str | None:
    """Classify a ratio phenomenon from semantic wording, never a question ID."""

    text = _normal(question)
    if not any(marker in text for marker in ("ty trong", "ty le", "phan tram")):
        return None
    if (
        "gia von" in text
        and "cho thue" in text
        and "dat" in text
        and "co so ha tang" in text
        and "tong gia von" in text
    ):
        return "lease_land_cost_share"
    if (
        "chi phi lai tien gui" in text
        and "tong chi phi lai" in text
    ):
        return "deposit_interest_expense_share"
    if (
        "tai san bo phan" in text
        and "dich vu van tai" in text
        and "tong tai san" in text
    ):
        return "transport_segment_asset_share"
    if (
        "khau hao va phan bo" in text
        and "tong chi phi san xuat va kinh doanh theo yeu to" in text
    ):
        return "production_factor_depreciation_share"
    if (
        "chung chi tien gui" in text
        and "duoi 12 thang" in text
        and "ty trong" in text
    ):
        return "certificate_deposit_short_maturity_share"
    if (
        "vay bang usd" in text
        and "tong khoan vay dai han" in text
    ):
        return "usd_long_term_loan_share"
    if (
        "trang thai tien te noi bang" in text
        and "tong tai san" in text
    ):
        return "net_on_balance_currency_asset_share"
    if (
        "khau hao tai san co dinh" in text
        and "tong chi phi quan ly doanh nghiep" in text
    ):
        return "management_depreciation_share"
    return None


def _table_year(table: Mapping[str, Any]) -> int | None:
    try:
        if table.get("report_year") is not None:
            return int(table["report_year"])
    except (TypeError, ValueError):
        pass
    try:
        return BUILDER.document_year(table.get("document_id"))
    except (TypeError, ValueError):
        return None


def _table_scope(table: Mapping[str, Any]) -> str:
    scope = str(table.get("scope") or "").strip().lower()
    if scope in {"separate", "consolidated", "aggregated", "unknown"}:
        return scope
    return ""


def _table_kind(table: Mapping[str, Any]) -> str:
    classifier = getattr(BUILDER.source_first_lookup_module, "_table_kind", None)
    raw = classifier(table) if callable(classifier) else ""
    if not raw:
        function = table.get("table_function")
        raw = function.get("kind") if isinstance(function, Mapping) else function
    return _normal(raw).replace("_", " ")


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
    for field in ("headers", "column_labels", "table_section", "table_purpose"):
        value = table.get(field)
        if isinstance(value, Mapping):
            values.extend(str(part or "") for part in value.values())
        elif isinstance(value, (list, tuple)):
            values.extend(str(part or "") for part in value)
        elif value:
            values.append(str(value))
    return _normal(" ".join(values))


def _row_label(row: Any) -> str:
    if not isinstance(row, (list, tuple)):
        return ""
    parts: list[str] = []
    for cell in row:
        text = str(cell or "").strip()
        if not text or text in {"-", "–", "—"}:
            continue
        if BUILDER.parse_decimal(cell) is not None:
            continue
        parts.append(text)
    return _normal(" ".join(parts))


def _row_label_without_leading_number(label: str) -> str:
    """Remove a statement line number while preserving accounting wording."""

    return _normal(re.sub(r"^\d+\s+", "", label))


def _raw_cell(table: Mapping[str, Any], row_index: int, column_index: int) -> Decimal | None:
    rows = table.get("rows") or []
    if row_index < 0 or row_index >= len(rows):
        return None
    row = rows[row_index]
    if not isinstance(row, (list, tuple)) or column_index < 0 or column_index >= len(row):
        return None
    return BUILDER.parse_decimal(row[column_index])


def _raw_source_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    """Read an explicit unit declaration from the immutable source document."""

    # Some Vietnamese annual-report note tables inherit the report's VND
    # accounting unit but omit it from the compact reconstructed table.  A
    # ratio is safe only when that unit is recovered from the same immutable
    # source file; never infer a scale from the magnitude of a cell.
    source_path = str(table.get("source_path") or "")
    char_start = table.get("char_start")
    try:
        char_offset = int(char_start)
    except (TypeError, ValueError):
        return None
    if not source_path:
        return None
    cache_key = (source_path, char_offset)
    if cache_key in _RAW_SOURCE_UNIT_CACHE:
        return _RAW_SOURCE_UNIT_CACHE[cache_key]
    multiplier: Decimal | None = None
    try:
        source_text = Path(source_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        _RAW_SOURCE_UNIT_CACHE[cache_key] = None
        return None
    unit_parser = getattr(BUILDER, "_unit_multiplier_from_text", None)
    if callable(unit_parser):
        prefix = source_text[: max(0, min(char_offset, len(source_text)))]
        for line in reversed(prefix.splitlines()):
            normalized = BUILDER.normalize(line)
            if "don vi" not in normalized:
                continue
            candidate = unit_parser(normalized)
            if candidate is not None and candidate > 0:
                multiplier = Decimal(str(candidate))
                break
        # A report-level accounting-unit declaration can precede the note by
        # many pages.  Accept it only for the explicit VND accounting-unit
        # sentence, not for an arbitrary neighbouring numeric sentence.
        if multiplier is None:
            for line in source_text.splitlines():
                normalized = BUILDER.normalize(line)
                if "don vi tien te su dung trong ke toan" not in normalized:
                    continue
                candidate = unit_parser(normalized)
                if candidate is not None and candidate > 0:
                    multiplier = Decimal(str(candidate))
                    break
    _RAW_SOURCE_UNIT_CACHE[cache_key] = multiplier
    if multiplier is not None:
        _STATS["strict_source_unit_raw_fallback"] += 1
    return multiplier


def _source_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    """Resolve units with source text taking precedence over reconstructed metadata.

    A few compact assets contain a stale or generic ``unit_hint`` while the
    source table/header declares a different scale.  For a strict ratio
    contract, the nearby immutable source declaration is the stronger
    provenance signal.  If the source is unavailable, fall back to the
    builder's validated table-level resolver; no magnitude-based inference is
    allowed.
    """

    raw_multiplier = _raw_source_multiplier(table)
    if raw_multiplier is not None and raw_multiplier > 0:
        return raw_multiplier
    resolver = getattr(BUILDER, "_table_declared_source_multiplier", None)
    if callable(resolver):
        multiplier = resolver(table)
        if multiplier is not None and multiplier > 0:
            return Decimal(str(multiplier))
    return None


def _column_text(table: Mapping[str, Any], column_index: int) -> str:
    values: list[str] = []
    for field in ("headers", "column_labels"):
        raw = table.get(field) or []
        if isinstance(raw, (list, tuple)) and column_index < len(raw):
            values.append(str(raw[column_index] or ""))
    for row in (table.get("rows") or [])[:4]:
        if isinstance(row, (list, tuple)) and column_index < len(row):
            values.append(str(row[column_index] or ""))
    return _normal(" ".join(values))


def _current_column(table: Mapping[str, Any], year: int) -> int | None:
    """Resolve the current-period column from source headers/period anchors."""

    widths: list[int] = []
    headers = table.get("headers") or table.get("column_labels") or []
    if isinstance(headers, (list, tuple)):
        widths.append(len(headers))
    for row in (table.get("rows") or [])[:4]:
        if isinstance(row, (list, tuple)):
            widths.append(len(row))
    if not widths:
        return None
    candidates: list[int] = []
    year_token = str(int(year))
    for column_index in range(max(widths)):
        text = _column_text(table, column_index)
        if re.search(rf"(?<!\d){re.escape(year_token)}(?!\d)", text):
            candidates.append(column_index)
    if len(candidates) == 1:
        return candidates[0]

    current_candidates = [
        column_index
        for column_index in range(max(widths))
        if any(
            marker in _normal(
                str((table.get("headers") or table.get("column_labels") or [])[column_index])
                if column_index < len(table.get("headers") or table.get("column_labels") or [])
                else ""
            )
            for marker in ("nam nay", "current", "closing", "so cuoi nam", "cuoi nam")
        )
    ]
    if len(current_candidates) == 1:
        return current_candidates[0]
    return None


def _scope_candidates(
    item: Mapping[str, Any], typed: Mapping[str, Any]
) -> list[str]:
    explicit = ARG._typed_scope(item, typed) or ARG._explicit_question_scope(
        str(item.get("question") or "")
    )
    if explicit:
        return [explicit]
    # An unqualified financial question has a canonical consolidated default.
    # The review shortlist is navigation metadata and must not silently make
    # a separate report authoritative merely because it received more lexical
    # mass.  Keep other scopes as bounded fallback only when the canonical
    # cohort is absent; the selected cohort must still be invariant across all
    # requested years and pass the source contract.
    candidates = ["consolidated"]
    candidates.extend(
        scope
        for scope in ("consolidated", "separate", "aggregated", "unknown")
        if scope not in candidates
    )
    return candidates


def _matching_tables(
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    *,
    ticker: str,
    year: int,
    scope: str,
) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for table in tables_by_uid.values():
        table_ticker = str(BUILDER.table_ticker(table) or "").strip().upper()
        if table_ticker != ticker:
            continue
        if _table_year(table) != year or _table_scope(table) != scope:
            continue
        matches.append(table)
    return matches


def _source_cell(
    table: Mapping[str, Any],
    *,
    row_index: int,
    column_index: int,
    role: str,
    family: str,
) -> dict[str, Any] | None:
    raw = _raw_cell(table, row_index, column_index)
    multiplier = _source_multiplier(table)
    if raw is None or multiplier is None:
        return None
    rows = table.get("rows") or []
    label = _row_label(rows[row_index]) if row_index < len(rows) else ""
    document_id = str(table.get("document_id") or "").removesuffix(".txt")
    value = raw * multiplier
    return {
        "value": value,
        "raw_value_decimal": str(raw),
        "source_to_vnd_multiplier": str(multiplier),
        "source_multiplier": multiplier,
        "operand_value": value,
        "role": role,
        "document_id": document_id,
        "internal_table_uid": str(table.get("internal_table_uid") or ""),
        "row_index": row_index,
        "column_index": column_index,
        "row_label": label,
        "score": 12.0,
        "candidate_source": "argmax_ratio_source_first_contract",
        "research_candidate_only": True,
        "argmax_ratio_family": family,
        "table_kind": _table_kind(table),
        "report_year": _table_year(table),
    }


def _select_lease_pair(
    tables: list[Mapping[str, Any]], *, year: int, family: str
) -> dict[str, Any] | None:
    numerators: list[dict[str, Any]] = []
    denominators: list[dict[str, Any]] = []
    for table in tables:
        kind = _table_kind(table)
        context = _table_context(table)
        rows = table.get("rows") or []
        if kind in {"financial note", "financial note detail"}:
            if "gia von hang ban" not in context:
                continue
            column_index = _current_column(table, year)
            if column_index is None:
                continue
            for row_index, row in enumerate(rows):
                label = _row_label(row)
                if not (
                    "gia von" in label
                    and "cho thue" in label
                    and "dat" in label
                    and "co so ha tang" in label
                ):
                    continue
                if any(marker in label for marker in ("chuyen nhuong", "van phong")):
                    continue
                source = _source_cell(
                    table,
                    row_index=row_index,
                    column_index=column_index,
                    role="numerator",
                    family=family,
                )
                if source is not None:
                    numerators.append(source)
        elif kind == "income statement":
            column_index = _current_column(table, year)
            if column_index is None:
                continue
            for row_index, row in enumerate(rows):
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                code = _normal(row[0])
                label = _row_label_without_leading_number(_normal(row[1]))
                if code != "11" or label != "gia von hang ban va dich vu cung cap":
                    continue
                source = _source_cell(
                    table,
                    row_index=row_index,
                    column_index=column_index,
                    role="denominator",
                    family=family,
                )
                if source is not None:
                    denominators.append(source)

    pairs = [
        (numerator, denominator)
        for numerator in numerators
        for denominator in denominators
        if numerator["document_id"] == denominator["document_id"]
        and numerator["source_to_vnd_multiplier"]
        == denominator["source_to_vnd_multiplier"]
    ]
    if len(pairs) != 1:
        return None
    numerator, denominator = pairs[0]
    return {
        "numerator": numerator,
        "denominator": denominator,
        "details": [],
        "contract": "lease_cost_note_plus_income_statement_code_11",
    }


def _is_interest_detail(label: str) -> bool:
    return label.startswith("chi phi lai") or label == "chi phi hoat dong tin dung khac"


def _select_interest_pair(
    tables: list[Mapping[str, Any]], *, year: int, family: str
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _table_kind(table) not in {"financial note", "financial note detail"}:
            continue
        context = _table_context(table)
        if "chi phi lai" not in context:
            continue
        column_index = _current_column(table, year)
        if column_index is None:
            continue
        rows = table.get("rows") or []
        target_indices = [
            row_index
            for row_index, row in enumerate(rows)
            if _row_label(row) == "chi phi lai tien gui"
        ]
        if len(target_indices) != 1:
            continue
        target_index = target_indices[0]
        for total_index in range(target_index + 1, len(rows)):
            total_label = _row_label(rows[total_index])
            if total_label:
                continue
            total_raw = _raw_cell(table, total_index, column_index)
            if total_raw is None:
                continue
            details: list[tuple[int, Decimal]] = []
            valid = True
            for row_index in range(target_index, total_index):
                label = _row_label(rows[row_index])
                raw = _raw_cell(table, row_index, column_index)
                if not label or not _is_interest_detail(label) or raw is None:
                    valid = False
                    break
                details.append((row_index, raw))
            if not valid or len(details) < 3:
                continue
            if sum((raw for _, raw in details), Decimal(0)) != total_raw:
                continue
            numerator = _source_cell(
                table,
                row_index=target_index,
                column_index=column_index,
                role="numerator",
                family=family,
            )
            denominator = _source_cell(
                table,
                row_index=total_index,
                column_index=column_index,
                role="denominator",
                family=family,
            )
            if numerator is None or denominator is None:
                continue
            pairs.append(
                {
                    "numerator": numerator,
                    "denominator": denominator,
                    "details": [
                        {
                            "row_index": row_index,
                            "raw_value_decimal": str(raw),
                            "row_label": _row_label(rows[row_index]),
                        }
                        for row_index, raw in details
                    ],
                    "contract": "interest_detail_sum_equals_blank_total",
                }
            )
    if len(pairs) != 1:
        return None
    pair = pairs[0]
    if (
        pair["numerator"]["source_to_vnd_multiplier"]
        != pair["denominator"]["source_to_vnd_multiplier"]
    ):
        return None
    return pair


def _segment_column(table: Mapping[str, Any], phrase: str) -> int | None:
    headers = table.get("headers") or table.get("column_labels") or []
    candidates = [
        column_index
        for column_index, value in enumerate(headers)
        if phrase in _normal(value)
    ]
    if len(candidates) == 1:
        return candidates[0]
    for row in (table.get("rows") or [])[:8]:
        if not isinstance(row, (list, tuple)):
            continue
        candidates = [
            column_index
            for column_index, value in enumerate(row)
            if phrase in _normal(value)
        ]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _current_segment_block(table: Mapping[str, Any]) -> tuple[int, int] | None:
    rows = table.get("rows") or []
    end = len(rows)
    for index, row in enumerate(rows):
        label = _row_label(row)
        if index > 0 and label == "so dau nam":
            end = index
            break
    starts = [
        index
        for index, row in enumerate(rows[:end])
        if _row_label(row) == "so cuoi nam"
    ]
    start = starts[0] if starts else 0
    return (start, end) if start < end else None


def _select_segment_pair(
    tables: list[Mapping[str, Any]], *, family: str
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _table_kind(table) != "balance sheet":
            continue
        block = _current_segment_block(table)
        if block is None:
            continue
        start, end = block
        segment_column = _segment_column(table, "dich vu van tai")
        total_column = _segment_column(table, "tong")
        if segment_column is None or total_column is None or segment_column == total_column:
            continue
        rows = table.get("rows") or []
        numerator_indices = [
            row_index
            for row_index in range(start, end)
            if _row_label(rows[row_index]) == "tai san bo phan"
        ]
        denominator_indices = [
            row_index
            for row_index in range(start, end)
            if _row_label(rows[row_index]) == "tong tai san"
        ]
        if len(numerator_indices) != 1 or len(denominator_indices) != 1:
            continue
        numerator = _source_cell(
            table,
            row_index=numerator_indices[0],
            column_index=segment_column,
            role="numerator",
            family=family,
        )
        denominator = _source_cell(
            table,
            row_index=denominator_indices[0],
            column_index=total_column,
            role="denominator",
            family=family,
        )
        if numerator is None or denominator is None:
            continue
        if (
            numerator["source_to_vnd_multiplier"]
            != denominator["source_to_vnd_multiplier"]
        ):
            continue
        pairs.append(
            {
                "numerator": numerator,
                "denominator": denominator,
                "details": [],
                "contract": "segment_transport_row_over_total_assets_row",
            }
        )
    if len(pairs) != 1:
        return None
    return pairs[0]


def _current_period_table(table: Mapping[str, Any], year: int) -> bool:
    """Reject a comparative asset that shares the report's ``report_year``.

    Risk tables in the corpus can be reconstructed as two assets: one for the
    requested closing date and one for the comparative date.  Both carry the
    same document/report year, so a report-year filter alone is insufficient.
    Prefer the explicit period labels and first header anchor; older OCR
    tables without either anchor fall back to the report heading itself.
    """

    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        labels = trace.get("period_labels") or []
        if isinstance(labels, (list, tuple)) and labels:
            explicit = [
                int(match.group(0))
                for label in labels
                for match in _YEAR_RE.finditer(str(label or ""))
            ]
            if explicit:
                return explicit[0] == int(year)

    headers = table.get("headers") or table.get("column_labels") or []
    if isinstance(headers, (list, tuple)) and headers:
        explicit = [int(match.group(0)) for match in _YEAR_RE.finditer(str(headers[0] or ""))]
        if explicit:
            return explicit[0] == int(year)

    context = _table_context(table)
    return bool(re.search(rf"(?<!\d){int(year)}(?!\d)", context))


def _total_column(table: Mapping[str, Any]) -> int | None:
    """Find the unique source column labelled ``Tổng cộng``."""

    widths: list[int] = []
    headers = table.get("headers") or table.get("column_labels") or []
    if isinstance(headers, (list, tuple)):
        widths.append(len(headers))
    for row in (table.get("rows") or [])[:4]:
        if isinstance(row, (list, tuple)):
            widths.append(len(row))
    candidates = [
        column_index
        for column_index in range(max(widths, default=0))
        if "tong cong" in _column_text(table, column_index)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _component_pair(
    *,
    table: Mapping[str, Any],
    year: int,
    family: str,
    numerator_index: int,
    component_indices: list[int],
    contract: str,
) -> dict[str, Any] | None:
    """Build a dimensionless ratio whose denominator is source-row sum.

    The numerator row is intentionally not duplicated in ``components``.
    The resolver and pandas query add it once to the component sum, keeping
    the emitted evidence CSV free of duplicate source coordinates.
    """

    column_index = _current_column(table, year)
    if column_index is None:
        return None
    numerator = _source_cell(
        table,
        row_index=numerator_index,
        column_index=column_index,
        role="numerator",
        family=family,
    )
    components = [
        _source_cell(
            table,
            row_index=row_index,
            column_index=column_index,
            role="denominator_component",
            family=family,
        )
        for row_index in component_indices
    ]
    if numerator is None or any(component is None for component in components):
        return None
    typed_components = [component for component in components if component is not None]
    denominator_value = numerator["value"] + sum(
        (component["value"] for component in typed_components), Decimal(0)
    )
    return {
        "numerator": numerator,
        "denominator": None,
        "denominator_components": typed_components,
        "denominator_value": denominator_value,
        "details": typed_components,
        "contract": contract,
        "ratio_mode": "signed",
    }


def _select_production_factor_pair(
    tables: list[Mapping[str, Any]], *, year: int, family: str
) -> dict[str, Any] | None:
    required = {
        "chi phi nguyen vat lieu trong chi phi san xuat",
        "chi phi nhan cong va nhan vien",
        "chi phi khau hao va phan bo",
        "chi phi dich vu mua ngoai",
        "chi phi khac",
    }
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _table_kind(table) != "financial note detail":
            continue
        if "chi phi san xuat va kinh doanh theo yeu to" not in _table_context(table):
            continue
        rows = table.get("rows") or []
        labelled = [
            (row_index, _row_label(row))
            for row_index, row in enumerate(rows[1:], start=1)
            if _row_label(row)
        ]
        if len(labelled) != len(required) or {label for _, label in labelled} != required:
            continue
        column_index = _current_column(table, year)
        if column_index is None or any(
            _raw_cell(table, row_index, column_index) is None
            for row_index, _ in labelled
        ):
            continue
        by_label = {label: row_index for row_index, label in labelled}
        pair = _component_pair(
            table=table,
            year=year,
            family=family,
            numerator_index=by_label["chi phi khau hao va phan bo"],
            component_indices=[
                by_label[label]
                for label in sorted(required - {"chi phi khau hao va phan bo"})
            ],
            contract="production_factor_rows_sum_to_total",
        )
        if pair is not None:
            pairs.append(pair)
    return pairs[0] if len(pairs) == 1 else None


def _select_certificate_deposit_pair(
    tables: list[Mapping[str, Any]], *, year: int, family: str
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    expected_children = {
        "duoi 12 thang",
        "tu 12 thang den duoi 5 nam",
        "tu 5 nam tro len",
    }
    for table in tables:
        if _table_kind(table) not in {"financial note", "financial note detail", "debt schedule"}:
            continue
        if "phat hanh giay to co gia" not in _table_context(table):
            continue
        rows = table.get("rows") or []
        parents = [
            row_index
            for row_index, row in enumerate(rows)
            if _row_label(row) == "chung chi tien gui"
        ]
        if len(parents) != 1:
            continue
        parent_index = parents[0]
        children: list[tuple[int, str]] = []
        for row_index in range(parent_index + 1, len(rows)):
            label = _row_label(rows[row_index])
            if label in expected_children:
                children.append((row_index, label))
                continue
            if children and label:
                break
        if len(children) != 3 or {label for _, label in children} != expected_children:
            continue
        column_index = _current_column(table, year)
        if column_index is None:
            continue
        child_values = {
            label: _raw_cell(table, row_index, column_index)
            for row_index, label in children
        }
        if any(value is None for value in child_values.values()):
            continue
        child_total = sum((value for value in child_values.values() if value is not None), Decimal(0))
        parent_value = _raw_cell(table, parent_index, column_index)
        if parent_value is not None and parent_value != child_total:
            continue
        numerator_index = next(row_index for row_index, label in children if label == "duoi 12 thang")
        component_indices = [row_index for row_index, label in children if label != "duoi 12 thang"]
        pair = _component_pair(
            table=table,
            year=year,
            family=family,
            numerator_index=numerator_index,
            component_indices=component_indices,
            contract="certificate_maturity_children_sum_to_certificate_total",
        )
        if pair is not None:
            pairs.append(pair)
    return pairs[0] if len(pairs) == 1 else None


def _select_usd_loan_pair(
    tables: list[Mapping[str, Any]], *, year: int, family: str
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _table_kind(table) != "financial data schedule":
            continue
        rows = table.get("rows") or []
        labels = [_row_label(row) for row in rows]
        if labels.count("vay bang usd") != 1 or labels.count("vay bang vnd") != 1:
            continue
        vnd_index = labels.index("vay bang vnd")
        total_indices = [
            row_index
            for row_index in range(vnd_index + 1, len(rows))
            if not labels[row_index]
        ]
        if len(total_indices) != 1:
            continue
        column_index = _current_column(table, year)
        if column_index is None:
            continue
        usd_index = labels.index("vay bang usd")
        usd = _raw_cell(table, usd_index, column_index)
        vnd = _raw_cell(table, vnd_index, column_index)
        total = _raw_cell(table, total_indices[0], column_index)
        if usd is None or vnd is None or total is None or usd <= 0 or vnd <= 0:
            continue
        if usd + vnd != total or total <= 0:
            continue
        numerator = _source_cell(
            table,
            row_index=usd_index,
            column_index=column_index,
            role="numerator",
            family=family,
        )
        denominator = _source_cell(
            table,
            row_index=total_indices[0],
            column_index=column_index,
            role="denominator",
            family=family,
        )
        validation_component = _source_cell(
            table,
            row_index=vnd_index,
            column_index=column_index,
            role="validation_component",
            family=family,
        )
        if numerator is None or denominator is None or validation_component is None:
            continue
        pairs.append(
            {
                "numerator": numerator,
                "denominator": denominator,
                "denominator_components": [],
                "details": [validation_component],
                "contract": "usd_plus_vnd_equals_long_term_loan_total",
                "ratio_mode": "signed",
            }
        )
    return pairs[0] if len(pairs) == 1 else None


def _select_net_currency_pair(
    tables: list[Mapping[str, Any]], *, year: int, family: str
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _table_kind(table) not in {"debt schedule", "governance roster"}:
            continue
        if not _current_period_table(table, year):
            continue
        rows = table.get("rows") or []
        labels = [_row_label(row) for row in rows]
        numerator_indices = [
            row_index
            for row_index, label in enumerate(labels)
            if label == "trang thai tien te noi bang"
        ]
        denominator_indices = [
            row_index for row_index, label in enumerate(labels) if label == "tong tai san"
        ]
        column_index = _total_column(table)
        if (
            len(numerator_indices) != 1
            or len(denominator_indices) != 1
            or column_index is None
        ):
            continue
        denominator_value = _raw_cell(table, denominator_indices[0], column_index)
        if denominator_value is None or denominator_value <= 0:
            continue
        numerator = _source_cell(
            table,
            row_index=numerator_indices[0],
            column_index=column_index,
            role="numerator",
            family=family,
        )
        denominator = _source_cell(
            table,
            row_index=denominator_indices[0],
            column_index=column_index,
            role="denominator",
            family=family,
        )
        if numerator is None or denominator is None:
            continue
        pairs.append(
            {
                "numerator": numerator,
                "denominator": denominator,
                "denominator_components": [],
                "details": [],
                "contract": "net_on_balance_currency_row_over_total_assets_row",
                "ratio_mode": "signed",
            }
        )
    return pairs[0] if len(pairs) == 1 else None


def _select_management_depreciation_pair(
    tables: list[Mapping[str, Any]], *, year: int, family: str
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _table_kind(table) not in {"financial note", "financial note detail", "project schedule"}:
            continue
        if "chi phi quan ly doanh nghiep" not in _table_context(table):
            continue
        rows = table.get("rows") or []
        labels = [_row_label(row) for row in rows]
        numerator_indices = [
            row_index
            for row_index, label in enumerate(labels)
            if label == "chi phi khau hao tai san co dinh"
        ]
        total_indices = [
            row_index
            for row_index, label in enumerate(labels)
            if label == "cong"
        ]
        column_index = _current_column(table, year)
        if (
            len(numerator_indices) != 1
            or len(total_indices) != 1
            or total_indices[0] <= numerator_indices[0]
            or column_index is None
        ):
            continue
        numerator_value = _raw_cell(table, numerator_indices[0], column_index)
        total_value = _raw_cell(table, total_indices[0], column_index)
        if numerator_value is None or total_value is None or numerator_value < 0 or total_value <= 0:
            continue
        numerator = _source_cell(
            table,
            row_index=numerator_indices[0],
            column_index=column_index,
            role="numerator",
            family=family,
        )
        denominator = _source_cell(
            table,
            row_index=total_indices[0],
            column_index=column_index,
            role="denominator",
            family=family,
        )
        if numerator is None or denominator is None:
            continue
        pairs.append(
            {
                "numerator": numerator,
                "denominator": denominator,
                "denominator_components": [],
                "details": [],
                "contract": "management_depreciation_over_management_total",
                "ratio_mode": "signed",
            }
        )
    return pairs[0] if len(pairs) == 1 else None


def _select_pair(
    family: str,
    tables: list[Mapping[str, Any]],
    *,
    year: int,
) -> dict[str, Any] | None:
    if family == "lease_land_cost_share":
        return _select_lease_pair(tables, year=year, family=family)
    if family == "deposit_interest_expense_share":
        return _select_interest_pair(tables, year=year, family=family)
    if family == "transport_segment_asset_share":
        return _select_segment_pair(tables, family=family)
    if family == "production_factor_depreciation_share":
        return _select_production_factor_pair(tables, year=year, family=family)
    if family == "certificate_deposit_short_maturity_share":
        return _select_certificate_deposit_pair(tables, year=year, family=family)
    if family == "usd_long_term_loan_share":
        return _select_usd_loan_pair(tables, year=year, family=family)
    if family == "net_on_balance_currency_asset_share":
        return _select_net_currency_pair(tables, year=year, family=family)
    if family == "management_depreciation_share":
        return _select_management_depreciation_pair(tables, year=year, family=family)
    return None


def _ratio_query(
    years: list[int],
    selected_year: int,
    *,
    direction: str,
    denominator_is_component_sum: bool,
    signed: bool,
) -> str:
    def term(year: int) -> str:
        numerator = (
            f"df1.loc[df1.operand_role=='period_{year}_numerator',"
            "'operand_value'].iloc[0]"
        )
        if denominator_is_component_sum:
            denominator = (
                f"({numerator} + df1.loc["
                f"df1.operand_role.str.startswith('period_{year}_denominator_component'),"
                "'operand_value'].sum())"
            )
        else:
            denominator = (
                f"df1.loc[df1.operand_role=='period_{year}_denominator',"
                "'operand_value'].iloc[0]"
            )
        if signed:
            return f"({numerator} / {denominator})"
        return f"(abs({numerator}) / abs({denominator}))"

    selected = term(selected_year)
    comparator = ">" if direction == "max" else "<"
    comparisons = " and ".join(
        f"{selected} {comparator} {term(year)}"
        for year in years
        if year != selected_year
    )
    return f"float(({comparisons}) * {selected_year})"


def _reject(
    *,
    item: Mapping[str, Any],
    family: str,
    reason: str,
    ticker: str = "",
    years: list[int] | None = None,
) -> None:
    _STATS[f"ratio_rejected_{reason.lower()}"] += 1
    _TRACE.append(
        {
            "question_id": _question_id(item),
            "status": "REJECTED_RATIO_CONTRACT",
            "family": family,
            "reason": reason,
            "ticker": ticker,
            "years": years or [],
        }
    )


def _resolve_ratio(
    item: Mapping[str, Any],
    typed: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    question = str(item.get("question") or "")
    family = _family_for_question(question)
    if family is None:
        return None
    _STATS[f"family_seen_{family}"] += 1
    ticker = ARG._typed_ticker(typed)
    years = ARG._typed_years(typed)
    operation = typed.get("operation_ast") or {}
    direction = str(operation.get("direction") or "max").strip().lower()
    if not ticker or len(years) < 2 or direction not in {"max", "min"}:
        _reject(
            item=item,
            family=family,
            reason="INVALID_TYPED_CONTRACT",
            ticker=ticker,
            years=years,
        )
        return None
    if tables_by_uid is None:
        _reject(
            item=item,
            family=family,
            reason="TABLES_MISSING",
            ticker=ticker,
            years=years,
        )
        return None

    selected_scope: str | None = None
    year_pairs: list[dict[str, Any]] | None = None
    for scope in _scope_candidates(item, typed):
        pairs: list[dict[str, Any]] = []
        for year in years:
            tables = _matching_tables(
                tables_by_uid,
                ticker=ticker,
                year=year,
                scope=scope,
            )
            pair = _select_pair(family, tables, year=year)
            if pair is None:
                pairs = []
                break
            pairs.append(pair)
        if len(pairs) == len(years):
            selected_scope = scope
            year_pairs = pairs
            break
    if selected_scope is None or year_pairs is None:
        _reject(
            item=item,
            family=family,
            reason="INCOMPLETE_OR_AMBIGUOUS_YEAR_COHORT",
            ticker=ticker,
            years=years,
        )
        return None

    all_sources: list[dict[str, Any]] = []
    ratios: list[Decimal] = []
    multipliers: set[str] = set()
    component_sum_contracts: set[bool] = set()
    signed_contracts: set[bool] = set()
    for year, pair in zip(years, year_pairs):
        numerator = pair["numerator"]
        denominator = pair["denominator"]
        denominator_components = list(pair.get("denominator_components") or [])
        denominator_value = (
            numerator["value"]
            + sum(
                (component["value"] for component in denominator_components),
                Decimal(0),
            )
            if denominator_components
            else denominator["value"]
        )
        if denominator_value == 0:
            _reject(
                item=item,
                family=family,
                reason="ZERO_DENOMINATOR",
                ticker=ticker,
                years=years,
            )
            return None
        multipliers.add(str(numerator["source_to_vnd_multiplier"]))
        for source in denominator_components:
            multipliers.add(str(source["source_to_vnd_multiplier"]))
        if denominator is not None:
            multipliers.add(str(denominator["source_to_vnd_multiplier"]))
        signed = str(pair.get("ratio_mode") or "absolute") == "signed"
        component_sum_contracts.add(bool(denominator_components))
        signed_contracts.add(signed)
        ratios.append(
            numerator["value"] / denominator_value
            if signed
            else abs(numerator["value"]) / abs(denominator_value)
        )
        numerator["role"] = f"period_{year}_numerator"
        all_sources.append(numerator)
        for component_index, component in enumerate(denominator_components):
            component["role"] = f"period_{year}_denominator_component_{component_index}"
            all_sources.append(component)
        if denominator is not None:
            denominator["role"] = f"period_{year}_denominator"
            all_sources.append(denominator)

    if len(component_sum_contracts) != 1 or len(signed_contracts) != 1:
        _reject(
            item=item,
            family=family,
            reason="MIXED_RATIO_CONTRACT",
            ticker=ticker,
            years=years,
        )
        return None

    if _STRICT_SOURCE_CONTRACT and len(multipliers) != 1:
        _reject(
            item=item,
            family=family,
            reason="MIXED_SOURCE_UNIT",
            ticker=ticker,
            years=years,
        )
        return None
    if len(multipliers) != 1:
        _reject(
            item=item,
            family=family,
            reason="MIXED_SOURCE_UNIT",
            ticker=ticker,
            years=years,
        )
        return None

    extreme = max(ratios) if direction == "max" else min(ratios)
    extreme_indices = [index for index, value in enumerate(ratios) if value == extreme]
    if len(extreme_indices) != 1:
        _reject(
            item=item,
            family=family,
            reason="RATIO_TIE",
            ticker=ticker,
            years=years,
        )
        return None
    selected_year = years[extreme_indices[0]]
    query = _ratio_query(
        years,
        selected_year,
        direction=direction,
        denominator_is_component_sum=next(iter(component_sum_contracts)),
        signed=next(iter(signed_contracts)),
    )
    answer = Decimal(selected_year)
    _STATS["ratio_accepted"] += 1
    _TRACE.append(
        {
            "question_id": _question_id(item),
            "status": "ACCEPTED",
            "family": family,
            "ticker": ticker,
            "scope": selected_scope,
            "years": years,
            "direction": direction,
            "ratios": [str(value) for value in ratios],
            "selected_year": selected_year,
            "source_multiplier": next(iter(multipliers)),
            "contracts": [pair["contract"] for pair in year_pairs],
            "ratio_mode": "signed" if next(iter(signed_contracts)) else "absolute",
            "denominator_is_component_sum": next(iter(component_sum_contracts)),
            "details": [
                {
                    "period": year,
                    "contract": pair["contract"],
                    "sources": [
                        {
                            "document_id": source.get("document_id"),
                            "internal_table_uid": source.get("internal_table_uid"),
                            "row_index": source.get("row_index"),
                            "column_index": source.get("column_index"),
                            "row_label": source.get("row_label"),
                            "raw_value_decimal": source.get("raw_value_decimal"),
                            "value": str(source.get("value", source.get("raw_value_decimal", ""))),
                        }
                        for source in pair.get("details") or []
                    ],
                }
                for year, pair in zip(years, year_pairs)
            ],
            "sources": [],
        }
    )
    # The compact trace above intentionally does not duplicate evidence rows;
    # append a deterministic source list separately to keep it readable and
    # avoid deriving any value from Question-ID metadata.
    _TRACE[-1]["sources"] = [
        {
            "period": (
                int(match.group(1))
                if (match := re.search(r"period_(\d+)", str(source.get("role") or "")))
                else None
            ),
            "role": source["role"],
            "document_id": source["document_id"],
            "internal_table_uid": source["internal_table_uid"],
            "row_index": source["row_index"],
            "column_index": source["column_index"],
            "row_label": source["row_label"],
            "raw_value_decimal": source["raw_value_decimal"],
            "value_vnd": str(source["value"]),
        }
        for index, source in enumerate(all_sources)
    ]
    return answer, all_sources, query, RATIO_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    typed = _typed_plan(item)
    operation = typed.get("operation_ast") or {} if typed is not None else {}
    ratio_family = _family_for_question(str(item.get("question") or ""))
    # The typed-plan compiler represents some ratio arg-extreme questions as
    # ``simple_aggregation`` with a plain max/min AST (for example when its
    # derived metric was already classified as complete).  The reusable
    # family contract, rather than that compiler status or a Question ID,
    # determines whether the guarded ratio executor is applicable.
    ratio_operation = operation.get("op") in {"arg_extreme_period", "max", "min"}
    if typed is not None and ratio_family is not None and ratio_operation:
        result = _resolve_ratio(item, typed, tables_by_uid=tables_by_uid)
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == RATIO_TIER:
        return 70.6
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _load_typed_plans(path: Path) -> dict[int, dict[str, Any]]:
    plans: dict[int, dict[str, Any]] = {}
    for record in BUILDER.read_jsonl(path):
        plans[int(record["question_id"])] = record
    if len(plans) != 1012:
        raise ValueError(f"typed-plan count={len(plans)} expected 1012")
    return plans


def _write_variant_metadata(output_dir: Path, *, typed_plans_path: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["arg_extreme_ratio_period_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "strict_source_contract": _STRICT_SOURCE_CONTRACT,
        "typed_plans_path": str(typed_plans_path),
        "typed_plan_count": len(_TYPED_PLANS),
        "contract": {
            "operation": "arg_extreme_period",
            "derived_metric": "numerator / denominator",
            "family_level_recognition": True,
            "exact_report_year_per_operand": True,
            "same_scope_across_years": True,
            "same_source_multiplier_across_years": True,
            "current_column_must_be_header_bound": True,
            "ambiguous_pairs_rejected": True,
            "ties_rejected": True,
            "hdb_blank_total_must_equal_detail_sum": True,
            "component_sum_denominators_are_emitted_as_source_rows": True,
            "current_period_asset_anchor_required_for_point_in_time_tables": True,
            "raw_source_unit_fallback_requires_same_source_file_declaration": True,
            "supported_families": [
                "lease_land_cost_share",
                "deposit_interest_expense_share",
                "transport_segment_asset_share",
                "production_factor_depreciation_share",
                "certificate_deposit_short_maturity_share",
                "usd_long_term_loan_share",
                "net_on_balance_currency_asset_share",
                "management_depreciation_share",
            ],
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "arg_extreme_ratio_period_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "arg_extreme_ratio_period_trace_v1.jsonl"
    trace_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in _TRACE
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = BUILDER.configure_parser(argparse.ArgumentParser())
    parser.add_argument(
        "--typed-plans",
        type=Path,
        required=True,
        help="Frozen typed_operand_plans_v1.jsonl used by the ratio selector gate",
    )
    parser.add_argument(
        "--strict-source-contract",
        action="store_true",
        help="Require one declared source multiplier across all ratio operands",
    )
    args = parser.parse_args()

    global _STRICT_SOURCE_CONTRACT, _TYPED_PLANS, VARIANT_PROTOCOL
    _STRICT_SOURCE_CONTRACT = bool(args.strict_source_contract)
    if _STRICT_SOURCE_CONTRACT:
        VARIANT_PROTOCOL = STRICT_VARIANT_PROTOCOL
    _TYPED_PLANS = _load_typed_plans(args.typed_plans)
    BUILDER.program_aware_answer = _patched_program_aware
    BUILDER._route_priority = _patched_route_priority

    BUILDER.build(args)
    _write_variant_metadata(args.output, typed_plans_path=args.typed_plans)
    print(
        json.dumps(
            {
                "protocol": VARIANT_PROTOCOL,
                "strict_source_contract": _STRICT_SOURCE_CONTRACT,
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
