"""Fail-closed semantic diagnostics for three multi-column ViFinQA cases.

This module is deliberately outside ``finance_query.e2e``.  It reads the
immutable V2 grid, the V3 context sidecar, the question catalog, and the raw
OCR source only to verify a pre-authored semantic hypothesis.  It emits
coordinates and provenance metadata, never the selected cell or any table
row.  A packet from this module is therefore a review/replay candidate, not
evidence, an answer, or a submission input.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_multicol_semantic_diagnostic_v1"
SCHEMA_VERSION = 1
SOURCE_CONTRACT = {
    "research_only": True,
    "candidate_only": True,
    "evidence_eligible": False,
    "may_authorize_evidence": False,
    "may_materialize_answer": False,
    "may_execute_formula": False,
    "may_select_final_column": False,
    "promotion_allowed": False,
    "training_eligible": False,
    "submission_eligible": False,
}

# Keep this list intentionally broader than the current producer output.  A
# future edit must fail validation rather than silently turning this sidecar
# into a value-bearing artifact.
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_value",
        "cell_value",
        "numeric_value",
        "raw_value",
        "raw_values",
        "raw_source_cell",
        "raw_source_row",
        "rows",
        "cell_provenance",
        "human_verified",
        "submission",
        "submission_path",
    }
)
NUMBER_RE = re.compile(r"\d")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_sha(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain an object")
        values.append(value)
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in FORBIDDEN_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be an integer") from error


def _coordinate(value: object, *, label: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {
        "row_index": _int(value.get("row_index"), label=f"{label}.row_index"),
        "column_index": _int(value.get("column_index"), label=f"{label}.column_index"),
    }


def _coordinates_equal(left: Iterable[Mapping[str, Any]], right: Iterable[Mapping[str, Any]]) -> bool:
    return [dict(item) for item in left] == [dict(item) for item in right]


def _source_cell_provenance(
    table: Mapping[str, Any], coordinates: Iterable[Mapping[str, Any]], *, label: str
) -> list[dict[str, Any]]:
    rows = table.get("cell_provenance") or []
    result: list[dict[str, Any]] = []
    for position, coordinate in enumerate(coordinates):
        row_index = _int(coordinate.get("row_index"), label=f"{label}[{position}].row_index")
        column_index = _int(
            coordinate.get("column_index"), label=f"{label}[{position}].column_index"
        )
        if row_index < 0 or row_index >= len(rows):
            raise ValueError(f"{label}[{position}] row is outside V2 provenance")
        row = rows[row_index]
        if column_index < 0 or column_index >= len(row):
            raise ValueError(f"{label}[{position}] column is outside V2 provenance")
        provenance = row[column_index]
        if not isinstance(provenance, Mapping):
            raise ValueError(f"{label}[{position}] has no V2 provenance object")
        # Copy only coordinates/flags; do not copy a source cell value.
        result.append(
            {
                "row_index": row_index,
                "column_index": column_index,
                "source_row": _int(provenance.get("source_row"), label=f"{label}.source_row"),
                "source_cell": _int(
                    provenance.get("source_cell"), label=f"{label}.source_cell"
                ),
                "anchor_row": _int(provenance.get("anchor_row"), label=f"{label}.anchor_row"),
                "anchor_column": _int(
                    provenance.get("anchor_column"), label=f"{label}.anchor_column"
                ),
                "covered_by_span": bool(provenance.get("covered_by_span")),
            }
        )
    return result


def _header_path(table: Mapping[str, Any], coordinates: Iterable[Mapping[str, Any]]) -> list[str]:
    rows = table.get("rows") or []
    labels: list[str] = []
    for position, coordinate in enumerate(coordinates):
        row_index = _int(coordinate.get("row_index"), label=f"header[{position}].row_index")
        column_index = _int(
            coordinate.get("column_index"), label=f"header[{position}].column_index"
        )
        if row_index < 0 or row_index >= len(rows):
            raise ValueError("header coordinate row is outside V2 grid")
        if column_index < 0 or column_index >= len(rows[row_index]):
            raise ValueError("header coordinate column is outside V2 grid")
        value = _text(rows[row_index][column_index])
        if value:
            labels.append(value)
    return labels


def _canonical_header(context: Mapping[str, Any], column_index: int) -> Mapping[str, Any] | None:
    columns = (_mapping(context.get("canonical_headers"))).get("columns") or []
    matches = [
        column
        for column in columns
        if isinstance(column, Mapping)
        and _int(column.get("column_index"), label="V3 column index") == column_index
    ]
    return matches[0] if len(matches) == 1 else None


def _row_profile(context: Mapping[str, Any], row_index: int) -> Mapping[str, Any] | None:
    profiles = context.get("row_profiles") or []
    matches = [
        profile
        for profile in profiles
        if isinstance(profile, Mapping)
        and _int(profile.get("row_index"), label="V3 row index") == row_index
    ]
    return matches[0] if len(matches) == 1 else None


def _context_strings(table: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    trace = _mapping(context.get("context_trace"))
    values.extend(
        [
            _text(trace.get("source_title")),
            _text(_mapping(trace.get("topic")).get("label")),
            _text(trace.get("summary")),
        ]
    )
    values.extend(_text(value) for value in table.get("column_labels") or [])
    for row in table.get("rows") or []:
        if row:
            values.append(_text(row[0]))
    return [value for value in values if value]


def _contains_fragment(values: Iterable[str], fragment: str) -> bool:
    return any(fragment in value for value in values)


def _source_window(
    source_path: Path, *, char_start: int, tail_chars: int
) -> tuple[str, int, int, int, int]:
    text = source_path.read_text(encoding="utf-8")
    if char_start < 0 or char_start > len(text):
        raise ValueError("source char_start is outside raw OCR")
    table_start = text.find("<table", char_start)
    table_close = text.find("</table>", table_start) if table_start >= 0 else -1
    table_end = table_close + len("</table>") if table_close >= 0 else -1
    window_start = max(0, char_start - 1200)
    window_end = min(len(text), (table_end if table_end >= 0 else char_start) + tail_chars)
    return text[window_start:window_end], table_start, table_end, window_start, window_end


def _raw_ocr_check(
    table: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str], str]:
    provenance = _mapping(table.get("source_provenance"))
    source_path = Path(_text(provenance.get("source_path")))
    if not source_path.is_file():
        return {
            "source_file_present": False,
            "source_sha256_matches_v2": False,
            "table_boundary_present": False,
            "context_fragment_status": {},
        }, ["RAW_OCR_SOURCE_MISSING"], ""
    source_bytes_hash = sha256_file(source_path)
    expected_hash = _text(provenance.get("source_sha256"))
    window, table_start, table_end, window_start, window_end = _source_window(
        source_path,
        char_start=_int(provenance.get("char_start"), label="source char_start"),
        tail_chars=_int(spec.get("raw_window_tail_chars") or 6000, label="raw tail chars"),
    )
    context_fragments = [
        _text(value) for value in spec.get("raw_context_fragments") or [] if _text(value)
    ]
    statuses = {fragment: fragment in window for fragment in context_fragments}
    checks = {
        "source_file_present": True,
        "source_sha256_matches_v2": source_bytes_hash == expected_hash,
        "table_boundary_present": table_start >= 0 and table_end > table_start,
        "context_fragment_status": statuses,
        "all_context_fragments_present": all(statuses.values()),
        "char_start": _int(provenance.get("char_start"), label="source char_start"),
        "table_start_char": table_start,
        "table_end_char": table_end,
        "window_start_char": window_start,
        "window_end_char": window_end,
        "raw_window_sha256": _sha256_bytes(window.encode("utf-8")),
    }
    reasons: list[str] = []
    if not checks["source_sha256_matches_v2"]:
        reasons.append("RAW_OCR_SOURCE_HASH_MISMATCH")
    if not checks["table_boundary_present"]:
        reasons.append("RAW_OCR_TABLE_BOUNDARY_MISSING")
    if not checks["all_context_fragments_present"]:
        reasons.append("RAW_OCR_CONTEXT_FRAGMENT_MISSING")
    return checks, reasons, source_bytes_hash


def _period_check(
    table: Mapping[str, Any], context: Mapping[str, Any], metadata: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    trace = _mapping(context.get("context_trace"))
    source_title = _text(trace.get("source_title"))
    expected_end = _text(_mapping(spec.get("period")).get("end"))
    fragments = [
        _text(value) for value in spec.get("period_context_fragments") or [] if _text(value)
    ]
    context_status = {fragment: fragment in source_title for fragment in fragments}
    metadata_end = _mapping(metadata.get("reporting_period_end"))
    metadata_end_text = ""
    if metadata_end:
        metadata_end_text = "-".join(
            f"{_int(metadata_end.get(part), label=f'metadata {part}'):02d}"
            for part in ("year", "month", "day")
        )
    checks = {
        "expected_period_end": expected_end,
        "source_context_fragments": context_status,
        "source_context_exact": all(context_status.values()),
        "metadata_period_end": metadata_end_text or None,
        "metadata_period_exact": metadata_end_text == expected_end,
        "header_period_role": _text(_mapping(spec.get("period")).get("header_role")),
    }
    reasons: list[str] = []
    if not checks["source_context_exact"]:
        reasons.append("PERIOD_SOURCE_CONTEXT_MISSING")
    if not checks["metadata_period_exact"]:
        reasons.append("DOCUMENT_METADATA_PERIOD_MISSING_OR_MISMATCH")
    return checks, reasons


def _unit_check(
    table: Mapping[str, Any], context: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    expected = _text(spec.get("source_unit"))
    context_trace = _mapping(context.get("context_trace"))
    context_units = [_text(value) for value in context_trace.get("unit_labels") or []]
    column_labels = [_text(value) for value in table.get("column_labels") or []]
    target_column = _mapping(spec.get("target")).get("column_index")
    target_columns = [
        column
        for column in spec.get("columns") or []
        if _int(column.get("column_index"), label="configured column index") == _int(
            target_column, label="target column index"
        )
    ]
    target_semantic = target_columns[0] if len(target_columns) == 1 else {}
    header_path = [
        _text(value)
        for value in target_semantic.get("header_labels") or []
        if _text(value)
    ]
    source_locations = {
        "context_trace": expected in context_units,
        "column_labels": any(expected in value for value in column_labels),
        "target_header": any(expected in value for value in header_path),
    }
    checks = {
        "expected_source_unit": expected,
        "context_units": context_units,
        "source_locations": source_locations,
        "unique_source_unit": any(source_locations.values()),
        "requested_output_unit": _text(spec.get("requested_unit")),
        "conversion_deferred": True,
    }
    reasons = [] if checks["unique_source_unit"] else ["SOURCE_UNIT_MISSING_OR_AMBIGUOUS"]
    return checks, reasons


def _scope_check(
    table: Mapping[str, Any], context: Mapping[str, Any], metadata: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    expected_scope = _text(spec.get("report_scope"))
    expected_company = _text(spec.get("entity"))
    source_title = _text(_mapping(context.get("context_trace")).get("source_title"))
    checks = {
        "expected_entity": expected_company,
        "document_company": _text(metadata.get("company")),
        "expected_report_scope": expected_scope,
        "document_report_scope": _text(metadata.get("report_scope")),
        "separate_report_marker_present": "RIÊNG" in source_title.upper(),
        "parent_entity_requested": _text(spec.get("entity_role")) == "parent",
        "scope_unique": (
            _text(metadata.get("company")) == expected_company
            and _text(metadata.get("report_scope")) == expected_scope
            and "RIÊNG" in source_title.upper()
        ),
    }
    reasons = [] if checks["scope_unique"] else ["ENTITY_OR_REPORT_SCOPE_NOT_UNIQUE"]
    return checks, reasons


def _header_check(
    table: Mapping[str, Any], context: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    raw_header_rows = [
        _int(value, label="configured raw header row")
        for value in spec.get("raw_header_row_indices") or []
    ]
    declared_header_rows = [
        _int(value, label="V2 header row") for value in table.get("header_row_indices") or []
    ]
    columns: list[dict[str, Any]] = []
    reasons: list[str] = []
    target_column_index = _int(
        _mapping(spec.get("target")).get("column_index"), label="target column index"
    )
    target_semantic_matches = 0
    for configured in spec.get("columns") or []:
        column_index = _int(configured.get("column_index"), label="configured column index")
        coordinates = [_coordinate(value, label="header source cell") for value in configured.get("header_source_cells") or []]
        labels = _header_path(table, coordinates)
        required_fragments = [
            _text(value) for value in configured.get("required_fragments") or [] if _text(value)
        ]
        fragment_status = {fragment: _contains_fragment(labels, fragment) for fragment in required_fragments}
        v2_provenance = _source_cell_provenance(table, coordinates, label="header_source_cells")
        # The first column is a row dimension, not a value-bearing header. It
        # may legitimately have an empty source header cell, so verify that
        # role through the row labels rather than requiring header text.
        semantic_match = (
            _text(configured.get("role")) == "row_label" and column_index == 0
        ) or (bool(required_fragments) and all(fragment_status.values()))
        if column_index == target_column_index and semantic_match:
            target_semantic_matches += 1
        canonical = _canonical_header(context, column_index)
        canonical_coordinates = [
            _coordinate(value, label="V3 header source cell")
            for value in (_mapping(canonical).get("header_source_cells") or [])
        ] if canonical is not None else []
        canonical_label = _text(_mapping(canonical).get("source_label")) if canonical else ""
        v3_match = bool(canonical) and _coordinates_equal(canonical_coordinates, coordinates) and all(
            fragment in canonical_label for fragment in required_fragments
        )
        columns.append(
            {
                "column_index": column_index,
                "role": _text(configured.get("role")),
                "semantic_label": _text(configured.get("semantic_label")),
                "header_source_cells": coordinates,
                "header_labels": labels,
                "required_fragment_status": fragment_status,
                "v2_header_provenance": v2_provenance,
                "v3_source_label_present": canonical is not None,
                "v3_header_coordinates_match": bool(canonical)
                and _coordinates_equal(canonical_coordinates, coordinates),
                "v3_semantic_label_match": v3_match,
                "period_role": _text(configured.get("period_role")),
                "unit_role": _text(configured.get("unit_role")),
            }
        )
        if not semantic_match:
            reasons.append(f"HEADER_ROLE_NOT_VERIFIED_COLUMN_{column_index}")

    v2_declared_matches = declared_header_rows == raw_header_rows
    raw_header_coordinates_valid = all(
        row_index in raw_header_rows
        for column in columns
        for row_index in [coordinate["row_index"] for coordinate in column["header_source_cells"]]
    )
    v3_target = next(
        (column for column in columns if column["column_index"] == target_column_index), None
    )
    v3_target_match = bool(v3_target and v3_target["v3_semantic_label_match"])
    raw_header_verified = bool(columns) and raw_header_coordinates_valid and target_semantic_matches == 1
    if raw_header_verified and v3_target_match and v2_declared_matches:
        status = "ALIGNED_V2_V3"
    elif raw_header_verified and not v3_target_match:
        status = "V2_RAW_HEADER_VERIFIED_V3_CONFLICT"
        reasons.append("V3_CANONICAL_HEADER_CONFLICT")
    elif raw_header_verified and not v2_declared_matches:
        status = "V2_RAW_HEADER_VERIFIED_DECLARED_HEADER_ROWS_CONFLICT"
        reasons.append("V2_DECLARED_HEADER_ROWS_CONFLICT")
    else:
        status = "HEADER_PROVENANCE_INCOMPLETE"
        reasons.append("HEADER_PROVENANCE_INCOMPLETE")
    checks = {
        "raw_header_row_indices": raw_header_rows,
        "v2_declared_header_row_indices": declared_header_rows,
        "v2_declared_header_rows_match_expected": v2_declared_matches,
        "raw_header_coordinates_valid": raw_header_coordinates_valid,
        "target_semantic_match_count": target_semantic_matches,
        "target_semantic_column_unique": target_semantic_matches == 1,
        "v3_target_semantic_match": v3_target_match,
        "status": status,
        "columns": columns,
    }
    # A V2 row-index mismatch is diagnostic evidence, but a raw source header
    # can still be independently verified.  The V3 conflict remains a hard
    # quarantine gate below.
    return checks, sorted(set(reasons)), columns


def _row_check(table: Mapping[str, Any], context: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    target = _mapping(spec.get("target"))
    row_index = _int(target.get("row_index"), label="target row index")
    column_index = _int(target.get("column_index"), label="target column index")
    rows = table.get("rows") or []
    row_valid = 0 <= row_index < len(rows) and isinstance(rows[row_index], list)
    row_label = _text(rows[row_index][0]) if row_valid and rows[row_index] else ""
    cell_present = row_valid and 0 <= column_index < len(rows[row_index])
    cell_is_numeric = bool(cell_present and NUMBER_RE.search(_text(rows[row_index][column_index])))
    profile = _row_profile(context, row_index)
    numeric_columns = [
        _int(value, label="V3 numeric column") for value in (_mapping(profile).get("numeric_columns") or [])
    ] if profile else []
    expected_parent_rows = []
    parent_status: dict[str, bool] = {}
    for parent in spec.get("parent_rows") or []:
        parent_index = _int(parent.get("row_index"), label="parent row index")
        expected_label = _text(parent.get("label"))
        parent_row_valid = 0 <= parent_index < len(rows) and bool(rows[parent_index])
        actual_label = _text(rows[parent_index][0]) if parent_row_valid else ""
        passed = parent_row_valid and actual_label == expected_label
        parent_status[f"{parent_index}:{expected_label}"] = passed
        expected_parent_rows.append(
            {"row_index": parent_index, "label": expected_label, "verified": passed}
        )
    row_label_match = row_label == _text(target.get("row_label"))
    checks = {
        "row_index": row_index,
        "column_index": column_index,
        "row_label": row_label,
        "expected_row_label": _text(target.get("row_label")),
        "row_label_exact": row_label_match,
        "row_role": _text(target.get("row_role")),
        "row_profile_role": _text(_mapping(profile).get("role")),
        "row_profile_present": profile is not None,
        "target_column_in_v3_numeric_columns": column_index in numeric_columns,
        "target_cell_is_numeric": cell_is_numeric,
        "parent_rows": expected_parent_rows,
        "all_parent_rows_verified": all(parent_status.values()) if parent_status else True,
    }
    reasons: list[str] = []
    if not row_label_match:
        reasons.append("TARGET_ROW_LABEL_MISMATCH")
    if not cell_is_numeric:
        reasons.append("TARGET_CELL_NOT_NUMERIC")
    if not checks["all_parent_rows_verified"]:
        reasons.append("PARENT_ROW_NOT_VERIFIED")
    if profile is None or column_index not in numeric_columns:
        reasons.append("V3_ROW_PROFILE_DOES_NOT_CONFIRM_TARGET_COLUMN")
    return checks, reasons


def _hypothesis_check(
    hypothesis: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    raw_sources: Mapping[str, str],
) -> dict[str, Any]:
    table_uid = _text(hypothesis.get("table_uid"))
    table = tables.get(table_uid)
    context = contexts.get(table_uid)
    evidence_fragments = [
        _text(value) for value in hypothesis.get("evidence_fragments") or [] if _text(value)
    ]
    if table is None or context is None:
        return {
            "hypothesis_id": _text(hypothesis.get("hypothesis_id")),
            "claim": _text(hypothesis.get("claim")),
            "expected_status": _text(hypothesis.get("expected_status")),
            "status": "UNVERIFIED_EVIDENCE",
            "evidence_fragment_status": {fragment: False for fragment in evidence_fragments},
            "rejection_codes": list(hypothesis.get("rejection_codes") or []),
        }
    # Adjacent tables can share the same shape and labels. The raw OCR period
    # and note heading are therefore part of hypothesis verification. Keep
    # this source text in memory only; it is never emitted in the artifact.
    values = _context_strings(table, context)
    raw_source = raw_sources.get(table_uid)
    if raw_source:
        values.append(raw_source)
    fragment_status = {fragment: _contains_fragment(values, fragment) for fragment in evidence_fragments}
    row_index = hypothesis.get("row_index")
    row_label = _text(hypothesis.get("row_label"))
    row_status = True
    if row_index is not None:
        index = _int(row_index, label="hypothesis row index")
        rows = table.get("rows") or []
        row_status = 0 <= index < len(rows) and bool(rows[index]) and _text(rows[index][0]) == row_label
    column_status = True
    column_index = hypothesis.get("column_index")
    required_column_fragments = [
        _text(value) for value in hypothesis.get("column_fragments") or [] if _text(value)
    ]
    if column_index is not None:
        index = _int(column_index, label="hypothesis column index")
        columns = [
            column for column in hypothesis.get("header_source_cells") or [] if isinstance(column, Mapping)
        ]
        try:
            labels = _header_path(table, columns)
        except ValueError:
            labels = []
        column_status = all(fragment in " ".join(labels) for fragment in required_column_fragments)
    evidence_ok = all(fragment_status.values()) and row_status and column_status
    expected_status = _text(hypothesis.get("expected_status"))
    return {
        "hypothesis_id": _text(hypothesis.get("hypothesis_id")),
        "claim": _text(hypothesis.get("claim")),
        "expected_status": expected_status,
        "status": expected_status if evidence_ok else "UNVERIFIED_EVIDENCE",
        "evidence_fragment_status": fragment_status,
        "row_label_verified": row_status,
        "column_role_fragments_verified": column_status,
        "rejection_codes": list(hypothesis.get("rejection_codes") or []),
        "alternative_table_uid": table_uid if expected_status == "REJECTED" else None,
    }


def _load_and_validate_config(config_path: Path) -> dict[str, Any]:
    config = _read_json(config_path)
    if config.get("protocol") != PROTOCOL or _int(config.get("schema_version"), label="config schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected multi-column diagnostic config")
    targets = config.get("targets") or []
    if not targets:
        raise ValueError("multi-column diagnostic config has no targets")
    question_ids: set[int] = set()
    for target in targets:
        question_id = _int(target.get("question_id"), label="question_id")
        if question_id in question_ids:
            raise ValueError("duplicate diagnostic question_id")
        question_ids.add(question_id)
        for key in ("entity", "document_id", "internal_table_uid", "source_unit", "requested_unit"):
            if not _text(target.get(key)):
                raise ValueError(f"target {question_id} lacks {key}")
        target_cell = _mapping(target.get("target"))
        for key in ("row_index", "column_index", "row_label", "row_role"):
            if key not in target_cell or (isinstance(target_cell.get(key), str) and not _text(target_cell.get(key))):
                raise ValueError(f"target {question_id} lacks target.{key}")
        if not target.get("columns"):
            raise ValueError(f"target {question_id} has no column-role map")
        if not target.get("hypotheses") or len(target["hypotheses"]) < 2:
            raise ValueError(f"target {question_id} needs at least two hypotheses")
    return config


def _manifest_inputs(paths: Mapping[str, Path]) -> dict[str, dict[str, str]]:
    return {
        name: {"path": str(path), "sha256": sha256_file(path)} for name, path in paths.items()
    }


def build_multicol_semantic_diagnostic(
    *,
    config_path: Path,
    questions_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    document_metadata_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a value-blind diagnostic and candidate/quarantine ledgers."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    config = _load_and_validate_config(config_path)
    questions = {
        _int(row.get("id"), label="question id"): _text(row.get("question"))
        for row in _read_jsonl(questions_path)
    }
    table_rows = _read_jsonl(structured_tables_path)
    context_rows = _read_jsonl(evidence_context_path)
    metadata_rows = _read_jsonl(document_metadata_path)
    tables: dict[str, Mapping[str, Any]] = {}
    table_uid_counts: Counter[str] = Counter()
    for table in table_rows:
        uid = _text(table.get("internal_table_uid"))
        if uid:
            table_uid_counts[uid] += 1
            tables[uid] = table
    contexts = {
        _text(row.get("internal_table_uid")): row
        for row in context_rows
        if _text(row.get("internal_table_uid"))
    }
    metadata = {
        _text(row.get("document_id")): row
        for row in metadata_rows
        if _text(row.get("document_id"))
    }
    # Load raw OCR only for the configured hypothesis tables. This lets the
    # evidence checks distinguish adjacent same-shape tables without copying
    # the full corpus into memory or into the output artifact.
    hypothesis_uids = {
        _text(hypothesis.get("table_uid"))
        for target in config["targets"]
        for hypothesis in target.get("hypotheses") or []
        if _text(hypothesis.get("table_uid"))
    }
    raw_hypothesis_sources: dict[str, str] = {}
    for hypothesis_uid in hypothesis_uids:
        hypothesis_table = tables.get(hypothesis_uid)
        hypothesis_provenance = _mapping(
            _mapping(hypothesis_table or {}).get("source_provenance")
        )
        hypothesis_source_path = Path(_text(hypothesis_provenance.get("source_path")))
        if hypothesis_source_path.is_file():
            raw_hypothesis_sources[hypothesis_uid] = hypothesis_source_path.read_text(
                encoding="utf-8"
            )

    diagnostics: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    for spec in sorted(config["targets"], key=lambda value: _int(value.get("question_id"), label="question_id")):
        question_id = _int(spec.get("question_id"), label="question_id")
        uid = _text(spec.get("internal_table_uid"))
        table = tables.get(uid)
        context = contexts.get(uid)
        document_id = _text(spec.get("document_id"))
        document_metadata = metadata.get(document_id, {})
        reasons: list[str] = []
        checks: dict[str, Any] = {}
        header_columns: list[dict[str, Any]] = []
        source_hash = ""
        source_cell_hash = ""
        if table is None or context is None:
            reasons.append("V2_OR_V3_TARGET_TABLE_MISSING")
            checks["table_context_present"] = False
            raw_checks = {"source_file_present": False}
            period_checks = {"source_context_exact": False, "metadata_period_exact": False}
            unit_checks = {"unique_source_unit": False}
            scope_checks = {"scope_unique": False}
            header_checks = {"status": "HEADER_PROVENANCE_INCOMPLETE", "columns": []}
            row_checks = {"row_label_exact": False, "target_cell_is_numeric": False}
        else:
            checks["table_context_present"] = True
            provenance = _mapping(table.get("source_provenance"))
            source_hash = _text(provenance.get("source_sha256"))
            source_path = Path(_text(provenance.get("source_path")))
            checks["table_uid_unique"] = table_uid_counts[uid] == 1
            checks["document_id_match"] = _text(table.get("document_id")) == document_id
            table_entity = _text(table.get("ticker")) or _text(table.get("entity"))
            checks["entity_source"] = "table"
            if not table_entity:
                table_entity = _text(document_metadata.get("company"))
                checks["entity_source"] = "document_metadata"
            checks["entity_match"] = table_entity == _text(spec.get("entity"))
            source_report_year = table.get("report_year")
            checks["report_year_source"] = "table"
            if source_report_year is None:
                source_report_year = document_metadata.get("report_year")
                checks["report_year_source"] = "document_metadata"
            checks["report_year_match"] = (
                source_report_year is not None
                and _int(source_report_year, label="source report_year")
                == _int(spec.get("report_year"), label="target report_year")
            )
            checks["local_ordinal_match"] = _int(table.get("local_ordinal"), label="table local_ordinal") == _int(spec.get("local_ordinal"), label="target local_ordinal")
            checks["source_provenance_present"] = bool(
                source_hash and _text(provenance.get("table_sha256")) and source_path.as_posix()
            )
            checks["context_provenance_aligned"] = bool(
                _text(context.get("internal_table_uid")) == uid
                and _text(context.get("document_id")) == document_id
                and _text(_mapping(context.get("source_provenance")).get("source_sha256")) == source_hash
                and _text(_mapping(context.get("source_provenance")).get("table_sha256"))
                == _text(provenance.get("table_sha256"))
                and _mapping(context.get("grid")).get("rectangular") is True
                and _mapping(context.get("grid")).get("provenance_complete") is True
            )
            if not checks["table_uid_unique"]:
                reasons.append("TARGET_TABLE_UID_NOT_UNIQUE")
            for name in (
                "document_id_match",
                "entity_match",
                "report_year_match",
                "local_ordinal_match",
                "source_provenance_present",
                "context_provenance_aligned",
            ):
                if not checks[name]:
                    reasons.append(f"{name.upper()}_FAILED")
            raw_checks, raw_reasons, actual_source_hash = _raw_ocr_check(table, spec)
            checks["raw_ocr"] = raw_checks
            source_hash = actual_source_hash or source_hash
            reasons.extend(raw_reasons)
            period_checks, period_reasons = _period_check(table, context, document_metadata, spec)
            checks["period"] = period_checks
            reasons.extend(
                reason for reason in period_reasons if reason != "DOCUMENT_METADATA_PERIOD_MISSING_OR_MISMATCH"
            )
            if "DOCUMENT_METADATA_PERIOD_MISSING_OR_MISMATCH" in period_reasons:
                checks["period"]["metadata_gap_is_non_authorizing_risk"] = True
            unit_checks, unit_reasons = _unit_check(table, context, spec)
            checks["unit"] = unit_checks
            reasons.extend(unit_reasons)
            scope_checks, scope_reasons = _scope_check(table, context, document_metadata, spec)
            checks["scope"] = scope_checks
            reasons.extend(scope_reasons)
            header_checks, header_reasons, header_columns = _header_check(table, context, spec)
            checks["header"] = header_checks
            reasons.extend(header_reasons)
            row_checks, row_reasons = _row_check(table, context, spec)
            checks["row"] = row_checks
            reasons.extend(row_reasons)
            target = _mapping(spec.get("target"))
            target_row_index = _int(target.get("row_index"), label="target row index")
            target_column_index = _int(target.get("column_index"), label="target column index")
            rows = table.get("rows") or []
            if (
                0 <= target_row_index < len(rows)
                and isinstance(rows[target_row_index], list)
                and 0 <= target_column_index < len(rows[target_row_index])
            ):
                target_raw = _text(rows[target_row_index][target_column_index])
                source_cell_hash = _sha256_bytes(target_raw.encode("utf-8"))
            expected_context = _mapping(context.get("quality"))
            checks["context_quality_review_ready"] = _text(expected_context.get("status")) == "review_ready"
            if not checks["context_quality_review_ready"]:
                reasons.append("V3_CONTEXT_NOT_REVIEW_READY")

        question_match = questions.get(question_id) == _text(spec.get("question"))
        checks["question_text_match"] = question_match
        if not question_match:
            reasons.append("QUESTION_TEXT_MISMATCH")
        hypothesis_results = [
            _hypothesis_check(hypothesis, tables, contexts, raw_hypothesis_sources)
            for hypothesis in spec.get("hypotheses") or []
        ]
        if any(result["status"] == "UNVERIFIED_EVIDENCE" for result in hypothesis_results):
            reasons.append("HYPOTHESIS_EVIDENCE_INCOMPLETE")
        if not any(result["status"] == "SUPPORTED" for result in hypothesis_results):
            reasons.append("NO_SUPPORTED_HYPOTHESIS")

        # Only this explicit conflict is a hard semantic gate after all source
        # anchors are present.  It is the Q242 failure mode: raw V2 is clear,
        # but the V3 canonical header points at data cells.
        header_status = _text(_mapping(checks.get("header")).get("status"))
        if header_status in {
            "V2_RAW_HEADER_VERIFIED_V3_CONFLICT",
            "HEADER_PROVENANCE_INCOMPLETE",
            "V2_RAW_HEADER_VERIFIED_DECLARED_HEADER_ROWS_CONFLICT",
        }:
            reasons.append("HEADER_PROVENANCE_NOT_RECONCILED")
        table_identity_checks = (
            "table_context_present",
            "table_uid_unique",
            "document_id_match",
            "entity_match",
            "report_year_match",
            "local_ordinal_match",
            "source_provenance_present",
            "context_provenance_aligned",
        )
        unique_gates = {
            "table": all(bool(checks.get(name)) for name in table_identity_checks),
            "row": bool(_mapping(checks.get("row")).get("row_label_exact") and _mapping(checks.get("row")).get("target_cell_is_numeric")),
            "column": bool(_mapping(checks.get("header")).get("target_semantic_column_unique")),
            "header_provenance": header_status == "ALIGNED_V2_V3",
            "period": bool(_mapping(checks.get("period")).get("source_context_exact")),
            "unit": bool(_mapping(checks.get("unit")).get("unique_source_unit")),
            "scope": bool(_mapping(checks.get("scope")).get("scope_unique")),
            "raw_ocr": bool(_mapping(checks.get("raw_ocr")).get("source_sha256_matches_v2") and _mapping(checks.get("raw_ocr")).get("table_boundary_present") and _mapping(checks.get("raw_ocr")).get("all_context_fragments_present")),
        }
        all_required_gates = all(unique_gates.values()) and not any(
            reason in {"HYPOTHESIS_EVIDENCE_INCOMPLETE", "NO_SUPPORTED_HYPOTHESIS", "QUESTION_TEXT_MISMATCH"}
            for reason in reasons
        )
        candidate_status = "CANDIDATE_ONLY_READY" if all_required_gates else "QUARANTINED"
        if candidate_status == "QUARANTINED":
            if header_status != "ALIGNED_V2_V3":
                quarantine_reason = "HEADER_PROVENANCE_NOT_RECONCILED"
            elif not unique_gates["period"]:
                quarantine_reason = "PERIOD_MISSING_OR_AMBIGUOUS"
            elif not unique_gates["column"]:
                quarantine_reason = "COLUMN_ROLE_NOT_UNIQUE"
            elif not unique_gates["scope"]:
                quarantine_reason = "SCOPE_NOT_UNIQUE"
            else:
                quarantine_reason = sorted(set(reasons))[0] if reasons else "REQUIRED_GATE_FAILED"
        else:
            quarantine_reason = None

        target = _mapping(spec.get("target"))
        target_column_index = _int(target.get("column_index"), label="target column index")
        selected_header = next(
            (column for column in header_columns if column.get("column_index") == target_column_index),
            {},
        )
        provenance = _mapping(table.get("source_provenance")) if table else {}
        diagnostic = {
            "protocol": PROTOCOL,
            "schema_version": SCHEMA_VERSION,
            "question_id": question_id,
            "question": _text(spec.get("question")),
            "entity": _text(spec.get("entity")),
            "entity_role": _text(spec.get("entity_role")),
            "document_id": document_id,
            "internal_table_uid": uid,
            "candidate_status": candidate_status,
            "quarantine_reason": quarantine_reason,
            "unique_gate_status": unique_gates,
            "blocker_codes": sorted(set(reasons)) if candidate_status == "QUARANTINED" else [],
            "source_locator": {
                "source_path": _text(provenance.get("source_path")),
                "source_sha256": source_hash,
                "table_sha256": _text(provenance.get("table_sha256")),
                "char_start": provenance.get("char_start"),
                "local_ordinal": table.get("local_ordinal") if table else None,
                "page_no": table.get("page_no") if table else None,
            },
            "table_shape": {
                "row_count": len(table.get("rows") or []) if table else 0,
                "column_count": max((len(row) for row in (table.get("rows") or [])), default=0) if table else 0,
            },
            "period": _mapping(checks.get("period")),
            "unit": _mapping(checks.get("unit")),
            "scope": _mapping(checks.get("scope")),
            "column_role_diagnostic": header_columns,
            "selected_cell": {
                "row_index": target.get("row_index"),
                "row_label": _text(target.get("row_label")),
                "row_role": _text(target.get("row_role")),
                "column_index": target.get("column_index"),
                "column_role": _text(target.get("column_role")),
                "semantic_column_label": _text(selected_header.get("semantic_label")),
                "header_source_cells": selected_header.get("header_source_cells") or [],
                "header_provenance": selected_header.get("v2_header_provenance") or [],
                "source_cell_sha256": source_cell_hash,
            },
            "parent_row_diagnostic": _mapping(checks.get("row")),
            "hypotheses": hypothesis_results,
            "checks": checks,
            "raw_numeric_values_included": False,
            "source_contract": dict(SOURCE_CONTRACT),
        }
        if _contains_forbidden_key(diagnostic):
            raise ValueError(f"diagnostic Q{question_id} contains a forbidden key")
        diagnostics.append(diagnostic)
        packet = {
            "protocol": PROTOCOL,
            "schema_version": SCHEMA_VERSION,
            "question_id": question_id,
            "operand_id": "x0",
            "candidate_status": candidate_status,
            "internal_table_uid": uid,
            "document_id": document_id,
            "entity": _text(spec.get("entity")),
            "entity_role": _text(spec.get("entity_role")),
            "report_scope": _text(spec.get("report_scope")),
            "report_year": spec.get("report_year"),
            "period_end": _text(_mapping(spec.get("period")).get("end")),
            "period_role": _text(_mapping(spec.get("period")).get("header_role")),
            "source_unit": _text(spec.get("source_unit")),
            "requested_output_unit": _text(spec.get("requested_unit")),
            "unit_conversion_status": "DEFERRED_TO_AGENT4_DECIMAL_REPLAY",
            "row_index": target.get("row_index"),
            "row_label": _text(target.get("row_label")),
            "row_role": _text(target.get("row_role")),
            "column_index": target.get("column_index"),
            "column_role": _text(target.get("column_role")),
            "header_source_cells": selected_header.get("header_source_cells") or [],
            "header_provenance": selected_header.get("v2_header_provenance") or [],
            "source_path": _text(provenance.get("source_path")),
            "source_sha256": source_hash,
            "table_sha256": _text(provenance.get("table_sha256")),
            "source_cell_sha256": source_cell_hash,
            "risk_codes": sorted(
                set(
                    [
                        reason
                        for reason in reasons
                        if reason in {
                            "DOCUMENT_METADATA_PERIOD_MISSING_OR_MISMATCH",
                            "V2_DECLARED_HEADER_ROWS_CONFLICT",
                            "V3_CANONICAL_HEADER_CONFLICT",
                        }
                    ]
                )
            ),
            "raw_numeric_values_included": False,
            "source_contract": dict(SOURCE_CONTRACT),
        }
        if _contains_forbidden_key(packet):
            raise ValueError(f"candidate Q{question_id} contains a forbidden key")
        if candidate_status == "CANDIDATE_ONLY_READY":
            candidates.append(packet)
        else:
            quarantine.append(
                {
                    "protocol": PROTOCOL,
                    "schema_version": SCHEMA_VERSION,
                    "question_id": question_id,
                    "internal_table_uid": uid,
                    "candidate_status": candidate_status,
                    "quarantine_reason": quarantine_reason,
                    "blocker_codes": sorted(set(reasons)),
                    "proposed_coordinate": {
                        "row_index": target.get("row_index"),
                        "column_index": target.get("column_index"),
                    },
                    "source_locator": {
                        "source_path": _text(provenance.get("source_path")),
                        "source_sha256": source_hash,
                        "table_sha256": _text(provenance.get("table_sha256")),
                        "char_start": provenance.get("char_start"),
                    },
                    "raw_numeric_values_included": False,
                    "source_contract": dict(SOURCE_CONTRACT),
                }
            )

    diagnostics.sort(key=lambda row: int(row["question_id"]))
    candidates.sort(key=lambda row: int(row["question_id"]))
    quarantine.sort(key=lambda row: int(row["question_id"]))
    summary = {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "target_question_count": len(diagnostics),
        "candidate_packet_count": len(candidates),
        "quarantine_count": len(quarantine),
        "candidate_status_counts": dict(
            sorted(Counter(row["candidate_status"] for row in diagnostics).items())
        ),
        "question_ids": [int(row["question_id"]) for row in diagnostics],
        "raw_numeric_values_included": False,
        "source_contract": dict(SOURCE_CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        diagnostic_path = temporary / "multicol_semantic_diagnostic_v1.jsonl"
        candidate_path = temporary / "candidate_packets_v1.jsonl"
        quarantine_path = temporary / "quarantine_ledger_v1.jsonl"
        summary_path = temporary / "summary.json"
        _write_jsonl(diagnostic_path, diagnostics)
        _write_jsonl(candidate_path, candidates)
        _write_jsonl(quarantine_path, quarantine)
        _write_json(summary_path, summary)
        inputs = _manifest_inputs(
            {
                "config": config_path,
                "questions": questions_path,
                "structured_tables_v2": structured_tables_path,
                "evidence_context_v3": evidence_context_path,
                "document_metadata": document_metadata_path,
            }
        )
        outputs = {
            name: {"path": path.name, "sha256": sha256_file(path)}
            for name, path in {
                "diagnostic": diagnostic_path,
                "candidates": candidate_path,
                "quarantine": quarantine_path,
                "summary": summary_path,
            }.items()
        }
        _write_json(
            temporary / "manifest.json",
            {
                "protocol": PROTOCOL,
                "schema_version": SCHEMA_VERSION,
                "inputs": inputs,
                "outputs": outputs,
                "source_contract": dict(SOURCE_CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def _verify_manifest(artifact_dir: Path) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != SOURCE_CONTRACT:
        raise ValueError("unexpected multi-column diagnostic manifest")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(_text(_mapping(descriptor).get("path")))
        if not path.is_file() or sha256_file(path) != _text(_mapping(descriptor).get("sha256")):
            raise ValueError("multi-column diagnostic input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / _text(_mapping(descriptor).get("path"))
        if not path.is_file() or sha256_file(path) != _text(_mapping(descriptor).get("sha256")):
            raise ValueError("multi-column diagnostic output hash mismatch")
    return manifest


def validate_multicol_semantic_diagnostic(
    artifact_dir: Path, *, expected_question_ids: Iterable[int] = (156, 242, 263)
) -> dict[str, Any]:
    """Validate the candidate/quarantine boundary and all required gates."""
    _verify_manifest(artifact_dir)
    expected = {int(value) for value in expected_question_ids}
    diagnostics = _read_jsonl(artifact_dir / "multicol_semantic_diagnostic_v1.jsonl")
    candidates = _read_jsonl(artifact_dir / "candidate_packets_v1.jsonl")
    quarantine = _read_jsonl(artifact_dir / "quarantine_ledger_v1.jsonl")
    summary = _read_json(artifact_dir / "summary.json")
    diagnostic_ids = [int(row.get("question_id")) for row in diagnostics]
    if set(diagnostic_ids) != expected or len(diagnostic_ids) != len(expected):
        raise ValueError("diagnostic question coverage is invalid")
    if len({int(row.get("question_id")) for row in candidates}) != len(candidates):
        raise ValueError("candidate packet question IDs are not unique")
    if len({int(row.get("question_id")) for row in quarantine}) != len(quarantine):
        raise ValueError("quarantine question IDs are not unique")
    if set(int(row.get("question_id")) for row in candidates) & set(
        int(row.get("question_id")) for row in quarantine
    ):
        raise ValueError("question appears in both candidate and quarantine ledgers")
    for row in [*diagnostics, *candidates, *quarantine]:
        if _contains_forbidden_key(row):
            raise ValueError("forbidden value/authorization key found in diagnostic artifact")
        if row.get("raw_numeric_values_included") is not False:
            raise ValueError("diagnostic artifact does not declare numeric redaction")
        if row.get("source_contract") != SOURCE_CONTRACT:
            raise ValueError("diagnostic source contract changed")
    diagnostic_by_id = {int(row["question_id"]): row for row in diagnostics}
    candidate_ids = {int(row["question_id"]) for row in candidates}
    quarantine_ids = {int(row["question_id"]) for row in quarantine}
    for question_id, row in diagnostic_by_id.items():
        status = _text(row.get("candidate_status"))
        if status == "CANDIDATE_ONLY_READY" and question_id not in candidate_ids:
            raise ValueError("ready diagnostic has no candidate packet")
        if status != "CANDIDATE_ONLY_READY" and question_id not in quarantine_ids:
            raise ValueError("quarantined diagnostic has no quarantine record")
        gates = row.get("unique_gate_status") or {}
        required = {"table", "row", "column", "header_provenance", "period", "unit", "scope", "raw_ocr"}
        if set(gates) != required:
            raise ValueError("diagnostic gate set is incomplete")
        if status == "CANDIDATE_ONLY_READY" and not all(bool(gates[key]) for key in required):
            raise ValueError("candidate packet bypassed a required gate")
        selected = row.get("selected_cell") or {}
        for key in ("row_index", "column_index", "header_source_cells", "header_provenance"):
            if key not in selected:
                raise ValueError(f"diagnostic missing selected-cell {key}")
    expected_counts = dict(sorted(Counter(row["candidate_status"] for row in diagnostics).items()))
    if summary.get("candidate_status_counts") != expected_counts:
        raise ValueError("diagnostic summary counts do not match")
    if summary.get("target_question_count") != len(diagnostics):
        raise ValueError("diagnostic summary count does not match")
    return {
        "status": "PASS",
        "target_question_count": len(diagnostics),
        "candidate_packet_count": len(candidates),
        "quarantine_count": len(quarantine),
        "candidate_question_ids": sorted(candidate_ids),
        "quarantine_question_ids": sorted(quarantine_ids),
        "answer_eligible": False,
        "submission_eligible": False,
    }
