"""Fail-closed authorization receipts for the full-corpus grounded replay.

The legacy V2 replay proves exact cells and Decimal arithmetic.  It does not
prove that a candidate cell has the requested variable, period, entity, scope
or revision semantics.  This adapter makes that gap executable: it converts
only source-preserved V2 facts into typed Evidence Bindings and emits an
Answer Certificate or ``ABSTAIN`` for every question.  It never fills a
missing semantic field from a route score, registry label, filename, or model.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import unicodedata

import yaml

from .answer_certificates import (
    compile_abstention_certificate,
    compile_answer_certificate,
    temporal_contract_for_operand,
)
from .evidence_binding import (
    BINDING_FIELDS,
    COMPARATIVE_BASES,
    FLOW_OR_STOCK,
    PERIOD_GRAINS,
    SEMANTIC_UNITS,
    NOT_APPLICABLE,
    PASS,
    UNRESOLVED,
    EVIDENCE_BINDING_SCHEMA_VERSION,
    build_evidence_binding,
)
from .semantic_approvals import load_human_semantic_approvals
from .exact_cell_bindings_v2 import resolve_source_unit


GROUNDED_AUTHORIZATION_PROTOCOL = "vifinqa_grounded_authorization_replay_v1"
GROUNDED_AUTHORIZATION_SCHEMA_VERSION = 1
AUTHORIZATION_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "may_materialize_answer": False,
}


class GroundedAuthorizationError(ValueError):
    """Raised when full-corpus authorization inputs lose their hash lineage."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise GroundedAuthorizationError(f"{path}:{line_number} must be a JSON object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise GroundedAuthorizationError(f"{path} must contain a JSON object")
    return value


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _require_output_sha(manifest_path: Path, artifact_path: Path, *, output_name: str, label: str) -> None:
    manifest = _json(manifest_path)
    expected = ((manifest.get("outputs") or {}).get(output_name) or {}).get("sha256")
    if not isinstance(expected, str) or sha256_file(artifact_path) != expected:
        raise GroundedAuthorizationError(f"SHA-256 mismatch for {label}")


def _require_evidence_context_lineage(
    manifest_path: Path,
    evidence_context_path: Path,
    structured_tables_path: Path,
) -> None:
    """Require V3 to be the declared, exact derivative of the supplied V2."""

    manifest = _json(manifest_path)
    if _text(manifest.get("sidecar_sha256")) != sha256_file(evidence_context_path):
        raise GroundedAuthorizationError("SHA-256 mismatch for V3 evidence context")
    if _text(manifest.get("input_structure_sha256")) != sha256_file(structured_tables_path):
        raise GroundedAuthorizationError("V3 evidence context does not derive from supplied V2 tables")


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _table_index(path: Path) -> dict[str, Mapping[str, Any]]:
    values = _rows(path)
    tables: dict[str, Mapping[str, Any]] = {}
    for table in values:
        uid = _text(table.get("internal_table_uid"))
        if not uid or uid in tables:
            raise GroundedAuthorizationError("structured tables require unique internal_table_uid")
        tables[uid] = table
    return tables


def _evidence_context_index(path: Path) -> dict[str, Mapping[str, Any]]:
    contexts: dict[str, Mapping[str, Any]] = {}
    for context in _rows(path):
        uid = _text(context.get("internal_table_uid"))
        if not uid or uid in contexts:
            raise GroundedAuthorizationError("V3 evidence context requires unique internal_table_uid")
        contexts[uid] = context
    return contexts


def _registry(path: Path) -> dict[str, Mapping[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise GroundedAuthorizationError("metric registry must be a mapping")
    values: dict[str, Mapping[str, Any]] = {}
    for metric in payload.get("metrics") or []:
        if not isinstance(metric, Mapping):
            raise GroundedAuthorizationError("metric registry entry must be a mapping")
        metric_id = _text(metric.get("metric_id"))
        if not metric_id or metric_id in values:
            raise GroundedAuthorizationError("metric registry requires unique metric_id")
        values[metric_id] = metric
    return values


def _cell_anchor(table: Mapping[str, Any], *, row_index: object, column_index: object) -> dict[str, Any] | None:
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        return None
    rows = table.get("rows") or []
    if row_index < 0 or column_index < 0 or row_index >= len(rows):
        return None
    row = rows[row_index]
    if not isinstance(row, list) or column_index >= len(row):
        return None
    document_uid = _text(table.get("document_id"))
    table_uid = _text(table.get("internal_table_uid"))
    if not document_uid or not table_uid:
        return None
    raw = str(row[column_index])
    return {
        "kind": "raw_cell",
        "document_uid": document_uid,
        "internal_table_uid": table_uid,
        "row_index": row_index,
        "column_index": column_index,
        "raw_text_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


def _document_anchor(table: Mapping[str, Any]) -> dict[str, Any] | None:
    provenance = _mapping(table.get("source_provenance"))
    document_uid = _text(table.get("document_id"))
    source_sha256 = _text(provenance.get("source_sha256"))
    if not document_uid or len(source_sha256) != 64:
        return None
    return {
        "kind": "document_metadata",
        "document_uid": document_uid,
        "raw_text_sha256": source_sha256,
    }


def _header_anchors(table: Mapping[str, Any], operand: Mapping[str, Any]) -> list[dict[str, Any]]:
    coordinates: list[tuple[object, object]] = []
    for raw in operand.get("header_source_cells") or []:
        raw_mapping = _mapping(raw)
        coordinates.append((raw_mapping.get("row_index"), raw_mapping.get("column_index")))
    for raw in operand.get("source_unit_anchors") or []:
        raw_mapping = _mapping(raw)
        coordinates.append((raw_mapping.get("row_index"), raw_mapping.get("column_index")))
    anchors: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for row_index, column_index in coordinates:
        if not isinstance(row_index, int) or not isinstance(column_index, int):
            continue
        if (row_index, column_index) in seen:
            continue
        anchor = _cell_anchor(table, row_index=row_index, column_index=column_index)
        if anchor is not None:
            seen.add((row_index, column_index))
            anchors.append(anchor)
    return anchors


def _raw_text(table: Mapping[str, Any], anchor: Mapping[str, Any]) -> str:
    rows = table.get("rows") or []
    row_index, column_index = anchor.get("row_index"), anchor.get("column_index")
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        return ""
    if row_index < 0 or row_index >= len(rows) or not isinstance(rows[row_index], list):
        return ""
    row = rows[row_index]
    return str(row[column_index]) if 0 <= column_index < len(row) else ""


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", _text(value).casefold())
    return " ".join(
        "".join(character for character in normalized if not unicodedata.combining(character)).split()
    )


_NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_VIETNAMESE_DATE_RE = re.compile(
    r"(?:ngay\s+)?(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s*(?:[|·:]\s*)?(\d{4})"
)


def _dates_in_text(value: object) -> list[date]:
    folded = _fold(value)
    values: list[date] = []
    for pattern in (_NUMERIC_DATE_RE, _VIETNAMESE_DATE_RE):
        for day, month, year in pattern.findall(folded):
            try:
                parsed = date(int(year), int(month), int(day))
            except ValueError:
                continue
            if parsed not in values:
                values.append(parsed)
    return values


def _context_is_source_aligned(
    table: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> bool:
    if context is None:
        return False
    table_provenance = _mapping(table.get("source_provenance"))
    context_provenance = _mapping(context.get("source_provenance"))
    return bool(
        _text(table.get("internal_table_uid"))
        and _text(table.get("internal_table_uid")) == _text(context.get("internal_table_uid"))
        and _text(table.get("document_id")) == _text(context.get("document_id"))
        and _text(table_provenance.get("source_sha256"))
        == _text(context_provenance.get("source_sha256"))
        and _text(table_provenance.get("table_sha256"))
        == _text(context_provenance.get("table_sha256"))
        and _mapping(context.get("grid")).get("rectangular") is True
        and _mapping(context.get("grid")).get("provenance_complete") is True
        and _text(_mapping(context.get("quality")).get("status")) == "review_ready"
    )


def _context_header(
    context: Mapping[str, Any],
    *,
    column_index: object,
) -> Mapping[str, Any] | None:
    matches = [
        value
        for value in _mapping(context.get("canonical_headers")).get("columns") or []
        if isinstance(value, Mapping) and value.get("column_index") == column_index
    ]
    return matches[0] if len(matches) == 1 else None


def _coordinates(values: object) -> list[tuple[int, int]]:
    coordinates: list[tuple[int, int]] = []
    for value in values if isinstance(values, list) else []:
        item = _mapping(value)
        row_index, column_index = item.get("row_index"), item.get("column_index")
        if isinstance(row_index, int) and isinstance(column_index, int):
            coordinates.append((row_index, column_index))
    return coordinates


def _period_binding(
    table: Mapping[str, Any] | None,
    operand: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve only periods directly supported by V3 header and source-title facts."""

    if table is None or _text(operand.get("binding_status")) != "binding_ready":
        return {"status": UNRESOLVED, "reason_codes": ["EXACT_VALUE_CELL_NOT_READY"]}
    if not _context_is_source_aligned(table, context):
        return {"status": UNRESOLVED, "reason_codes": ["V3_CONTEXT_NOT_SOURCE_ALIGNED"]}
    assert context is not None
    header = _context_header(context, column_index=operand.get("column_index"))
    if header is None:
        return {"status": UNRESOLVED, "reason_codes": ["V3_HEADER_NOT_UNIQUE"]}
    header_coordinates = _coordinates(header.get("header_source_cells"))
    if not header_coordinates or header_coordinates != _coordinates(operand.get("header_source_cells")):
        return {"status": UNRESOLVED, "reason_codes": ["V2_V3_HEADER_ANCHORS_MISMATCH"]}
    anchors = [
        anchor
        for row_index, column_index in header_coordinates
        if (anchor := _cell_anchor(table, row_index=row_index, column_index=column_index)) is not None
    ]
    if len(anchors) != len(header_coordinates):
        return {"status": UNRESOLVED, "reason_codes": ["V3_HEADER_ANCHOR_INVALID"]}
    raw_header = " | ".join(_raw_text(table, anchor) for anchor in anchors)
    source_label = _text(header.get("source_label"))
    table_function = _text(_mapping(context.get("table_function")).get("kind"))
    document_anchor = _document_anchor(table)
    source_title = _text(_mapping(context.get("context_trace")).get("source_title"))

    if _text(operand.get("period_resolution_method")) == "v2_exact_source_title_current_header_v1":
        expected_title_hash = hashlib.sha256(source_title.encode("utf-8")).hexdigest()
        if _text(operand.get("period_source_title_sha256")) != expected_title_hash:
            return {"status": UNRESOLVED, "reason_codes": ["PERIOD_SOURCE_TITLE_HASH_MISMATCH"]}
        period_labels = [_text(value) for value in operand.get("period_labels") or [] if _text(value)]
        years = {
            int(match.group(1))
            for value in period_labels
            if (match := re.fullmatch(r"(?:nam\s+)?(20\d{2})", _fold(value))) is not None
        }
        if len(years) != 1:
            return {"status": UNRESOLVED, "reason_codes": ["RECOVERED_PERIOD_YEAR_NOT_UNIQUE"]}
        requested_year = next(iter(years))
        matching_dates = [value for value in _dates_in_text(source_title) if value.year == requested_year]
        if len(matching_dates) != 1 or matching_dates[0].isoformat() != _text(operand.get("period_source_date")):
            return {"status": UNRESOLVED, "reason_codes": ["RECOVERED_SOURCE_TITLE_DATE_MISMATCH"]}
        period_date = matching_dates[0]
        folded_header = _fold(raw_header or source_label)
        if document_anchor is None:
            return {"status": UNRESOLVED, "reason_codes": ["RECOVERED_PERIOD_DOCUMENT_ANCHOR_MISSING"]}
        if table_function == "balance_sheet" and folded_header == "so cuoi nam":
            return {
                "status": PASS,
                "raw_period_label": raw_header or source_label,
                "period_grain": "instant",
                "flow_or_stock": "stock",
                "comparative_basis": "current",
                "period_years": [requested_year],
                "as_of_date": period_date.isoformat(),
                "source_anchors": [*anchors, document_anchor],
                "recognition_method": "v3_exact_source_title_current_balance_header_v1",
            }
        folded_title = _fold(source_title)
        if (
            table_function in {"income_statement", "cash_flow_statement"}
            and folded_header == "nam nay"
            and re.search(r"(?:cho|trong)\s+nam(?:\s+tai\s+chinh)?\s+ket\s+thuc\s+ngay", folded_title)
        ):
            try:
                prior_anniversary = period_date.replace(year=period_date.year - 1)
            except ValueError:
                prior_anniversary = period_date.replace(year=period_date.year - 1, day=28)
            start_date = prior_anniversary + timedelta(days=1)
            return {
                "status": PASS,
                "raw_period_label": raw_header or source_label,
                "period_grain": "fiscal_year",
                "flow_or_stock": "flow",
                "comparative_basis": "current",
                "period_years": [requested_year],
                "start_date": start_date.isoformat(),
                "end_date": period_date.isoformat(),
                "fiscal_year": requested_year,
                "source_anchors": [*anchors, document_anchor],
                "recognition_method": "v3_exact_source_title_current_duration_header_v1",
            }
        return {"status": UNRESOLVED, "reason_codes": ["RECOVERED_CURRENT_HEADER_SEMANTICS_MISMATCH"]}

    if table_function == "balance_sheet":
        dates = _dates_in_text(raw_header)
        if not dates:
            dates = _dates_in_text(source_label)
        if len(dates) != 1:
            return {"status": UNRESOLVED, "reason_codes": ["BALANCE_SHEET_AS_OF_DATE_NOT_UNIQUE"]}
        as_of = dates[0]
        comparative_basis = (
            "opening_balance"
            if (as_of.month, as_of.day) == (1, 1)
            else "closing_balance"
            if (as_of.month, as_of.day) == (12, 31)
            else "current"
        )
        return {
            "status": PASS,
            "raw_period_label": raw_header or source_label,
            "period_grain": "instant",
            "flow_or_stock": "stock",
            "comparative_basis": comparative_basis,
            "period_years": [as_of.year],
            "as_of_date": as_of.isoformat(),
            "source_anchors": anchors,
            "recognition_method": "v3_exact_header_balance_sheet_date_v1",
        }

    if table_function not in {"income_statement", "cash_flow_statement"}:
        return {"status": UNRESOLVED, "reason_codes": ["V3_TABLE_FUNCTION_NOT_PERIOD_AUTHORIZING"]}
    period_labels = [_text(value) for value in header.get("period_labels") or [] if _text(value)]
    years = {
        int(match.group(1))
        for value in period_labels
        if (match := re.fullmatch(r"(?:nam\s+)?(20\d{2})", _fold(value))) is not None
    }
    if len(years) != 1 or not any(str(next(iter(years))) in value for value in (raw_header, source_label)):
        return {"status": UNRESOLVED, "reason_codes": ["DURATION_HEADER_YEAR_NOT_UNIQUE"]}
    fiscal_year = next(iter(years))
    folded_title = _fold(source_title)
    if not re.search(
        r"(?:(?:cho|trong)\s+)?nam(?:\s+tai\s+chinh)?\s+ket\s+thuc\s+(?:vao\s+)?ngay",
        folded_title,
    ):
        return {"status": UNRESOLVED, "reason_codes": ["DURATION_SOURCE_TITLE_MISSING"]}
    title_dates = _dates_in_text(source_title)
    matching_end_dates = [value for value in title_dates if value.year == fiscal_year]
    comparative_basis = "current"
    recognition_method = "v3_exact_header_and_source_title_duration_v1"
    if len(matching_end_dates) == 1:
        end_date = matching_end_dates[0]
    elif len(matching_end_dates) == 0 and len(title_dates) == 1 and title_dates[0].year == fiscal_year + 1:
        # A comparative column can carry an exact prior year while the title
        # correctly names the current report period.  Preserve the title's
        # fiscal month/day and move it back exactly one year; no other date or
        # ambiguous title is accepted.
        try:
            end_date = title_dates[0].replace(year=fiscal_year)
        except ValueError:
            end_date = title_dates[0].replace(year=fiscal_year, day=28)
        comparative_basis = "prior_year"
        recognition_method = "v3_exact_comparative_header_and_next_report_title_duration_v1"
    else:
        return {"status": UNRESOLVED, "reason_codes": ["DURATION_END_DATE_NOT_UNIQUE"]}
    try:
        prior_anniversary = end_date.replace(year=end_date.year - 1)
    except ValueError:
        prior_anniversary = end_date.replace(year=end_date.year - 1, day=28)
    start_date = prior_anniversary + timedelta(days=1)
    if document_anchor is None:
        return {"status": UNRESOLVED, "reason_codes": ["DURATION_DOCUMENT_ANCHOR_MISSING"]}
    return {
        "status": PASS,
        "raw_period_label": raw_header or source_label,
        "period_grain": "fiscal_year",
        "flow_or_stock": "flow",
        "comparative_basis": comparative_basis,
        "period_years": [fiscal_year],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "fiscal_year": fiscal_year,
        "source_anchors": [*anchors, document_anchor],
        "recognition_method": recognition_method,
    }


def _entity_scope_binding(
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    *,
    requested_entity: str,
    requested_scope: str,
) -> dict[str, Any]:
    """Bind explicit source scope while refusing ticker inference from metadata."""

    unresolved = {
        "entity_status": UNRESOLVED,
        "scope_status": UNRESOLVED,
        "entity": requested_entity or None,
        "scope": requested_scope or None,
        "source_anchors": [],
        "reason_codes": ["SOURCE_ENTITY_TICKER_ALIAS_NOT_EVIDENCE_ELIGIBLE"],
    }
    if table is None or not _context_is_source_aligned(table, context):
        return unresolved
    assert context is not None
    source_title = _text(_mapping(context.get("context_trace")).get("source_title"))
    folded_title = _fold(source_title)
    detected = {
        scope
        for scope, pattern in {
            "separate": r"\b(?:rieng|bao cao tai chinh rieng)\b",
            "consolidated": r"\b(?:hop nhat|bao cao tai chinh hop nhat)\b",
        }.items()
        if re.search(pattern, folded_title)
    }
    document_anchor = _document_anchor(table)
    if len(detected) != 1 or document_anchor is None:
        return unresolved
    return {
        **unresolved,
        "scope_status": PASS,
        "scope": next(iter(detected)),
        "source_anchors": [document_anchor],
        "recognition_method": "v3_literal_source_title_scope_v1",
    }


def _unit_binding(
    table: Mapping[str, Any] | None,
    operand: Mapping[str, Any],
    evidence_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if table is None or _text(operand.get("binding_status")) != "binding_ready":
        return {"status": UNRESOLVED, "reason_codes": ["UNIT_NOT_EXACT_CELL_BOUND"]}
    multiplier = _text(operand.get("source_to_vnd_multiplier"))
    source_unit = _text(operand.get("source_unit"))
    try:
        valid_multiplier = Decimal(multiplier) > 0
    except InvalidOperation:
        valid_multiplier = False
    if not source_unit or not valid_multiplier:
        return {"status": UNRESOLVED, "reason_codes": ["UNIT_SOURCE_ANCHOR_MISSING_OR_CONFLICTING"]}
    if _text(operand.get("unit_resolution_method")) == "v3_exact_source_title_unit_v1":
        candidates = operand.get("source_unit_context_anchors") or []
        trace = _mapping(_mapping(evidence_context).get("context_trace"))
        title = _text(trace.get("source_title"))
        candidate = _mapping(candidates[0]) if len(candidates) == 1 else {}
        provenance = _mapping(table.get("source_provenance"))
        detected_unit, _, detected_multiplier = resolve_source_unit([{"raw_source_cell": title}])
        valid_context = (
            bool(candidate)
            and candidate.get("anchor_kind") == "evidence_context_source_title"
            and _text(candidate.get("document_id")) == _text(table.get("document_id"))
            and _text(candidate.get("internal_table_uid")) == _text(table.get("internal_table_uid"))
            and _text(candidate.get("source_sha256")) == _text(provenance.get("source_sha256"))
            and _text(candidate.get("table_sha256")) == _text(provenance.get("table_sha256"))
            and _text(candidate.get("source_title_sha256")) == hashlib.sha256(title.encode("utf-8")).hexdigest()
            and _text(candidate.get("evidence_context_row_sha256")) == _sha_json(evidence_context or {})
            and detected_unit == source_unit
            and detected_multiplier is not None
            and format(detected_multiplier, "f") == multiplier
        )
        if not valid_context:
            return {"status": UNRESOLVED, "reason_codes": ["UNIT_SOURCE_TITLE_ANCHOR_INVALID"]}
        return {
            "status": PASS,
            "raw_unit_label": _text(candidate.get("raw_unit_label")),
            "normalized_currency": "VND",
            "scale": multiplier,
            "semantic_unit": "monetary",
            "resolution_level": "document",
            "conversion_status": "PASS" if _text(_mapping(operand.get("requested_output_unit")).get("unit")) != "vnd" else "NOT_REQUIRED",
            "source_anchors": [
                {
                    "kind": "document_metadata",
                    "document_uid": _text(table.get("document_id")),
                    "raw_text_sha256": _text(candidate.get("source_title_sha256")),
                    "anchor_method": "v3_exact_source_title_unit_v1",
                    "document_source_sha256": _text(candidate.get("source_sha256")),
                    "evidence_context_row_sha256": _text(candidate.get("evidence_context_row_sha256")),
                }
            ],
        }
    anchors = _header_anchors(table, operand)
    if not anchors:
        return {"status": UNRESOLVED, "reason_codes": ["UNIT_SOURCE_ANCHOR_MISSING_OR_CONFLICTING"]}
    raw_unit_label = " | ".join(dict.fromkeys(_raw_text(table, anchor) for anchor in anchors if _raw_text(table, anchor)))
    return {
        "status": PASS,
        "raw_unit_label": raw_unit_label,
        "normalized_currency": "VND",
        "scale": multiplier,
        "semantic_unit": "monetary",
        "resolution_level": "column",
        "conversion_status": "PASS" if _text(_mapping(operand.get("requested_output_unit")).get("unit")) != "vnd" else "NOT_REQUIRED",
        "source_anchors": anchors,
    }


def _source_integrity(table: Mapping[str, Any] | None, operand: Mapping[str, Any]) -> dict[str, Any]:
    if table is None or _text(operand.get("binding_status")) != "binding_ready":
        return {"status": UNRESOLVED, "reason_codes": ["EXACT_VALUE_CELL_NOT_READY"]}
    anchor = _cell_anchor(table, row_index=operand.get("row_index"), column_index=operand.get("column_index"))
    if anchor is None:
        return {"status": UNRESOLVED, "reason_codes": ["EXACT_VALUE_CELL_COORDINATE_INVALID"]}
    return {"status": PASS, "value_cell": anchor}


def _lineage(
    table: Mapping[str, Any] | None,
    *,
    source_integrity: Mapping[str, Any],
    variable_binding: Mapping[str, Any],
    period_binding: Mapping[str, Any],
    unit_binding: Mapping[str, Any],
    entity_scope_binding: Mapping[str, Any],
) -> dict[str, Any]:
    provenance = _mapping(table.get("source_provenance")) if table is not None else {}
    value_cell = _mapping(source_integrity.get("value_cell"))
    return {
        "document_sha256": _text(provenance.get("source_sha256")),
        "table_sha256": _text(provenance.get("table_sha256")),
        "raw_cell_sha256": _text(value_cell.get("raw_text_sha256")),
        "header_evidence_sha256": _sha_json(
            {
                "period_source_anchors": period_binding.get("source_anchors") or [],
                "unit_source_anchors": unit_binding.get("source_anchors") or [],
            }
        ),
        "semantic_evidence_sha256": _sha_json(
            {
                "variable_binding": variable_binding,
                "entity_scope_binding": entity_scope_binding,
            }
        ),
        "binding_schema_version": EVIDENCE_BINDING_SCHEMA_VERSION,
        "resolver_version": "grounded-authorization-v3-source-resolver-v1",
    }


def _operand_id(question_id: object, stage_id: object, role: object) -> str:
    return f"q{question_id}:stage:{_text(stage_id) or 'unresolved'}:role:{_text(role) or 'unresolved'}"


def _build_binding(
    *,
    question: Mapping[str, Any],
    stage: Mapping[str, Any],
    operand: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    evidence_contexts: Mapping[str, Mapping[str, Any]],
    approval: Mapping[str, Any] | None,
) -> dict[str, Any]:
    table = tables.get(_text(operand.get("internal_table_uid")))
    evidence_context = evidence_contexts.get(_text(operand.get("internal_table_uid")))
    document_uid = _text(table.get("document_id")) if table is not None else f"unresolved:q{question.get('question_id')}"
    table_uid = _text(table.get("internal_table_uid")) if table is not None else f"unresolved-table:q{question.get('question_id')}"
    context = _mapping(question.get("question_context"))
    entities = context.get("entities") or []
    entity = _text(entities[0]) if isinstance(entities, list) and len(entities) == 1 else ""
    scope = _text(context.get("scope"))
    requested_entity_role = _text(context.get("entity_role"))
    source_integrity = _source_integrity(table, operand)
    unit_binding = _unit_binding(table, operand, evidence_context)
    period_binding = _period_binding(table, operand, evidence_context)
    if approval is None or table is None:
        variable_binding = {
            "status": UNRESOLVED,
            "reason_codes": ["V2_ROUTE_CONCEPT_IS_NOT_SOURCE_VARIABLE_EVIDENCE"],
        }
        entity_scope_binding = _entity_scope_binding(
            table,
            evidence_context,
            requested_entity=entity,
            requested_scope=scope,
        )
    else:
        row_label = _mapping(approval.get("row_label"))
        row_anchor = _cell_anchor(
            table,
            row_index=row_label.get("row_index"),
            column_index=row_label.get("column_index"),
        )
        document_anchor = _document_anchor(table)
        if row_anchor is None or document_anchor is None:
            variable_binding = {
                "status": UNRESOLVED,
                "reason_codes": ["HUMAN_APPROVAL_SOURCE_ANCHOR_STALE"],
            }
            entity_scope_binding = _entity_scope_binding(
                table,
                evidence_context,
                requested_entity=entity,
                requested_scope=scope,
            )
        else:
            variable_provenance = _mapping(approval.get("variable_decision_provenance"))
            variable_reviewer_type = _text(variable_provenance.get("reviewer_type"))
            variable_binding = {
                "status": PASS,
                "variable_id": approval.get("variable_id"),
                "raw_row_label": _raw_text(table, row_anchor),
                "recognition_method": (
                    "chatgpt_verified_exact_row_navigation_promotion_v1"
                    if approval.get("navigation_promotion_lineage")
                    else "chatgpt_verified_exact_cross_entity_operand_v1"
                    if approval.get("cross_entity_promotion_lineage")
                    else "chatgpt_verified_exact_row_label_correction_v1"
                    if variable_reviewer_type == "chatgpt_verified"
                    else "human_verified_exact_row_label_v1"
                ),
                "source_anchors": [row_anchor],
                "approval_id": approval.get("variable_approval_id") or approval.get("approval_id"),
                **(
                    {"decision_provenance": dict(variable_provenance)}
                    if variable_provenance
                    else {}
                ),
                **(
                    {"semantic_correction_lineage": approval.get("semantic_correction_lineage")}
                    if approval.get("semantic_correction_lineage")
                    else {}
                ),
                **(
                    {"navigation_promotion_lineage": approval.get("navigation_promotion_lineage")}
                    if approval.get("navigation_promotion_lineage")
                    else {}
                ),
                **(
                    {"cross_entity_promotion_lineage": approval.get("cross_entity_promotion_lineage")}
                    if approval.get("cross_entity_promotion_lineage")
                    else {}
                ),
            }
            entity_scope_binding = {
                "entity_status": PASS,
                "scope_status": PASS,
                "entity": approval.get("entity"),
                "scope": approval.get("scope"),
                "source_anchors": [document_anchor],
                "recognition_method": (
                    "chatgpt_verified_issuer_scope_v1"
                    if _text(
                        _mapping(
                            approval.get("entity_scope_decision_provenance")
                        ).get("reviewer_type")
                    )
                    == "chatgpt_verified"
                    else "human_verified_issuer_scope_v1"
                ),
                "approval_id": approval.get("approval_id"),
                **(
                    {
                        "decision_provenance": dict(
                            _mapping(
                                approval.get("entity_scope_decision_provenance")
                                or approval.get("decision_provenance")
                            )
                        )
                    }
                    if approval.get("entity_scope_decision_provenance")
                    else {}
                ),
            }
    if requested_entity_role:
        approved_role = _text((approval or {}).get("entity_role"))
        role_evidence = _mapping((approval or {}).get("entity_role_evidence"))
        if approved_role == requested_entity_role and role_evidence:
            role_review_provenance = _mapping((approval or {}).get("entity_role_decision_provenance"))
            role_reviewer_type = _text(role_review_provenance.get("reviewer_type"))
            entity_role_binding = {
                "status": PASS,
                "role": approved_role,
                "recognition_method": (
                    "chatgpt_verified_document_entity_role_v1"
                    if role_reviewer_type == "chatgpt_verified"
                    else "human_verified_document_entity_role_v1"
                ),
                "source_anchors": list(role_evidence.get("source_anchors") or []),
                "approval_id": (approval or {}).get("approval_id"),
                "decision_provenance": dict(role_review_provenance),
            }
        else:
            entity_role_binding = {
                "status": UNRESOLVED,
                "role": requested_entity_role,
                "recognition_method": "explicit_question_role_requires_source_provenance_v1",
                "source_anchors": [],
                "reason_codes": ["ENTITY_ROLE_SOURCE_PROVENANCE_MISSING"],
            }
    else:
        entity_role_binding = {
            "status": NOT_APPLICABLE,
            "role": None,
            "recognition_method": "no_explicit_entity_role_claim_v1",
            "source_anchors": [],
        }
    revision_binding = {
        "status": NOT_APPLICABLE,
        "revision_policy": "latest_valid",
        "reason_codes": ["V2_REPLAY_HAS_NO_REVISION_SELECTION_EVIDENCE"],
    }
    return build_evidence_binding(
        operand_id=_operand_id(question.get("question_id"), stage.get("stage_id"), operand.get("role")),
        document_uid=document_uid,
        internal_table_uid=table_uid,
        source_integrity=source_integrity,
        variable_binding=variable_binding,
        period_binding=period_binding,
        unit_binding=unit_binding,
        entity_scope_binding=entity_scope_binding,
        entity_role_binding=entity_role_binding,
        revision_binding=revision_binding,
        binding_lineage=_lineage(
            table,
            source_integrity=source_integrity,
            variable_binding=variable_binding,
            period_binding=period_binding,
            unit_binding=unit_binding,
            entity_scope_binding=entity_scope_binding,
        ),
        required_fields=[
            field for field in BINDING_FIELDS
            if field != "revision" and (field != "entity_role" or requested_entity_role)
        ],
    )


def _temporal_shape(operand: Mapping[str, Any], metric: Mapping[str, Any] | None) -> tuple[list[str], list[str], list[str]]:
    metric_operands = {
        _text(value.get("role")): value
        for value in (metric or {}).get("operands") or []
        if isinstance(value, Mapping)
    }
    declared = _mapping(metric_operands.get(_text(operand.get("role"))))
    statement_types = {_text(value) for value in declared.get("statement_types") or []}
    if statement_types and statement_types.issubset({"balance_sheet"}):
        return ["instant"], ["stock"], ["current", "opening_balance", "closing_balance"]
    if statement_types and statement_types.issubset({"income_statement", "cash_flow_statement"}):
        return ["fiscal_year"], ["flow"], ["current"]
    return sorted(PERIOD_GRAINS), sorted(FLOW_OR_STOCK), sorted(COMPARATIVE_BASES)


def _formula_contract(stage: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]], registry: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    metric_id = _text(stage.get("metric_id"))
    metric = registry.get(metric_id)
    constraints: dict[str, Any] = {}
    for operand, binding in zip(stage.get("required_operands") or [], bindings, strict=True):
        grains, flow_or_stock, comparative_bases = _temporal_shape(_mapping(operand), metric)
        constraints[str(binding["operand_id"])] = {
            "period_grains": grains,
            "flow_or_stock": flow_or_stock,
            "comparative_bases": comparative_bases,
            "semantic_units": ["monetary"],
            "revision_policies": ["latest_valid"],
        }
    operand_ids = [str(binding["operand_id"]) for binding in bindings]
    return {
        "formula_id": metric_id or "reported_value_lookup",
        "contract_source": "metric_registry_statement_type_policy_v1",
        "operand_constraints": constraints,
        "cross_operand_rules": [
            {"kind": "same", "field": field, "operands": operand_ids}
            for field in ("entity", "entity_role", "scope", "normalized_currency")
            if len(operand_ids) >= 2
        ],
    }


def _replace_formula_roles(node: object, role_to_operand_id: Mapping[str, str]) -> object:
    """Translate registry role leaves to the immutable binding IDs in this run."""
    if isinstance(node, str):
        return role_to_operand_id.get(node, node)
    if isinstance(node, list):
        return [_replace_formula_roles(value, role_to_operand_id) for value in node]
    if isinstance(node, Mapping):
        return {str(key): _replace_formula_roles(value, role_to_operand_id) for key, value in node.items()}
    return node


def _binding_plan(
    *,
    question: Mapping[str, Any],
    stage: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    context = _mapping(question.get("question_context"))
    years = context.get("years") if isinstance(context.get("years"), list) else []
    entities = context.get("entities") or []
    entity = entities[0] if isinstance(entities, list) and len(entities) == 1 else None
    request = _mapping(question.get("requested_output_unit"))
    metric = registry.get(_text(stage.get("metric_id")))
    role_to_operand_id = {
        _text(original.get("role")): str(binding["operand_id"])
        for original, binding in zip(stage.get("required_operands") or [], bindings, strict=True)
    }
    operation_ast = (
        _replace_formula_roles(dict(metric.get("formula_ast") or {}), role_to_operand_id)
        if metric is not None
        else {"op": "lookup", "args": [str(bindings[0]["operand_id"])] if len(bindings) == 1 else []}
    )
    operands = []
    for original, binding in zip(stage.get("required_operands") or [], bindings, strict=True):
        operands.append(
            {
                "operand_id": binding["operand_id"],
                "role": original.get("role"),
                "expected_variable_id": original.get("concept_id") or original.get("role"),
                "required": True,
                "temporal_contract": temporal_contract_for_operand(
                    years=years,
                    scope=context.get("scope"),
                    requested_unit=request.get("unit"),
                    entity=entity,
                    entity_role=context.get("entity_role"),
                ),
            }
        )
    return {
        "operands": operands,
        "operation_ast": operation_ast,
        "formula_compatibility": _formula_contract(stage, bindings, registry),
    }


def _composed_binding_plan(
    *,
    question: Mapping[str, Any],
    materialized_stages: Sequence[
        tuple[Mapping[str, Any], list[Mapping[str, Any]], list[Mapping[str, Any]]]
    ],
) -> dict[str, Any]:
    """Build a typed cross-stage plan without requiring same-entity operands."""

    graph = _mapping(question.get("controlled_operation_graph"))
    stage_order = graph.get("stage_order")
    source_ast = graph.get("operation_ast")
    if (
        graph.get("protocol") != "vifinqa_controlled_composition_graph_v1"
        or not isinstance(stage_order, list)
        or len(stage_order) != 2
        or len({_text(value) for value in stage_order}) != 2
        or source_ast != {"op": "subtract", "args": stage_order}
    ):
        raise GroundedAuthorizationError("controlled cross-stage operation graph is invalid")
    by_stage = {_text(stage.get("stage_id")): (stage, operands, bindings) for stage, operands, bindings in materialized_stages}
    if set(by_stage) != {_text(value) for value in stage_order}:
        raise GroundedAuthorizationError("controlled graph stage coverage mismatch")
    request = _mapping(question.get("requested_output_unit"))
    context = _mapping(question.get("question_context"))
    years = context.get("years") if isinstance(context.get("years"), list) else []
    plan_operands: list[dict[str, Any]] = []
    binding_ids_by_stage: dict[str, str] = {}
    constraints: dict[str, dict[str, list[str]]] = {}
    operand_ids: list[str] = []
    bound_entities: list[str] = []
    for raw_stage_id in stage_order:
        stage_id = _text(raw_stage_id)
        _, original_operands, bindings = by_stage[stage_id]
        if len(original_operands) != 1 or len(bindings) != 1:
            raise GroundedAuthorizationError("controlled graph stages require one exact operand")
        original, binding = original_operands[0], bindings[0]
        operand_id = str(binding["operand_id"])
        binding_ids_by_stage[stage_id] = operand_id
        operand_ids.append(operand_id)
        entity_scope = _mapping(binding.get("entity_scope_binding"))
        entity_role = _mapping(binding.get("entity_role_binding"))
        bound_entities.append(_text(entity_scope.get("entity")))
        plan_operands.append(
            {
                "operand_id": operand_id,
                "role": original.get("role"),
                "stage_id": stage_id,
                "expected_variable_id": original.get("concept_id") or original.get("role"),
                "required": True,
                "temporal_contract": temporal_contract_for_operand(
                    years=years,
                    scope=context.get("scope"),
                    requested_unit=request.get("unit"),
                    entity=entity_scope.get("entity"),
                    entity_role=entity_role.get("role") or context.get("entity_role"),
                ),
            }
        )
        constraints[operand_id] = {
            "period_grains": ["fiscal_year"],
            "flow_or_stock": ["flow"],
            "comparative_bases": ["current"],
            "semantic_units": ["monetary"],
            "revision_policies": ["latest_valid"],
        }
    if not all(bound_entities) or len(set(bound_entities)) != len(bound_entities):
        raise GroundedAuthorizationError("cross-entity composition requires distinct proven entities")
    operation_ast = _replace_formula_roles(source_ast, binding_ids_by_stage)
    if graph.get("binding_operation_ast") != operation_ast:
        raise GroundedAuthorizationError("controlled graph binding operation AST mismatch")
    same_fields = (
        "entity_role", "scope", "period_years", "period_grain",
        "flow_or_stock", "comparative_basis", "normalized_currency",
        "semantic_unit", "revision_policy",
    )
    return {
        "operands": plan_operands,
        "operation_ast": operation_ast,
        "formula_compatibility": {
            "formula_id": "controlled_cross_entity_subtract",
            "contract_source": "chatgpt_reviewed_typed_composition_graph_v1",
            "operand_constraints": constraints,
            "cross_operand_rules": [
                {"kind": "same", "field": field, "operands": operand_ids}
                for field in same_fields
            ],
            "different_entity_required": True,
        },
        "composition_lineage": {
            "promotion_packet_sha256": graph.get("promotion_packet_sha256"),
            "promotion_decision_sha256": graph.get("promotion_decision_sha256"),
            "numeric_values_selected_by_reviewer": False,
        },
    }


def _execution_receipt(plan: Mapping[str, Any], trace: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "status": _text((trace or {}).get("status")) or "dependency_blocked",
        "answer_decimal": (trace or {}).get("converted_output_decimal"),
        "operation_ast_sha256": (
            (trace or {}).get("operation_ast_sha256")
            or _sha_json(plan.get("operation_ast") or {})
        ),
    }


def _counterfactual_checks(bindings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    fully_bound = bool(bindings) and all(
        _text(binding.get("binding_status")) == "BOUND"
        and binding.get("operand_eligible") is True
        for binding in bindings
    )
    if fully_bound:
        field_by_dimension = {
            "period": "period_binding",
            "scope": "entity_scope_binding",
            "unit": "unit_binding",
        }
        if any(_text((binding.get("entity_role_binding") or {}).get("status")) != NOT_APPLICABLE for binding in bindings):
            field_by_dimension["entity_role"] = "entity_role_binding"
        return [
            {
                "dimension": dimension,
                "status": "EXHAUSTED",
                "rejection_code": "NO_ALTERNATIVE_IN_HASH_BOUND_EXACT_BINDING_SET",
                "alternative_binding_id": "none-in-bounded-set",
                "candidate_count": len(bindings),
                "candidate_set_sha256": _sha_json(
                    [binding.get(field_name) or {} for binding in bindings]
                ),
                "enumeration_complete": True,
                "candidate_universe": "exact_cell_binding_stage_operands",
                "global_uniqueness_proven": False,
            }
            for dimension, field_name in field_by_dimension.items()
        ]
    return [
        {
            "dimension": dimension,
            "status": "NOT_CHECKED",
            "rejection_code": "COUNTERFACTUAL_RECEIPT_NOT_MATERIALIZED",
            "alternative_binding_id": "",
        }
        for dimension in ("period", "entity_role", "scope", "unit")
    ]


def _blocker_category(reason: object) -> str:
    value = _text(reason)
    parts = value.split(":")
    if len(parts) >= 6 and parts[0].startswith("q") and parts[1] == "stage" and parts[3] == "role":
        value = ":".join(parts[5:])
    return value.split(":", 1)[0] or "UNKNOWN_BLOCKER"


def materialize_authorization_replay(
    *,
    bindings: Path,
    bindings_manifest: Path,
    execution: Path,
    execution_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    semantic_review_queue: Path,
    semantic_review_manifest: Path,
    semantic_human_decisions: Path,
    metric_registry: Path,
    evidence_bindings_output: Path,
    answer_certificates_output: Path,
) -> dict[str, Any]:
    """Materialize full-corpus binding and answer-authority receipts.

    The produced artifacts may be entirely abstaining when V2 lacks semantic
    proof.  That is a verified integration result, not an error to repair by
    guessing headers, periods, variables, scope, or revision data.
    """
    _require_output_sha(bindings_manifest, bindings, output_name="bindings", label="exact bindings")
    _require_output_sha(execution_manifest, execution, output_name="execution", label="Decimal execution")
    _require_evidence_context_lineage(
        evidence_context_manifest,
        evidence_context,
        structured_tables,
    )
    binding_rows = {int(row.get("question_id")): row for row in _rows(bindings)}
    execution_rows = {int(row.get("question_id")): row for row in _rows(execution)}
    if not binding_rows or set(binding_rows) != set(execution_rows):
        raise GroundedAuthorizationError("exact bindings and execution must cover the same questions")
    if len(binding_rows) != len(_rows(bindings)) or len(execution_rows) != len(_rows(execution)):
        raise GroundedAuthorizationError("question_id must be unique in authorization inputs")
    tables = _table_index(structured_tables)
    evidence_contexts = _evidence_context_index(evidence_context)
    human_semantic_approvals = load_human_semantic_approvals(
        queue=semantic_review_queue,
        queue_manifest=semantic_review_manifest,
        decisions=semantic_human_decisions,
        bindings=bindings,
        structured_tables=structured_tables,
        evidence_context=evidence_context,
    )
    semantic_approvals = dict(human_semantic_approvals)
    registry = _registry(metric_registry)
    evidence_rows: list[dict[str, Any]] = []
    certificate_rows: list[dict[str, Any]] = []

    for question_id in sorted(binding_rows):
        question = binding_rows[question_id]
        execution_row = execution_rows[question_id]
        stages = [stage for stage in question.get("stages") or [] if isinstance(stage, Mapping)]
        traces = {
            _text(trace.get("stage_id")): trace
            for trace in execution_row.get("stage_traces") or []
            if isinstance(trace, Mapping)
        }
        materialized_stages: list[
            tuple[Mapping[str, Any], list[Mapping[str, Any]], list[Mapping[str, Any]]]
        ] = []
        for stage_candidate in stages:
            operands = [
                value
                for value in stage_candidate.get("required_operands") or []
                if isinstance(value, Mapping)
            ]
            bindings_for_stage = [
                _build_binding(
                    question=question,
                    stage=stage_candidate,
                    operand=operand,
                    tables=tables,
                    evidence_contexts=evidence_contexts,
                    approval=semantic_approvals.get(
                        (
                            question_id,
                            _text(stage_candidate.get("stage_id")),
                            _text(operand.get("role")),
                        )
                    ),
                )
                for operand in operands
            ]
            materialized_stages.append((stage_candidate, operands, bindings_for_stage))
            for operand, binding in zip(operands, bindings_for_stage, strict=True):
                evidence_rows.append(
                    {
                        "schema_version": GROUNDED_AUTHORIZATION_SCHEMA_VERSION,
                        "protocol": GROUNDED_AUTHORIZATION_PROTOCOL,
                        "question_id": question_id,
                        "stage_id": stage_candidate.get("stage_id"),
                        "role": operand.get("role"),
                        "evidence_binding": binding,
                        "source_contract": dict(AUTHORIZATION_CONTRACT),
                    }
                )
        controlled_graph = question.get("controlled_operation_graph")
        if len(stages) != 1 and isinstance(controlled_graph, Mapping):
            all_operands = [operand for _, operands, _ in materialized_stages for operand in operands]
            all_evidence_bindings = [binding for _, _, bindings in materialized_stages for binding in bindings]
            if not all_operands:
                certificate = compile_abstention_certificate(
                    question_id=question_id,
                    reason_codes=["CONTROLLED_GRAPH_HAS_NO_REQUIRED_OPERANDS"],
                    execution={"status": execution_row.get("execution_status")},
                )
            else:
                plan = _composed_binding_plan(
                    question=question,
                    materialized_stages=materialized_stages,
                )
                certificate = compile_answer_certificate(
                    question_id=question_id,
                    binding_plan=plan,
                    operand_bindings=all_evidence_bindings,
                    execution=_execution_receipt(
                        plan,
                        _mapping(execution_row.get("composition_trace")),
                    ),
                    alternative_checks=_counterfactual_checks(all_evidence_bindings),
                )
            certificate_rows.append(
                {
                    "schema_version": GROUNDED_AUTHORIZATION_SCHEMA_VERSION,
                    "protocol": GROUNDED_AUTHORIZATION_PROTOCOL,
                    "question_id": question_id,
                    "stage_id": controlled_graph.get("final_node_id"),
                    "answer_certificate": certificate,
                    "authorization_status": certificate["status"],
                    "source_contract": dict(AUTHORIZATION_CONTRACT),
                }
            )
            continue
        if len(stages) != 1:
            certificate = compile_abstention_certificate(
                question_id=question_id,
                reason_codes=[
                    "NO_EXECUTABLE_STAGE"
                    if not stages
                    else "MULTI_STAGE_EXECUTION_GRAPH_NOT_MATERIALIZED"
                ],
                execution={"status": execution_row.get("execution_status")},
            )
            certificate_rows.append(
                {
                    "schema_version": GROUNDED_AUTHORIZATION_SCHEMA_VERSION,
                    "protocol": GROUNDED_AUTHORIZATION_PROTOCOL,
                    "question_id": question_id,
                    "stage_id": None,
                    "answer_certificate": certificate,
                    "authorization_status": certificate["status"],
                    "source_contract": dict(AUTHORIZATION_CONTRACT),
                }
            )
            continue

        stage, original_operands, evidence_bindings = materialized_stages[0]
        if not original_operands:
            certificate = compile_abstention_certificate(
                question_id=question_id,
                reason_codes=["STAGE_HAS_NO_REQUIRED_OPERANDS"],
                execution={"status": execution_row.get("execution_status")},
            )
            certificate_rows.append(
                {
                    "schema_version": GROUNDED_AUTHORIZATION_SCHEMA_VERSION,
                    "protocol": GROUNDED_AUTHORIZATION_PROTOCOL,
                    "question_id": question_id,
                    "stage_id": stage.get("stage_id"),
                    "answer_certificate": certificate,
                    "authorization_status": certificate["status"],
                    "source_contract": dict(AUTHORIZATION_CONTRACT),
                }
            )
            continue
        plan = _binding_plan(question=question, stage=stage, bindings=evidence_bindings, registry=registry)
        certificate = compile_answer_certificate(
            question_id=question_id,
            binding_plan=plan,
            operand_bindings=evidence_bindings,
            execution=_execution_receipt(plan, traces.get(_text(stage.get("stage_id")))),
            alternative_checks=_counterfactual_checks(evidence_bindings),
        )
        certificate_rows.append(
            {
                "schema_version": GROUNDED_AUTHORIZATION_SCHEMA_VERSION,
                "protocol": GROUNDED_AUTHORIZATION_PROTOCOL,
                "question_id": question_id,
                "stage_id": stage.get("stage_id"),
                "answer_certificate": certificate,
                "authorization_status": certificate["status"],
                "source_contract": dict(AUTHORIZATION_CONTRACT),
            }
        )

    _write_jsonl(evidence_bindings_output, evidence_rows)
    _write_jsonl(answer_certificates_output, certificate_rows)
    binding_status_counts = dict(
        sorted(Counter(row["evidence_binding"]["binding_status"] for row in evidence_rows).items())
    )
    certificate_status_counts = dict(
        sorted(Counter(row["authorization_status"] for row in certificate_rows).items())
    )
    blocker_reasons = [
        reason
        for row in certificate_rows
        for reason in row["answer_certificate"].get("abstain_reason_codes") or []
    ]
    blocker_counts = dict(
        sorted(
            Counter(_blocker_category(reason) for reason in blocker_reasons).items()
        )
    )
    authorization_ready = bool(certificate_rows) and not blocker_reasons and all(
        status == "BOUND" for status in binding_status_counts
    )
    readiness = {
        "schema_version": 1,
        "protocol": "vifinqa_authorization_readiness_v1",
        "authorization_status": "ready_for_independent_audit" if authorization_ready else "blocked",
        "question_count": len(certificate_rows),
        "evidence_binding_count": len(evidence_rows),
        "binding_status_counts": binding_status_counts,
        "answer_certificate_status_counts": certificate_status_counts,
        "blocker_receipt_count": len(blocker_reasons),
        "blocker_category_counts": blocker_counts,
        "human_semantic_approval_count": len(human_semantic_approvals),
        "effective_semantic_approval_count": len(semantic_approvals),
        "chatgpt_semantic_correction_count": 0,
        "chatgpt_navigation_promotion_count": 0,
        "chatgpt_cross_entity_promotion_count": 0,
        "independent_audit_required": True,
        "release_gate_required": True,
        "release_authorized": False,
        "submission_compilation_allowed": False,
        "source_contract": dict(AUTHORIZATION_CONTRACT),
    }
    readiness_path = answer_certificates_output.with_name("authorization_readiness_v1.json")
    _write_json(readiness_path, readiness)
    manifest = {
        "schema_version": GROUNDED_AUTHORIZATION_SCHEMA_VERSION,
        "protocol": GROUNDED_AUTHORIZATION_PROTOCOL,
        "inputs": {
            "bindings": {"path": str(bindings), "sha256": sha256_file(bindings)},
            "bindings_manifest": {"path": str(bindings_manifest), "sha256": sha256_file(bindings_manifest)},
            "execution": {"path": str(execution), "sha256": sha256_file(execution)},
            "execution_manifest": {"path": str(execution_manifest), "sha256": sha256_file(execution_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "evidence_context": {"path": str(evidence_context), "sha256": sha256_file(evidence_context)},
            "evidence_context_manifest": {
                "path": str(evidence_context_manifest),
                "sha256": sha256_file(evidence_context_manifest),
            },
            "semantic_review_queue": {
                "path": str(semantic_review_queue),
                "sha256": sha256_file(semantic_review_queue),
            },
            "semantic_review_manifest": {
                "path": str(semantic_review_manifest),
                "sha256": sha256_file(semantic_review_manifest),
            },
            "semantic_human_decisions": {
                "path": str(semantic_human_decisions),
                "sha256": sha256_file(semantic_human_decisions),
            },
            "metric_registry": {"path": str(metric_registry), "sha256": sha256_file(metric_registry)},
        },
        "outputs": {
            "evidence_bindings": {"path": str(evidence_bindings_output), "sha256": sha256_file(evidence_bindings_output)},
            "answer_certificates": {"path": str(answer_certificates_output), "sha256": sha256_file(answer_certificates_output)},
            "authorization_readiness": {"path": str(readiness_path), "sha256": sha256_file(readiness_path)},
        },
        "counts": {
            "question_count": len(certificate_rows),
            "evidence_binding_count": len(evidence_rows),
            "evidence_binding_status_counts": binding_status_counts,
            "answer_certificate_status_counts": certificate_status_counts,
            "field_status_counts": {
                field: dict(
                    sorted(
                        Counter(
                            row["evidence_binding"]["field_statuses"][f"{field}_status"]
                            for row in evidence_rows
                        ).items()
                    )
                )
                for field in BINDING_FIELDS
            },
            "period_recognition_method_counts": dict(
                sorted(
                    Counter(
                        _text(row["evidence_binding"]["period_binding"].get("recognition_method"))
                        or "unresolved"
                        for row in evidence_rows
                    ).items()
                )
            ),
            "human_semantic_approval_count": len(human_semantic_approvals),
            "effective_semantic_approval_count": len(semantic_approvals),
            "chatgpt_semantic_correction_count": 0,
            "chatgpt_navigation_promotion_count": 0,
            "chatgpt_cross_entity_promotion_count": 0,
        },
        "source_contract": dict(AUTHORIZATION_CONTRACT),
    }
    manifest_path = answer_certificates_output.with_suffix(".manifest.json")
    _write_json(manifest_path, manifest)
    return {
        **manifest,
        "manifest_path": str(manifest_path),
        "readiness_path": str(readiness_path),
    }
