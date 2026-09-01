#!/usr/bin/env python3
"""Independently replay the supported arg-extreme ratio families.

This checker intentionally does not import the candidate adapter.  It scans
the immutable structured-table asset, identifies supported ratio phenomena
from question wording, reselects source cells with an independent contract,
recomputes each period ratio, and verifies source-file hashes and line-map
locators.  Its PASS status is an engineering/source replay result only; no
gold answer or official scorer is present in this workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_FAMILIES = {
    "lease_land_cost_share",
    "deposit_interest_expense_share",
    "transport_segment_asset_share",
    "production_factor_depreciation_share",
    "certificate_deposit_short_maturity_share",
    "usd_long_term_loan_share",
    "net_on_balance_currency_asset_share",
    "management_depreciation_share",
}

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_RAW_SOURCE_UNIT_CACHE: dict[tuple[str, int], Decimal | None] = {}


def _normal(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("đ", "d")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _decimal(value: Any) -> Decimal | None:
    raw = "" if value is None else str(value).strip()
    if not raw or raw in {"-", "–", "—", "_", "N/A", "n/a"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace("\u00a0", "").replace(" ", "").rstrip("%")
    if raw.startswith("-"):
        negative = True
        raw = raw[1:]
    elif raw.startswith("+"):
        raw = raw[1:]
    if "." in raw and "," in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif raw.count(".") >= 1:
        pieces = raw.split(".")
        if all(len(piece) == 3 for piece in pieces[1:]):
            raw = "".join(pieces)
    elif raw.count(",") >= 1:
        pieces = raw.split(",")
        if all(len(piece) == 3 for piece in pieces[1:]):
            raw = "".join(pieces)
        elif raw.count(",") == 1:
            raw = raw.replace(",", ".")
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


def _family(question: str) -> str | None:
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
    if "chi phi lai tien gui" in text and "tong chi phi lai" in text:
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
    if "vay bang usd" in text and "tong khoan vay dai han" in text:
        return "usd_long_term_loan_share"
    if "trang thai tien te noi bang" in text and "tong tai san" in text:
        return "net_on_balance_currency_asset_share"
    if (
        "khau hao tai san co dinh" in text
        and "tong chi phi quan ly doanh nghiep" in text
    ):
        return "management_depreciation_share"
    return None


def _kind(table: Mapping[str, Any]) -> str:
    function = table.get("table_function")
    raw = function.get("kind") if isinstance(function, Mapping) else function
    return _normal(raw).replace("_", " ")


def _year(table: Mapping[str, Any]) -> int | None:
    try:
        return int(table.get("report_year"))
    except (TypeError, ValueError):
        match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(table.get("document_id") or ""))
        return int(match.group(0)) if match else None


def _scope(table: Mapping[str, Any]) -> str:
    return str(table.get("scope") or "").strip().lower()


def _context(table: Mapping[str, Any]) -> str:
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


def _label(row: Any) -> str:
    if not isinstance(row, (list, tuple)):
        return ""
    parts: list[str] = []
    for cell in row:
        text = str(cell or "").strip()
        if not text or text in {"-", "–", "—"} or _decimal(cell) is not None:
            continue
        parts.append(text)
    return _normal(" ".join(parts))


def _without_number(label: str) -> str:
    return _normal(re.sub(r"^\d+\s+", "", label))


def _cell(table: Mapping[str, Any], row_index: int, column_index: int) -> Decimal | None:
    rows = table.get("rows") or []
    if row_index < 0 or row_index >= len(rows):
        return None
    row = rows[row_index]
    if not isinstance(row, (list, tuple)) or column_index < 0 or column_index >= len(row):
        return None
    return _decimal(row[column_index])


def _column_text(table: Mapping[str, Any], column_index: int) -> str:
    values: list[str] = []
    headers = table.get("headers") or table.get("column_labels") or []
    if isinstance(headers, (list, tuple)) and column_index < len(headers):
        values.append(str(headers[column_index] or ""))
    for row in (table.get("rows") or [])[:4]:
        if isinstance(row, (list, tuple)) and column_index < len(row):
            values.append(str(row[column_index] or ""))
    return _normal(" ".join(values))


def _current_column(table: Mapping[str, Any], year: int) -> int | None:
    widths: list[int] = []
    headers = table.get("headers") or table.get("column_labels") or []
    if isinstance(headers, (list, tuple)):
        widths.append(len(headers))
    for row in (table.get("rows") or [])[:4]:
        if isinstance(row, (list, tuple)):
            widths.append(len(row))
    if not widths:
        return None
    year_candidates = [
        index
        for index in range(max(widths))
        if re.search(rf"(?<!\d){year}(?!\d)", _column_text(table, index))
    ]
    if len(year_candidates) == 1:
        return year_candidates[0]
    header_values = headers if isinstance(headers, (list, tuple)) else []
    current_candidates = [
        index
        for index, value in enumerate(header_values)
        if any(
            marker in _normal(value)
            for marker in ("nam nay", "current", "closing", "so cuoi nam", "cuoi nam")
        )
    ]
    return current_candidates[0] if len(current_candidates) == 1 else None


def _current_period_table(table: Mapping[str, Any], year: int) -> bool:
    """Require the requested closing-period asset, not its comparative twin."""

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
        explicit = [
            int(match.group(0)) for match in _YEAR_RE.finditer(str(headers[0] or ""))
        ]
        if explicit:
            return explicit[0] == int(year)
    return bool(re.search(rf"(?<!\d){int(year)}(?!\d)", _context(table)))


def _total_column(table: Mapping[str, Any]) -> int | None:
    headers = table.get("headers") or table.get("column_labels") or []
    widths: list[int] = []
    if isinstance(headers, (list, tuple)):
        widths.append(len(headers))
    for row in (table.get("rows") or [])[:4]:
        if isinstance(row, (list, tuple)):
            widths.append(len(row))
    candidates = [
        index
        for index in range(max(widths, default=0))
        if "tong cong" in _column_text(table, index)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _text_multiplier(value: Any) -> Decimal | None:
    text = _normal(value)
    if any(
        token in text
        for token in ("trieu dong", "trieu vnd", "million dong", "million vnd")
    ):
        return Decimal("1000000")
    if any(
        token in text
        for token in ("ty dong", "ty vnd", "billion dong", "billion vnd")
    ):
        return Decimal("1000000000")
    if "vnd" in text or "don vi tinh dong" in text:
        return Decimal(1)
    return None


def _raw_source_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    """Read the nearest explicit unit declaration from the source file."""

    source_path = str(table.get("source_path") or "")
    try:
        char_start = int(table.get("char_start"))
    except (TypeError, ValueError):
        return None
    if not source_path:
        return None
    key = (source_path, char_start)
    if key in _RAW_SOURCE_UNIT_CACHE:
        return _RAW_SOURCE_UNIT_CACHE[key]
    try:
        source_text = Path(source_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        _RAW_SOURCE_UNIT_CACHE[key] = None
        return None

    multiplier: Decimal | None = None
    prefix = source_text[: max(0, min(char_start, len(source_text)))]
    for line in reversed(prefix.splitlines()):
        normalized = _normal(line)
        if "don vi" not in normalized and "unit" not in normalized:
            continue
        candidate = _text_multiplier(normalized)
        if candidate is not None:
            multiplier = candidate
            break
    if multiplier is None:
        # Some reports declare the accounting unit once, far before the note.
        # This fallback is deliberately restricted to the explicit sentence.
        for line in source_text.splitlines():
            normalized = _normal(line)
            if "don vi tien te su dung trong ke toan" not in normalized:
                continue
            candidate = _text_multiplier(normalized)
            if candidate is not None:
                multiplier = candidate
                break
    _RAW_SOURCE_UNIT_CACHE[key] = multiplier
    return multiplier


def _multiplier(table: Mapping[str, Any]) -> Decimal | None:
    # The immutable source declaration takes precedence over compact-asset
    # metadata, which can retain a stale unit_hint for a reconstructed table.
    raw_multiplier = _raw_source_multiplier(table)
    if raw_multiplier is not None:
        return raw_multiplier
    headers = table.get("headers") or table.get("column_labels") or []
    multiplier = _text_multiplier(" ".join(str(value) for value in headers))
    if multiplier is not None:
        return multiplier
    for row in (table.get("rows") or [])[:8]:
        if not isinstance(row, (list, tuple)):
            continue
        row_text = " ".join(str(value) for value in row)
        multiplier = _text_multiplier(row_text)
        if multiplier is None:
            continue
        has_numeric = any(_decimal(value) is not None for value in row)
        normalized = _normal(row_text)
        if "don vi" in normalized or "unit" in normalized or not has_numeric:
            return multiplier
    trace = table.get("context_trace") or {}
    source_title = trace.get("source_title") if isinstance(trace, Mapping) else ""
    if "don vi" in _normal(source_title):
        multiplier = _text_multiplier(_normal(source_title).rsplit("don vi", 1)[-1])
        if multiplier is not None:
            return multiplier
    return None


def _source(
    table: Mapping[str, Any],
    *,
    row_index: int,
    column_index: int,
    role: str,
) -> dict[str, Any] | None:
    raw = _cell(table, row_index, column_index)
    multiplier = _multiplier(table)
    if raw is None or multiplier is None:
        return None
    return {
        "role": role,
        "uid": str(table.get("internal_table_uid") or ""),
        "document_id": str(table.get("document_id") or "").removesuffix(".txt"),
        "row_index": row_index,
        "column_index": column_index,
        "row_label": _label((table.get("rows") or [])[row_index]),
        "raw_value_decimal": str(raw),
        "value_vnd": str(raw * multiplier),
        "source_multiplier": str(multiplier),
        "source_path": str(table.get("source_path") or ""),
        "char_start": table.get("char_start"),
        "source_sha256": table.get("source_sha256"),
    }


def _lease_pair(tables: list[Mapping[str, Any]], year: int) -> dict[str, Any] | None:
    nums: list[dict[str, Any]] = []
    dens: list[dict[str, Any]] = []
    for table in tables:
        rows = table.get("rows") or []
        column = _current_column(table, year)
        if column is None:
            continue
        if _kind(table) in {"financial note", "financial note detail"}:
            if "gia von hang ban" not in _context(table):
                continue
            for index, row in enumerate(rows):
                label = _label(row)
                if not (
                    "gia von" in label
                    and "cho thue" in label
                    and "dat" in label
                    and "co so ha tang" in label
                ):
                    continue
                if any(marker in label for marker in ("chuyen nhuong", "van phong")):
                    continue
                source = _source(table, row_index=index, column_index=column, role="numerator")
                if source is not None:
                    nums.append(source)
        if _kind(table) == "income statement":
            for index, row in enumerate(rows):
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                if _normal(row[0]) != "11":
                    continue
                if _without_number(_normal(row[1])) != "gia von hang ban va dich vu cung cap":
                    continue
                source = _source(table, row_index=index, column_index=column, role="denominator")
                if source is not None:
                    dens.append(source)
    pairs = [
        (num, den)
        for num in nums
        for den in dens
        if num["document_id"] == den["document_id"]
        and num["source_multiplier"] == den["source_multiplier"]
    ]
    if len(pairs) != 1:
        return None
    return {"numerator": pairs[0][0], "denominator": pairs[0][1], "contract": "lease"}


def _interest_pair(tables: list[Mapping[str, Any]], year: int) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _kind(table) not in {"financial note", "financial note detail"}:
            continue
        if "chi phi lai" not in _context(table):
            continue
        column = _current_column(table, year)
        if column is None:
            continue
        rows = table.get("rows") or []
        targets = [
            index for index, row in enumerate(rows) if _label(row) == "chi phi lai tien gui"
        ]
        if len(targets) != 1:
            continue
        target = targets[0]
        for total_index in range(target + 1, len(rows)):
            if _label(rows[total_index]):
                continue
            total = _cell(table, total_index, column)
            if total is None:
                continue
            detail_values: list[Decimal] = []
            valid = True
            for row_index in range(target, total_index):
                label = _label(rows[row_index])
                value = _cell(table, row_index, column)
                if (
                    not label
                    or value is None
                    or not (
                        label.startswith("chi phi lai")
                        or label == "chi phi hoat dong tin dung khac"
                    )
                ):
                    valid = False
                    break
                detail_values.append(value)
            if not valid or len(detail_values) < 3 or sum(detail_values, Decimal(0)) != total:
                continue
            numerator = _source(table, row_index=target, column_index=column, role="numerator")
            denominator = _source(table, row_index=total_index, column_index=column, role="denominator")
            if numerator is None or denominator is None:
                continue
            if numerator["source_multiplier"] != denominator["source_multiplier"]:
                continue
            pairs.append({"numerator": numerator, "denominator": denominator, "contract": "interest_sum"})
    return pairs[0] if len(pairs) == 1 else None


def _segment_column(table: Mapping[str, Any], phrase: str) -> int | None:
    headers = table.get("headers") or table.get("column_labels") or []
    matches = [index for index, value in enumerate(headers) if phrase in _normal(value)]
    if len(matches) == 1:
        return matches[0]
    for row in (table.get("rows") or [])[:8]:
        if not isinstance(row, (list, tuple)):
            continue
        matches = [index for index, value in enumerate(row) if phrase in _normal(value)]
        if len(matches) == 1:
            return matches[0]
    return None


def _segment_pair(tables: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _kind(table) != "balance sheet":
            continue
        rows = table.get("rows") or []
        end = len(rows)
        for index, row in enumerate(rows):
            if index > 0 and _label(row) == "so dau nam":
                end = index
                break
        starts = [
            index for index, row in enumerate(rows[:end]) if _label(row) == "so cuoi nam"
        ]
        start = starts[0] if starts else 0
        if start >= end:
            continue
        service_col = _segment_column(table, "dich vu van tai")
        total_col = _segment_column(table, "tong")
        if service_col is None or total_col is None or service_col == total_col:
            continue
        numerator_rows = [
            index for index in range(start, end) if _label(rows[index]) == "tai san bo phan"
        ]
        denominator_rows = [
            index for index in range(start, end) if _label(rows[index]) == "tong tai san"
        ]
        if len(numerator_rows) != 1 or len(denominator_rows) != 1:
            continue
        numerator = _source(table, row_index=numerator_rows[0], column_index=service_col, role="numerator")
        denominator = _source(table, row_index=denominator_rows[0], column_index=total_col, role="denominator")
        if numerator is None or denominator is None:
            continue
        if numerator["source_multiplier"] != denominator["source_multiplier"]:
            continue
        pairs.append({"numerator": numerator, "denominator": denominator, "contract": "segment"})
    return pairs[0] if len(pairs) == 1 else None


def _component_pair(
    table: Mapping[str, Any],
    *,
    year: int,
    numerator_index: int,
    component_indices: list[int],
    contract: str,
) -> dict[str, Any] | None:
    column = _current_column(table, year)
    if column is None:
        return None
    numerator = _source(
        table, row_index=numerator_index, column_index=column, role="numerator"
    )
    components = [
        _source(
            table,
            row_index=index,
            column_index=column,
            role="denominator_component",
        )
        for index in component_indices
    ]
    if numerator is None or any(component is None for component in components):
        return None
    return {
        "numerator": numerator,
        "denominator": None,
        "denominator_components": [
            component for component in components if component is not None
        ],
        "validation_components": [],
        "contract": contract,
        "ratio_mode": "signed",
    }


def _production_factor_pair(
    tables: list[Mapping[str, Any]], year: int
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
        if _kind(table) != "financial note detail":
            continue
        if "chi phi san xuat va kinh doanh theo yeu to" not in _context(table):
            continue
        rows = table.get("rows") or []
        labelled = [
            (index, _label(row))
            for index, row in enumerate(rows[1:], start=1)
            if _label(row)
        ]
        if len(labelled) != len(required) or {label for _, label in labelled} != required:
            continue
        by_label = {label: index for index, label in labelled}
        pair = _component_pair(
            table,
            year=year,
            numerator_index=by_label["chi phi khau hao va phan bo"],
            component_indices=[
                by_label[label]
                for label in sorted(required - {"chi phi khau hao va phan bo"})
            ],
            contract="production_factor_rows_sum_to_total",
        )
        if pair is None:
            continue
        column = _current_column(table, year)
        if column is None or any(_cell(table, index, column) is None for index, _ in labelled):
            continue
        pairs.append(pair)
    return pairs[0] if len(pairs) == 1 else None


def _certificate_deposit_pair(
    tables: list[Mapping[str, Any]], year: int
) -> dict[str, Any] | None:
    expected_children = {
        "duoi 12 thang",
        "tu 12 thang den duoi 5 nam",
        "tu 5 nam tro len",
    }
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _kind(table) not in {
            "financial note",
            "financial note detail",
            "debt schedule",
        }:
            continue
        if "phat hanh giay to co gia" not in _context(table):
            continue
        rows = table.get("rows") or []
        parents = [
            index for index, row in enumerate(rows) if _label(row) == "chung chi tien gui"
        ]
        if len(parents) != 1:
            continue
        parent = parents[0]
        children: list[tuple[int, str]] = []
        for index in range(parent + 1, len(rows)):
            label = _label(rows[index])
            if label in expected_children:
                children.append((index, label))
                continue
            if children and label:
                break
        if len(children) != 3 or {label for _, label in children} != expected_children:
            continue
        column = _current_column(table, year)
        if column is None:
            continue
        child_values = {
            label: _cell(table, index, column) for index, label in children
        }
        if any(value is None for value in child_values.values()):
            continue
        child_total = sum(
            (value for value in child_values.values() if value is not None), Decimal(0)
        )
        parent_value = _cell(table, parent, column)
        if parent_value is not None and parent_value != child_total:
            continue
        numerator_index = next(index for index, label in children if label == "duoi 12 thang")
        component_indices = [index for index, label in children if label != "duoi 12 thang"]
        pair = _component_pair(
            table,
            year=year,
            numerator_index=numerator_index,
            component_indices=component_indices,
            contract="certificate_maturity_children_sum_to_certificate_total",
        )
        if pair is not None:
            pairs.append(pair)
    return pairs[0] if len(pairs) == 1 else None


def _usd_loan_pair(tables: list[Mapping[str, Any]], year: int) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _kind(table) != "financial data schedule":
            continue
        rows = table.get("rows") or []
        labels = [_label(row) for row in rows]
        if labels.count("vay bang usd") != 1 or labels.count("vay bang vnd") != 1:
            continue
        usd_index = labels.index("vay bang usd")
        vnd_index = labels.index("vay bang vnd")
        total_indices = [
            index for index in range(vnd_index + 1, len(rows)) if not labels[index]
        ]
        if len(total_indices) != 1:
            continue
        column = _current_column(table, year)
        if column is None:
            continue
        usd = _cell(table, usd_index, column)
        vnd = _cell(table, vnd_index, column)
        total = _cell(table, total_indices[0], column)
        if (
            usd is None
            or vnd is None
            or total is None
            or usd <= 0
            or vnd <= 0
            or total <= 0
            or usd + vnd != total
        ):
            continue
        numerator = _source(table, row_index=usd_index, column_index=column, role="numerator")
        denominator = _source(
            table, row_index=total_indices[0], column_index=column, role="denominator"
        )
        validation = _source(
            table, row_index=vnd_index, column_index=column, role="validation_component"
        )
        if numerator is None or denominator is None or validation is None:
            continue
        pairs.append(
            {
                "numerator": numerator,
                "denominator": denominator,
                "denominator_components": [],
                "validation_components": [validation],
                "contract": "usd_plus_vnd_equals_long_term_loan_total",
                "ratio_mode": "signed",
            }
        )
    return pairs[0] if len(pairs) == 1 else None


def _net_currency_pair(
    tables: list[Mapping[str, Any]], year: int
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _kind(table) not in {"debt schedule", "governance roster"}:
            continue
        if not _current_period_table(table, year):
            continue
        rows = table.get("rows") or []
        labels = [_label(row) for row in rows]
        numerator_indices = [
            index for index, label in enumerate(labels) if label == "trang thai tien te noi bang"
        ]
        denominator_indices = [
            index for index, label in enumerate(labels) if label == "tong tai san"
        ]
        column = _total_column(table)
        if len(numerator_indices) != 1 or len(denominator_indices) != 1 or column is None:
            continue
        denominator_value = _cell(table, denominator_indices[0], column)
        if denominator_value is None or denominator_value <= 0:
            continue
        numerator = _source(
            table, row_index=numerator_indices[0], column_index=column, role="numerator"
        )
        denominator = _source(
            table, row_index=denominator_indices[0], column_index=column, role="denominator"
        )
        if numerator is None or denominator is None:
            continue
        pairs.append(
            {
                "numerator": numerator,
                "denominator": denominator,
                "denominator_components": [],
                "validation_components": [],
                "contract": "net_on_balance_currency_row_over_total_assets_row",
                "ratio_mode": "signed",
            }
        )
    return pairs[0] if len(pairs) == 1 else None


def _management_depreciation_pair(
    tables: list[Mapping[str, Any]], year: int
) -> dict[str, Any] | None:
    pairs: list[dict[str, Any]] = []
    for table in tables:
        if _kind(table) not in {
            "financial note",
            "financial note detail",
            "project schedule",
        }:
            continue
        if "chi phi quan ly doanh nghiep" not in _context(table):
            continue
        rows = table.get("rows") or []
        labels = [_label(row) for row in rows]
        numerator_indices = [
            index
            for index, label in enumerate(labels)
            if label == "chi phi khau hao tai san co dinh"
        ]
        total_indices = [index for index, label in enumerate(labels) if label == "cong"]
        column = _current_column(table, year)
        if (
            len(numerator_indices) != 1
            or len(total_indices) != 1
            or total_indices[0] <= numerator_indices[0]
            or column is None
        ):
            continue
        numerator_value = _cell(table, numerator_indices[0], column)
        total_value = _cell(table, total_indices[0], column)
        if numerator_value is None or total_value is None or numerator_value < 0 or total_value <= 0:
            continue
        numerator = _source(
            table, row_index=numerator_indices[0], column_index=column, role="numerator"
        )
        denominator = _source(
            table, row_index=total_indices[0], column_index=column, role="denominator"
        )
        if numerator is None or denominator is None:
            continue
        pairs.append(
            {
                "numerator": numerator,
                "denominator": denominator,
                "denominator_components": [],
                "validation_components": [],
                "contract": "management_depreciation_over_management_total",
                "ratio_mode": "signed",
            }
        )
    return pairs[0] if len(pairs) == 1 else None


def _pair(family: str, tables: list[Mapping[str, Any]], year: int) -> dict[str, Any] | None:
    if family == "lease_land_cost_share":
        return _lease_pair(tables, year)
    if family == "deposit_interest_expense_share":
        return _interest_pair(tables, year)
    if family == "transport_segment_asset_share":
        return _segment_pair(tables)
    if family == "production_factor_depreciation_share":
        return _production_factor_pair(tables, year)
    if family == "certificate_deposit_short_maturity_share":
        return _certificate_deposit_pair(tables, year)
    if family == "usd_long_term_loan_share":
        return _usd_loan_pair(tables, year)
    if family == "net_on_balance_currency_asset_share":
        return _net_currency_pair(tables, year)
    if family == "management_depreciation_share":
        return _management_depreciation_pair(tables, year)
    return None


def _ticker(plan: Mapping[str, Any]) -> str:
    entities = plan.get("entities") or []
    if entities:
        return str(entities[0]).strip().upper()
    operands = plan.get("operands") or []
    for operand in operands:
        if isinstance(operand, Mapping) and operand.get("ticker"):
            return str(operand["ticker"]).strip().upper()
    return ""


def _years(plan: Mapping[str, Any]) -> list[int]:
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    return years


def _requested_scope(plan: Mapping[str, Any], question: str) -> str:
    scope = str(plan.get("scope") or "").strip().lower()
    if scope in {"separate", "consolidated", "aggregated"}:
        return scope
    text = _normal(question)
    if "cong ty me" in text or "bao cao tai chinh rieng" in text:
        return "separate"
    if "bao cao tai chinh hop nhat" in text:
        return "consolidated"
    # The public questions leave scope blank; the established financial QA
    # default for an unqualified company question is the consolidated cohort.
    return "consolidated"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_provenance(source: Mapping[str, Any], line_map: Mapping[str, Any], cache: dict[str, str]) -> str | None:
    uid = str(source.get("uid") or "")
    expected_line = line_map.get(uid)
    path = Path(str(source.get("source_path") or ""))
    if expected_line is None:
        return "SOURCE_LINE_MAP_MISSING"
    if not path.is_file():
        return "SOURCE_FILE_MISSING"
    actual_sha = cache.get(str(path))
    if actual_sha is None:
        actual_sha = _sha256(path)
        cache[str(path)] = actual_sha
    declared_sha = str(source.get("source_sha256") or "")
    if declared_sha and actual_sha != declared_sha:
        return "SOURCE_SHA256_MISMATCH"
    char_start = source.get("char_start")
    if char_start is not None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            actual_line = text.count("\n", 0, int(char_start)) + 1
            if int(expected_line) != actual_line:
                return "SOURCE_LINE_MISMATCH"
        except (OSError, TypeError, ValueError):
            return "SOURCE_LINE_READ_ERROR"
    return None


def replay(
    *,
    asset_path: Path,
    source_line_map_path: Path,
    questions_path: Path,
    typed_plans_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    questions = {
        int(row["id"]): row
        for row in (json.loads(line) for line in questions_path.read_text(encoding="utf-8").splitlines())
        if row.get("id") is not None
    }
    typed = {
        int(row["question_id"]): row
        for row in (json.loads(line) for line in typed_plans_path.read_text(encoding="utf-8").splitlines())
        if row.get("question_id") is not None
    }
    tables: dict[str, dict[str, Any]] = {}
    asset_digest = hashlib.sha256()
    with asset_path.open(encoding="utf-8") as handle:
        for line in handle:
            asset_digest.update(line.encode("utf-8"))
            if line.strip():
                table = json.loads(line)
                uid = str(table.get("internal_table_uid") or "")
                if uid:
                    tables[uid] = table
    line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    supported = []
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    source_hash_cache: dict[str, str] = {}
    for question_id, question_item in sorted(questions.items()):
        family = _family(str(question_item.get("question") or ""))
        if family not in SUPPORTED_FAMILIES:
            continue
        supported.append(question_id)
        plan = typed.get(question_id)
        if not plan:
            failures.append({"question_id": question_id, "family": family, "reason": "TYPED_PLAN_MISSING"})
            continue
        ticker = _ticker(plan)
        years = _years(plan)
        scope = _requested_scope(plan, str(question_item.get("question") or ""))
        pairs: list[dict[str, Any]] = []
        for year in years:
            cohort = [
                table
                for table in tables.values()
                if str(table.get("ticker") or "").upper() == ticker
                and _year(table) == year
                and _scope(table) == scope
            ]
            selected = _pair(family, cohort, year)
            if selected is None:
                pairs = []
                break
            pairs.append(selected)
        if len(pairs) != len(years) or len(years) < 2:
            failures.append(
                {
                    "question_id": question_id,
                    "family": family,
                    "ticker": ticker,
                    "years": years,
                    "scope": scope,
                    "reason": "INCOMPLETE_OR_AMBIGUOUS_COHORT",
                }
            )
            continue
        ratios: list[Decimal] = []
        provenance_errors: list[str] = []
        multipliers: set[str] = set()
        source_records: list[dict[str, Any]] = []
        validation_records: list[dict[str, Any]] = []
        for year, pair in zip(years, pairs):
            numerator = pair["numerator"]
            denominator = pair["denominator"]
            components = list(pair.get("denominator_components") or [])
            validation_components = list(pair.get("validation_components") or [])
            denominator_value = (
                Decimal(str(numerator["value_vnd"]))
                + sum(
                    (Decimal(str(component["value_vnd"])) for component in components),
                    Decimal(0),
                )
                if components
                else Decimal(str(denominator["value_vnd"]))
            )
            if denominator_value == 0:
                provenance_errors.append("ZERO_DENOMINATOR")
                continue
            signed = str(pair.get("ratio_mode") or "absolute") == "signed"
            numerator_value = Decimal(str(numerator["value_vnd"]))
            ratios.append(
                numerator_value / denominator_value
                if signed
                else abs(numerator_value) / abs(denominator_value)
            )
            period_sources: list[dict[str, Any]] = []
            period_validation_sources: list[dict[str, Any]] = []
            numerator_record = dict(numerator)
            numerator_record["role"] = f"period_{year}_numerator"
            period_sources.append(numerator_record)
            for index, component in enumerate(components):
                component_record = dict(component)
                component_record["role"] = f"period_{year}_denominator_component_{index}"
                period_sources.append(component_record)
            if denominator is not None:
                denominator_record = dict(denominator)
                denominator_record["role"] = f"period_{year}_denominator"
                period_sources.append(denominator_record)
            for index, component in enumerate(validation_components):
                validation_record = dict(component)
                validation_record["role"] = f"period_{year}_validation_component_{index}"
                period_validation_sources.append(validation_record)
            # ``sources`` is the exact answer-cell closure and must match the
            # candidate evidence CSV.  Validation-only cells (for example the
            # VND leg used to prove a USD+VND total) are retained separately so
            # provenance is still checked without changing materialization
            # matching semantics.
            source_records.extend(period_sources)
            validation_records.extend(period_validation_sources)
            for source in [*period_sources, *period_validation_sources]:
                multipliers.add(source["source_multiplier"])
                error = _verify_provenance(source, line_map, source_hash_cache)
                if error:
                    provenance_errors.append(error)
        if len(ratios) != len(years):
            failures.append({"question_id": question_id, "family": family, "reason": "RATIO_REPLAY_FAILED", "errors": sorted(set(provenance_errors))})
            continue
        direction = str(
            (plan.get("operation_ast") or {}).get("direction") or plan.get("direction") or "max"
        ).strip().lower()
        if direction not in {"max", "min"}:
            failures.append(
                {
                    "question_id": question_id,
                    "family": family,
                    "reason": "INVALID_DIRECTION",
                    "direction": direction,
                }
            )
            continue
        extreme = max(ratios) if direction == "max" else min(ratios)
        winners = [index for index, value in enumerate(ratios) if value == extreme]
        if len(winners) != 1:
            failures.append({"question_id": question_id, "family": family, "reason": "RATIO_TIE", "ratios": [str(value) for value in ratios]})
            continue
        if len(multipliers) != 1:
            provenance_errors.append("MIXED_SOURCE_UNIT")
        record = {
            "question_id": question_id,
            "family": family,
            "ticker": ticker,
            "scope": scope,
            "years": years,
            "ratios": [str(value) for value in ratios],
            "selected_year": years[winners[0]],
            "direction": direction,
            "ratio_mode": str(pairs[0].get("ratio_mode") or "absolute"),
            "denominator_is_component_sum": bool(pairs[0].get("denominator_components")),
            "contracts": [pair["contract"] for pair in pairs],
            "source_multiplier": sorted(multipliers),
            "sources": source_records,
            "validation_sources": validation_records,
            "provenance_errors": sorted(set(provenance_errors)),
        }
        if provenance_errors:
            failures.append({"question_id": question_id, "family": family, "reason": "PROVENANCE_FAILED", "errors": sorted(set(provenance_errors))})
        else:
            records.append(record)

    result = {
        "protocol": "vifinqa_independent_arg_extreme_ratio_replay_v2",
        "status": "PASS" if not failures and len(records) == len(supported) else "FAIL",
        "asset_path": str(asset_path),
        "asset_sha256": asset_digest.hexdigest(),
        "source_line_map_path": str(source_line_map_path),
        "questions_path": str(questions_path),
        "typed_plans_path": str(typed_plans_path),
        "population": {
            "questions": len(questions),
            "structured_tables": len(tables),
            "supported_ratio_questions": len(supported),
            "replayed_supported_questions": len(records),
            "failures": len(failures),
        },
        "records": records,
        "failures": failures,
        "answer_accuracy": "NOT_MEASURED",
        "execution_accuracy": "NOT_MEASURED",
        "official_scorer": "NOT_AVAILABLE",
        "authority_status": "CANDIDATE_ONLY",
        "promotion_allowed": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--source-line-map", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--typed-plans", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(
        asset_path=args.asset,
        source_line_map_path=args.source_line_map,
        questions_path=args.questions,
        typed_plans_path=args.typed_plans,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
