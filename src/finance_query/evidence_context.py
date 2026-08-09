"""Canonical, source-preserving table context for autonomous evidence review.

The raw reports are OCR-derived HTML.  ``table_structure`` V2 deliberately
keeps blank cells under ``rowspan``/``colspan`` so the original grid remains
lossless.  That is the correct source representation, but it is not yet a
useful semantic representation: a child header can lose the parent period
label when a parent cell spans several columns.

This module derives a *separate* canonical context from V2.  It never changes
OCR text, moves a cell, fills a missing value, or replaces V2 rows.  Every
canonical label only reuses text from a raw-HTML V2 header cell through the
recorded span provenance.  Tables that cannot support that contract are marked
``needs_processing`` or ``blocked`` and must not become autonomous training
silver.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .table_structure import normalize_space, sha256_file


NUMERIC_CELL_RE = re.compile(r"^[\s()\-+\d.,%/]+$")
PERIOD_RE = re.compile(
    r"(?:31\s*[/.-]\s*12\s*[/.-]\s*(?:19|20)\d{2}|"
    r"0?1\s*[/.-]\s*0?1\s*[/.-]\s*(?:19|20)\d{2}|"
    r"(?:19|20)\d{2}|số cuối năm|số đầu năm|năm nay|năm trước|"
    r"cuối kỳ|đầu kỳ)",
    re.IGNORECASE,
)
UNIT_RE = re.compile(
    r"(?:nghìn|ngàn|triệu|tỷ)\s*(?:vnd|đồng)|vnd|%|phần trăm",
    re.IGNORECASE,
)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _source_text(
    rows: list[list[str]],
    provenance: list[list[dict[str, Any]]],
    row_index: int,
    column_index: int,
) -> tuple[str, bool]:
    """Read source text at a grid cell, following only its recorded span owner."""
    value = str(rows[row_index][column_index]).strip()
    if value:
        return value, True
    origin = provenance[row_index][column_index]
    if not bool(origin.get("covered_by_span")):
        return "", True
    anchor_row = origin.get("anchor_row")
    anchor_column = origin.get("anchor_column")
    if not _is_int(anchor_row) or not _is_int(anchor_column):
        return "", False
    if not 0 <= anchor_row < len(rows) or not 0 <= anchor_column < len(rows[anchor_row]):
        return "", False
    anchor = str(rows[anchor_row][anchor_column]).strip()
    return anchor, bool(anchor)


def source_grid_integrity(table: dict[str, Any]) -> dict[str, Any]:
    """Check only dimensions/provenance; no OCR correction is attempted."""
    rows = table.get("rows") or []
    provenance = table.get("cell_provenance") or []
    if not isinstance(rows, list) or not rows:
        return {
            "rectangular": False,
            "provenance_complete": False,
            "width": 0,
            "reason_codes": ["empty_grid"],
        }
    width = len(rows[0]) if isinstance(rows[0], list) else 0
    reasons: list[str] = []
    if width == 0 or any(not isinstance(row, list) or len(row) != width for row in rows):
        reasons.append("non_rectangular_grid")
    provenance_complete = (
        isinstance(provenance, list)
        and len(provenance) == len(rows)
        and all(isinstance(row, list) and len(row) == width for row in provenance)
    )
    if not provenance_complete:
        reasons.append("provenance_shape_mismatch")
    invalid_provenance = 0
    if provenance_complete:
        for row in provenance:
            for cell in row:
                if not isinstance(cell, dict):
                    invalid_provenance += 1
    if invalid_provenance:
        reasons.append("invalid_cell_provenance")
    return {
        "rectangular": not any(reason == "non_rectangular_grid" for reason in reasons),
        "provenance_complete": provenance_complete and invalid_provenance == 0,
        "width": width,
        "reason_codes": reasons,
    }


def canonical_headers(table: dict[str, Any]) -> dict[str, Any]:
    """Create per-column source-header paths while preserving header provenance."""
    rows = table.get("rows") or []
    provenance = table.get("cell_provenance") or []
    header_indices = [
        int(index)
        for index in table.get("header_row_indices") or []
        if _is_int(index) and 0 <= int(index) < len(rows)
    ]
    if not rows:
        return {"header_row_indices": [], "columns": [], "span_recoveries": 0}
    width = len(rows[0])
    provenance_available = (
        isinstance(provenance, list)
        and len(provenance) == len(rows)
        and all(isinstance(row, list) and len(row) == width for row in provenance)
    )
    columns: list[dict[str, Any]] = []
    span_recoveries = 0
    for column_index in range(width):
        parts: list[str] = []
        sources: list[dict[str, int]] = []
        for row_index in header_indices:
            if provenance_available:
                value, valid = _source_text(rows, provenance, row_index, column_index)
                origin = provenance[row_index][column_index]
            else:
                value, valid, origin = str(rows[row_index][column_index]).strip(), True, {}
            if not valid:
                continue
            if value and value not in parts:
                parts.append(value)
                anchor_row = origin.get("anchor_row")
                anchor_column = origin.get("anchor_column")
                if _is_int(anchor_row) and _is_int(anchor_column):
                    sources.append(
                        {"row_index": anchor_row, "column_index": anchor_column}
                    )
            if provenance_available and not str(rows[row_index][column_index]).strip() and value:
                span_recoveries += 1
        source_label = " · ".join(parts)
        if not source_label and column_index == 0:
            role = "row_label"
        elif any(hint in source_label.casefold() for hint in ("mã số", "thuyết minh")):
            role = "reference"
        elif "%" in source_label or "tỷ lệ" in source_label.casefold():
            role = "percent_or_rate"
        elif source_label:
            role = "value_or_text"
        else:
            role = "unknown"
        periods = [normalize_space(match.group(0)) for match in PERIOD_RE.finditer(source_label)]
        units = [normalize_space(match.group(0)) for match in UNIT_RE.finditer(source_label)]
        columns.append(
            {
                "column_index": column_index,
                "source_label": source_label,
                "header_source_cells": sources,
                "role": role,
                "period_labels": list(dict.fromkeys(periods)),
                "unit_labels": list(dict.fromkeys(units)),
            }
        )
    return {
        "header_row_indices": header_indices,
        "columns": columns,
        "span_recoveries": span_recoveries,
    }


def _looks_numeric(value: Any) -> bool:
    text = str(value).strip()
    return bool(
        text
        and NUMERIC_CELL_RE.fullmatch(text)
        and any(character.isdigit() for character in text)
    )


def row_profiles(table: dict[str, Any], header_indices: set[int]) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for row_index, row in enumerate(table.get("rows") or []):
        values = [str(value).strip() for value in row]
        non_empty = [value for value in values if value]
        numeric_columns = [index for index, value in enumerate(values) if _looks_numeric(value)]
        if row_index in header_indices:
            role = "header"
        elif not non_empty:
            role = "empty"
        elif numeric_columns:
            role = "data"
        elif len(non_empty) == 1:
            role = "group_or_note"
        else:
            role = "textual"
        profiles.append(
            {
                "row_index": row_index,
                "role": role,
                "numeric_columns": numeric_columns,
                "non_empty_cell_count": len(non_empty),
            }
        )
    return profiles


def evidence_quality(
    table: dict[str, Any], grid: dict[str, Any], headers: dict[str, Any], profiles: list[dict[str, Any]]
) -> dict[str, Any]:
    """Give a review gate, never a truth/correctness score."""
    reasons = list(grid["reason_codes"])
    columns = headers["columns"]
    data = [profile for profile in profiles if profile["role"] == "data"]
    numeric_columns = {
        column
        for profile in data
        for column in profile["numeric_columns"]
        if column != 0
    }
    labelled_numeric_columns = {
        column
        for column in numeric_columns
        if column < len(columns) and columns[column]["source_label"]
    }
    header_coverage = (
        sum(
            bool(column["source_label"]) or column.get("role") == "row_label"
            for column in columns
        )
        / len(columns)
        if columns
        else 0.0
    )
    numeric_header_coverage = (
        len(labelled_numeric_columns) / len(numeric_columns) if numeric_columns else 1.0
    )
    period_bound_numeric_columns = sum(
        bool(columns[column]["period_labels"])
        for column in numeric_columns
        if column < len(columns)
    )
    if not headers["header_row_indices"]:
        reasons.append("header_not_detected")
    if not data:
        reasons.append("no_data_rows")
    if numeric_columns and numeric_header_coverage < 1.0:
        reasons.append("numeric_column_without_source_header")
    if not grid["rectangular"] or not grid["provenance_complete"]:
        status = "blocked"
    elif not headers["header_row_indices"] or not data:
        status = "needs_processing"
    elif header_coverage < 0.60 or numeric_header_coverage < 1.0:
        status = "needs_processing"
    else:
        status = "review_ready"
    score = (
        0.40 * float(grid["rectangular"])
        + 0.25 * float(grid["provenance_complete"])
        + 0.20 * header_coverage
        + 0.15 * numeric_header_coverage
    )
    return {
        "status": status,
        "score": round(score, 4),
        "reason_codes": list(dict.fromkeys(reasons)),
        "header_coverage": round(header_coverage, 4),
        "numeric_header_coverage": round(numeric_header_coverage, 4),
        "data_row_count": len(data),
        "numeric_column_count": len(numeric_columns),
        "period_bound_numeric_column_count": period_bound_numeric_columns,
    }


def build_evidence_context(table: dict[str, Any]) -> dict[str, Any]:
    """Build one independent V3 context row from one V2 raw-HTML structure."""
    grid = source_grid_integrity(table)
    headers = canonical_headers(table)
    profiles = row_profiles(table, set(headers["header_row_indices"]))
    quality = evidence_quality(table, grid, headers, profiles)
    provenance = table.get("source_provenance") or {}
    return {
        "internal_table_uid": str(table["internal_table_uid"]),
        "document_id": table.get("document_id"),
        "local_ordinal": table.get("local_ordinal"),
        "evidence_context_version": 1,
        "source_provenance": {
            "source_path": provenance.get("source_path"),
            "source_sha256": provenance.get("source_sha256"),
            "char_start": provenance.get("char_start"),
            "table_sha256": provenance.get("table_sha256"),
        },
        "grid": grid,
        "canonical_headers": headers,
        "row_profiles": profiles,
        "quality": quality,
        "table_function": table.get("table_function") or {},
        "table_section": table.get("table_section") or {},
        "context_trace": table.get("context_trace") or {},
    }


def recover_continuation_headers(
    contexts: list[dict[str, Any]],
) -> dict[str, int]:
    """Recover a missing header only from an immediately prior compatible table.

    This handles a common raw-report pattern: a table continues on the next
    page without repeating its multi-row header.  The recovery remains fully
    attributable to the prior table UID.  It is deliberately narrow: a change
    in document, ordinal, width, function, or numeric-column layout refuses the
    recovery rather than copying a nearby but unrelated table's headings.
    """
    by_document: dict[str, list[dict[str, Any]]] = {}
    for context in contexts:
        document_id = str(context.get("document_id") or "")
        if document_id:
            by_document.setdefault(document_id, []).append(context)

    recovered = 0
    candidates = 0
    for group in by_document.values():
        group.sort(key=lambda row: int(row.get("local_ordinal") or 0))
        for previous, current in zip(group, group[1:]):
            quality = current.get("quality") or {}
            reasons = set(str(value) for value in quality.get("reason_codes") or [])
            if quality.get("status") != "needs_processing" or "header_not_detected" not in reasons:
                continue
            candidates += 1
            previous_quality = previous.get("quality") or {}
            if previous_quality.get("status") != "review_ready":
                continue
            if int(current.get("local_ordinal") or 0) != int(previous.get("local_ordinal") or 0) + 1:
                continue
            previous_columns = (previous.get("canonical_headers") or {}).get("columns") or []
            current_columns = (current.get("canonical_headers") or {}).get("columns") or []
            if len(previous_columns) != len(current_columns) or not previous_columns:
                continue
            current_function = str((current.get("table_function") or {}).get("kind") or "")
            previous_function = str((previous.get("table_function") or {}).get("kind") or "")
            if not current_function or current_function != previous_function:
                continue
            current_numeric_columns = {
                int(column)
                for profile in current.get("row_profiles") or []
                if profile.get("role") == "data"
                for column in profile.get("numeric_columns") or []
                if _is_int(column)
            }
            if not current_numeric_columns:
                continue
            if any(
                not str(previous_columns[column].get("source_label") or "")
                for column in current_numeric_columns
                if 0 <= column < len(previous_columns)
            ):
                continue
            copied_columns = [
                {
                    **column,
                    "header_source_table_uid": previous["internal_table_uid"],
                }
                for column in previous_columns
            ]
            current["canonical_headers"] = {
                **(current.get("canonical_headers") or {}),
                "columns": copied_columns,
                "recovered_from": {
                    "internal_table_uid": previous["internal_table_uid"],
                    "document_id": previous.get("document_id"),
                    "local_ordinal": previous.get("local_ordinal"),
                    "method": "adjacent_prior_compatible_raw_header",
                },
            }
            updated_reasons = [
                reason
                for reason in quality.get("reason_codes") or []
                if reason not in {"header_not_detected", "numeric_column_without_source_header"}
            ]
            current["quality"] = {
                **quality,
                "status": "review_ready",
                "score": round(max(float(quality.get("score") or 0.0), 0.84), 4),
                "header_coverage": previous_quality.get("header_coverage"),
                "numeric_header_coverage": previous_quality.get("numeric_header_coverage"),
                "reason_codes": [
                    *updated_reasons,
                    "header_recovered_from_adjacent_raw_table",
                ],
            }
            recovered += 1
    return {"candidate_count": candidates, "recovered_count": recovered}


def evidence_context_manifest_path(path: Path) -> Path:
    return path.with_name("table_evidence_context_v1.manifest.json")


def validate_evidence_context_sidecar(
    bundle_dir: Path,
    structure_path: Path,
    context_path: Path,
) -> dict[str, Any]:
    """Verify a semantic sidecar is tied to this exact V2/bundle source."""
    bundle_dir = bundle_dir.resolve()
    structure_path = structure_path.resolve()
    context_path = context_path.resolve()
    manifest_path = evidence_context_manifest_path(context_path)
    if not context_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Evidence-context sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("evidence_context_version") or 0) != 1:
        raise ValueError("Unsupported evidence-context sidecar version")
    if int(manifest.get("error_count") or 0) != 0:
        raise ValueError("Evidence-context sidecar contains build errors")
    if str(manifest.get("input_structure_sha256") or "") != sha256_file(structure_path):
        raise ValueError("Evidence-context sidecar belongs to another V2 structure file")
    if str(manifest.get("input_bundle_tables_sha256") or "") != sha256_file(
        bundle_dir / "tables.jsonl"
    ):
        raise ValueError("Evidence-context sidecar belongs to another immutable bundle")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(context_path):
        raise ValueError("Evidence-context sidecar checksum mismatch")
    return manifest


def sha256_rows(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
