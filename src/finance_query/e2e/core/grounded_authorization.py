"""Fail-closed authorization receipts for the full-corpus grounded replay.

The legacy V2 replay proves exact cells and Decimal arithmetic.  It does not
prove that a candidate cell has the requested variable, period, entity, scope
or revision semantics.  This adapter makes that gap executable: it converts
only source-preserved V2 facts into typed Evidence Bindings and emits an
Answer Certificate or ``ABSTAIN`` for every question.  It never fills a
missing semantic field from a route score, registry label, filename, model, or
reviewer decision.
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
    NOT_APPLICABLE,
    PASS,
    UNRESOLVED,
    EVIDENCE_BINDING_SCHEMA_VERSION,
    build_evidence_binding,
)
from .exact_cell_bindings_v2 import resolve_source_unit


GROUNDED_AUTHORIZATION_PROTOCOL = "vifinqa_grounded_authorization_replay_v1"
GROUNDED_AUTHORIZATION_SCHEMA_VERSION = 1
AUTHORIZATION_CONTRACT = {
    # The public answer channel may carry a best-effort prediction, but that
    # lane is deliberately not an EvidenceBinding or strict-answer authority.
    "answer_output_allowed": True,
    "answer_requires_complete_certificate": False,
    "authoritative_answer_requires_complete_certificate": True,
    "answer_requires_surviving_candidate": True,
    "may_materialize_answer": True,
    "best_effort_candidate_lane": True,
    "best_effort_candidate_authority": False,
    "evidence_binding_authority": False,
    "strict_answer_authority": "complete_answer_certificate_and_source_aligned_binding",
    "strict_answer_requires_complete_certificate": True,
    "strict_answer_authorized_by_candidate": False,
    "evidence_eligible": False,
    "model_metadata_authority": False,
    "reviewer_metadata_authority": False,
    "research_value_authority": False,
    "gold_data_used": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "release_authorized": False,
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


def _load_best_candidate_predictions(
    path: Path | None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Load selector output used only for best-effort answer serving.

    The file is an explicit input, not an authority shortcut.  Each retained
    row still carries its candidate/filter metadata, while the answer
    certificate validates that the candidate is finite and not rejected.
    """
    if path is None:
        return {}, {"path": None, "loaded": False, "candidate_count": 0}
    candidates: dict[int, dict[str, Any]] = {}
    skipped = 0
    for row in _rows(path):
        try:
            question_id = int(row.get("question_id", row.get("id")))
        except (TypeError, ValueError) as exc:
            raise GroundedAuthorizationError(
                "best candidate predictions require an integer question_id"
            ) from exc
        if question_id in candidates:
            raise GroundedAuthorizationError(
                f"duplicate best candidate prediction for question {question_id}"
            )
        if row.get("answer") is None and row.get("answer_decimal") is None and row.get("value") is None:
            skipped += 1
            continue
        candidates[question_id] = dict(row)
    return candidates, {
        "path": str(path),
        "sha256": sha256_file(path),
        "loaded": True,
        "candidate_count": len(candidates),
        "skipped_without_numeric_answer": skipped,
        "navigation_only": False,
        "answer_authority": "best_effort_candidate_only",
    }


def _execution_candidate(
    *,
    question_id: int,
    stage_id: object,
    trace: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Convert a replay-ready execution trace into a fallback candidate."""
    if not isinstance(trace, Mapping):
        return None
    if _text(trace.get("status")) not in {"PASS", "execution_replay_ready"}:
        return None
    value = trace.get("converted_output_decimal")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not decimal_value.is_finite():
        return None
    return {
        "question_id": question_id,
        "candidate_id": f"q{question_id}:stage:{_text(stage_id)}:execution",
        "answer_decimal": format(decimal_value, "f"),
        "filter_status": "SURVIVED_FILTER",
        "filter_passed": True,
        "filter_score": 1.0,
        "selection_method": "best_surviving_exact_execution_candidate",
        "source": trace.get("operand_sources") or trace.get("stage_id"),
        "stage_id": stage_id,
    }


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


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_LINEAGE_FEEDBACK_PROTOCOL = "vifinqa_source_lineage_feedback_v1"
_SOURCE_LINEAGE_REQUIRED_FIELDS = [
    "canonical_v2.internal_table_uid",
    "canonical_v2.document_id",
    "canonical_v2.source_provenance.source_sha256",
    "canonical_v2.source_provenance.table_sha256",
    "canonical_v2.exact_cell_coordinates",
    "canonical_v3.internal_table_uid",
    "canonical_v3.document_id",
    "canonical_v3.source_provenance.source_sha256",
    "canonical_v3.source_provenance.table_sha256",
    "canonical_v3.grid.provenance_complete",
    "canonical_v3.quality.status",
]


def _valid_sha256(value: object) -> bool:
    return bool(_SHA256_RE.fullmatch(_text(value)))


def _source_lineage_feedback(
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Explain source-closure state without granting evidence authority.

    The canonical authorizer receives V2 tables and their V3 context sidecar;
    it does not receive the full-corpus asset as an independently comparable
    input.  This receipt therefore distinguishes a missing V2 table, a
    V2/V3 identity or hash mismatch, and an exact V2/V3 link.  It deliberately
    does not claim that a hash was recomputed from the original source file,
    and it can never authorize an answer.
    """

    table_provenance = _mapping(table.get("source_provenance")) if table is not None else {}
    context_provenance = _mapping(context.get("source_provenance")) if context is not None else {}
    table_uid = _text(table.get("internal_table_uid")) if table is not None else ""
    context_uid = _text(context.get("internal_table_uid")) if context is not None else ""
    table_document = _text(table.get("document_id")) if table is not None else ""
    context_document = _text(context.get("document_id")) if context is not None else ""
    table_source_sha = _text(table_provenance.get("source_sha256"))
    context_source_sha = _text(context_provenance.get("source_sha256"))
    table_table_sha = _text(table_provenance.get("table_sha256"))
    context_table_sha = _text(context_provenance.get("table_sha256"))
    reasons: list[str] = []

    if table is None:
        reasons.append("SOURCE_CLOSURE_CANONICAL_V2_TABLE_MISSING")
    else:
        if not table_uid:
            reasons.append("SOURCE_CLOSURE_CANONICAL_V2_TABLE_UID_MISSING")
        if not table_document:
            reasons.append("SOURCE_CLOSURE_CANONICAL_V2_DOCUMENT_UID_MISSING")
        if not isinstance(table.get("source_provenance"), Mapping):
            reasons.append("SOURCE_CLOSURE_CANONICAL_V2_PROVENANCE_SCHEMA_MISSING")
        if not _valid_sha256(table_source_sha):
            reasons.append("BINDING_LINEAGE_DOCUMENT_SHA256_INVALID")
        if not _valid_sha256(table_table_sha):
            reasons.append("BINDING_LINEAGE_TABLE_SHA256_INVALID")

    if context is None:
        reasons.append("SOURCE_CLOSURE_CANONICAL_V3_CONTEXT_MISSING")
    else:
        if not context_uid:
            reasons.append("SOURCE_CLOSURE_CANONICAL_V3_TABLE_UID_MISSING")
        if not context_document:
            reasons.append("SOURCE_CLOSURE_CANONICAL_V3_DOCUMENT_UID_MISSING")
        if not _valid_sha256(context_source_sha):
            reasons.append("SOURCE_CLOSURE_V3_DOCUMENT_SHA256_INVALID")
        if not _valid_sha256(context_table_sha):
            reasons.append("SOURCE_CLOSURE_V3_TABLE_SHA256_INVALID")
        grid = _mapping(context.get("grid"))
        if grid.get("rectangular") is not True or grid.get("provenance_complete") is not True:
            reasons.append("SOURCE_CLOSURE_V3_GRID_PROVENANCE_INCOMPLETE")
        if _text(_mapping(context.get("quality")).get("status")) != "review_ready":
            reasons.append("SOURCE_CLOSURE_V3_CONTEXT_NOT_REVIEW_READY")

    if table is not None and context is not None:
        if table_uid and context_uid and table_uid != context_uid:
            reasons.append("SOURCE_CLOSURE_INTERNAL_TABLE_UID_MISMATCH")
        if table_document and context_document and table_document != context_document:
            reasons.append("SOURCE_CLOSURE_DOCUMENT_UID_MISMATCH")
        if _valid_sha256(table_source_sha) and _valid_sha256(context_source_sha) and table_source_sha != context_source_sha:
            reasons.append("SOURCE_CLOSURE_DOCUMENT_SHA256_MISMATCH")
        if _valid_sha256(table_table_sha) and _valid_sha256(context_table_sha) and table_table_sha != context_table_sha:
            reasons.append("SOURCE_CLOSURE_TABLE_SHA256_MISMATCH")

    status = "PASS" if not reasons else "BLOCKED"
    return {
        "schema_version": 1,
        "protocol": _SOURCE_LINEAGE_FEEDBACK_PROTOCOL,
        "status": status,
        "may_authorize": False,
        "diagnostic_only": True,
        "comparison_scope": "canonical_v2_structured_table_to_canonical_v3_context",
        "external_full_table_assets_comparison": "NOT_AVAILABLE_IN_AUTHORIZATION_INPUT",
        "raw_source_hash_recomputed": False,
        "checks": {
            "v2_table_present": table is not None,
            "v2_table_uid_present": bool(table_uid),
            "v2_document_uid_present": bool(table_document),
            "v2_provenance_schema_present": isinstance(table.get("source_provenance"), Mapping)
            if table is not None
            else False,
            "v2_document_sha256_valid": _valid_sha256(table_source_sha),
            "v2_table_sha256_valid": _valid_sha256(table_table_sha),
            "v3_context_present": context is not None,
            "v3_table_uid_present": bool(context_uid),
            "v3_document_uid_present": bool(context_document),
            "v3_document_sha256_valid": _valid_sha256(context_source_sha),
            "v3_table_sha256_valid": _valid_sha256(context_table_sha),
            "v2_v3_table_uid_equal": bool(table_uid and context_uid and table_uid == context_uid),
            "v2_v3_document_uid_equal": bool(
                table_document and context_document and table_document == context_document
            ),
            "v2_v3_document_sha256_equal": bool(
                _valid_sha256(table_source_sha)
                and _valid_sha256(context_source_sha)
                and table_source_sha == context_source_sha
            ),
            "v2_v3_table_sha256_equal": bool(
                _valid_sha256(table_table_sha)
                and _valid_sha256(context_table_sha)
                and table_table_sha == context_table_sha
            ),
            "v3_grid_provenance_complete": bool(
                context is not None
                and _mapping(context.get("grid")).get("rectangular") is True
                and _mapping(context.get("grid")).get("provenance_complete") is True
            ),
            "v3_context_review_ready": bool(
                context is not None
                and _text(_mapping(context.get("quality")).get("status")) == "review_ready"
            ),
        },
        "reason_codes": sorted(set(reasons)),
        "required_fields": list(_SOURCE_LINEAGE_REQUIRED_FIELDS) if reasons else [],
    }


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
    return _source_lineage_feedback(table, context)["status"] == "PASS"


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

    period_resolution_method = _text(operand.get("period_resolution_method"))
    if period_resolution_method == "v2_exact_source_title_current_header_v1":
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
            and re.search(r"(?:cho|trong)\s+nam(?:\s+tai\s+chinh)?(?:\s+(?:19|20)\d{2})?\s+ket\s+thuc\s+(?:vao|tai)?\s+ngay", folded_title)
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

    if period_resolution_method == "v2_exact_header_period_v1":
        # An explicitly dated header is a separate, stricter path from the
        # legacy ``Năm nay``/``Số cuối năm`` recovery.  The header itself must
        # contain exactly one requested year; a filename or route label cannot
        # supply it.  For an instant balance we additionally require a full
        # as-of date either in that header or in the source title.  For a flow
        # statement the same date is used as the fiscal-year end.
        period_labels = [_text(value) for value in header.get("period_labels") or [] if _text(value)]
        header_values = [raw_header, source_label, *period_labels]
        header_years = {
            int(match.group(0))
            for value in header_values
            for match in re.finditer(r"(?<!\d)(?:19|20)\d{2}(?!\d)", _fold(value))
        }
        requested_years = {
            int(match.group(1))
            for value in (_text(value) for value in operand.get("period_labels") or [])
            if (match := re.fullmatch(r"(?:nam\s+)?(20\d{2})", _fold(value))) is not None
        }
        if len(header_years) != 1 or len(requested_years) != 1 or header_years != requested_years:
            return {"status": UNRESOLVED, "reason_codes": ["EXPLICIT_HEADER_YEAR_NOT_UNIQUE"]}
        requested_year = next(iter(requested_years))
        expected_title_hash = _text(operand.get("period_source_title_sha256"))
        if expected_title_hash and expected_title_hash != hashlib.sha256(source_title.encode("utf-8")).hexdigest():
            return {"status": UNRESOLVED, "reason_codes": ["PERIOD_SOURCE_TITLE_HASH_MISMATCH"]}
        if document_anchor is None:
            return {"status": UNRESOLVED, "reason_codes": ["EXPLICIT_HEADER_DOCUMENT_ANCHOR_MISSING"]}
        header_dates = [value for value in _dates_in_text(raw_header or source_label) if value.year == requested_year]
        title_dates = [value for value in _dates_in_text(source_title) if value.year == requested_year]
        dates = header_dates or title_dates
        if len(dates) != 1:
            return {"status": UNRESOLVED, "reason_codes": ["EXPLICIT_HEADER_DATE_NOT_UNIQUE"]}
        period_date = dates[0]
        if table_function == "balance_sheet":
            return {
                "status": PASS,
                "raw_period_label": raw_header or source_label,
                "period_grain": "instant",
                "flow_or_stock": "stock",
                "comparative_basis": "closing_balance" if (period_date.month, period_date.day) == (12, 31) else "current",
                "period_years": [requested_year],
                "as_of_date": period_date.isoformat(),
                "source_anchors": [*anchors, document_anchor],
                "recognition_method": "v3_exact_header_explicit_balance_date_v1",
            }
        if table_function in {"income_statement", "cash_flow_statement"}:
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
                "recognition_method": "v3_exact_header_explicit_duration_v1",
            }
        return {"status": UNRESOLVED, "reason_codes": ["V3_TABLE_FUNCTION_NOT_PERIOD_AUTHORIZING"]}

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
    source_lineage_feedback: Mapping[str, Any] | None = None,
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
        "source_lineage_feedback": dict(source_lineage_feedback or {}),
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
    source_lineage_feedback = _source_lineage_feedback(table, evidence_context)
    source_integrity = _source_integrity(table, operand)
    unit_binding = _unit_binding(table, operand, evidence_context)
    period_binding = _period_binding(table, operand, evidence_context)
    variable_binding = {
        "status": UNRESOLVED,
        "reason_codes": ["MACHINE_VARIABLE_SOURCE_PROOF_NOT_AVAILABLE"],
    }
    entity_scope_binding = _entity_scope_binding(
        table,
        evidence_context,
        requested_entity=entity,
        requested_scope=scope,
    )
    if requested_entity_role:
        entity_role_binding = {
            "status": UNRESOLVED,
            "role": requested_entity_role,
            "recognition_method": "explicit_question_role_requires_machine_source_provenance_v1",
            "source_anchors": [],
            "reason_codes": ["ENTITY_ROLE_MACHINE_SOURCE_PROVENANCE_MISSING"],
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
            source_lineage_feedback=source_lineage_feedback,
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
    """Build a typed, narrowly controlled cross-stage composition plan."""

    graph = _mapping(question.get("controlled_operation_graph"))
    stage_order = graph.get("stage_order")
    source_ast = graph.get("operation_ast")
    composition_operator = _text(_mapping(source_ast).get("op"))
    composition_mode = _text(graph.get("composition_mode")) or "cross_entity_v1"
    selector_mode = composition_mode == "same_entity_multi_period_argmax_v1"
    valid_arity = (
        (composition_operator == "subtract" and isinstance(stage_order, list) and len(stage_order) == 2)
        or (composition_operator == "mean" and isinstance(stage_order, list) and len(stage_order) >= 2)
        or (
            composition_operator == "arg_extreme_period"
            and isinstance(stage_order, list)
            and len(stage_order) >= 2
            and _text(_mapping(source_ast).get("direction")) == "max"
        )
    )
    expected_source_ast = (
        {"op": "arg_extreme_period", "direction": "max", "args": stage_order}
        if selector_mode
        else {"op": composition_operator, "args": stage_order}
    )
    if (
        graph.get("protocol") != "vifinqa_controlled_composition_graph_v1"
        or not isinstance(stage_order, list)
        or not valid_arity
        or len({_text(value) for value in stage_order}) != len(stage_order)
        or source_ast != expected_source_ast
        or composition_mode not in {
            "cross_entity_v1",
            "temporal_same_entity_two_period_subtract_v1",
            "single_period_same_entity_subtract_v1",
            "same_entity_multi_period_argmax_v1",
        }
        or (
            composition_mode
            in {
                "temporal_same_entity_two_period_subtract_v1",
                "single_period_same_entity_subtract_v1",
            }
            and composition_operator != "subtract"
        )
        or (selector_mode and composition_operator != "arg_extreme_period")
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
    stage_years_by_id: dict[str, list[int]] = {}
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
        stage_years = [
            int(value)
            for value in original.get("period_labels") or []
            if isinstance(value, (int, str)) and str(value).isdigit()
        ]
        stage_years_by_id[stage_id] = stage_years
        operand_years = (
            stage_years
            if composition_mode in {
                "temporal_same_entity_two_period_subtract_v1",
                "same_entity_multi_period_argmax_v1",
            }
            else years
        )
        plan_operands.append(
            {
                "operand_id": operand_id,
                "role": original.get("role"),
                "stage_id": stage_id,
                "expected_variable_id": original.get("concept_id") or original.get("role"),
                "required": True,
                "temporal_contract": temporal_contract_for_operand(
                    years=operand_years,
                    scope=context.get("scope"),
                    # A period selector returns a year, but each exact leaf is
                    # still a monetary source value.  Its unit contract must
                    # therefore validate the internal VND-normalized operand,
                    # not attempt a fictitious VND-to-year conversion.
                    requested_unit=("VND" if selector_mode else request.get("unit")),
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
    requested_entities = [_text(value) for value in context.get("entities") or []]
    if composition_mode == "cross_entity_v1":
        if not all(bound_entities) or len(set(bound_entities)) != len(bound_entities):
            raise GroundedAuthorizationError("cross-entity composition requires distinct proven entities")
    else:
        if len(set(requested_entities)) != 1:
            raise GroundedAuthorizationError("same-entity composition requires exactly one requested entity")
        if composition_mode == "single_period_same_entity_subtract_v1" and len(years) != 1:
            raise GroundedAuthorizationError("single-period composition requires exactly one requested year")
        observed_entities = {value for value in bound_entities if value}
        if observed_entities and observed_entities != set(requested_entities):
            raise GroundedAuthorizationError("same-entity composition entity does not match the requested entity")
    if selector_mode:
        expected_years = sorted(
            {int(value) for value in years if isinstance(value, (int, str)) and str(value).isdigit()}
        )
        observed_periods = {
            stage_id: values[0]
            for stage_id, values in stage_years_by_id.items()
            if len(values) == 1
        }
        if (
            len(expected_years) != len(stage_order)
            or sorted(observed_periods.values()) != expected_years
            or len(set(observed_periods.values())) != len(observed_periods)
            or graph.get("stage_periods") != observed_periods
        ):
            raise GroundedAuthorizationError("period selector graph does not prove one requested period per stage")
    operation_ast = _replace_formula_roles(source_ast, binding_ids_by_stage)
    if graph.get("binding_operation_ast") != operation_ast:
        raise GroundedAuthorizationError("controlled graph binding operation AST mismatch")
    same_fields = (
        (
            "entity_role",
            "scope",
            "period_years",
            "period_grain",
            "flow_or_stock",
            "comparative_basis",
            "normalized_currency",
            "semantic_unit",
            "revision_policy",
        )
        if composition_mode == "cross_entity_v1"
        else (
            "entity",
            "entity_role",
            "scope",
            "period_grain",
            "flow_or_stock",
            "comparative_basis",
            "normalized_currency",
            "semantic_unit",
            "revision_policy",
        )
        if composition_mode in {
            "temporal_same_entity_two_period_subtract_v1",
            "same_entity_multi_period_argmax_v1",
        }
        else (
            "entity",
            "entity_role",
            "scope",
            "period_years",
            "period_grain",
            "flow_or_stock",
            "comparative_basis",
            "normalized_currency",
            "semantic_unit",
            "revision_policy",
        )
    )
    return {
        "operands": plan_operands,
        "operation_ast": operation_ast,
        "formula_compatibility": {
            "formula_id": (
                f"controlled_cross_entity_{composition_operator}"
                if composition_mode == "cross_entity_v1"
                else "controlled_temporal_same_entity_subtract"
                if composition_mode == "temporal_same_entity_two_period_subtract_v1"
                else "controlled_single_period_same_entity_subtract"
                if composition_mode == "single_period_same_entity_subtract_v1"
                else "controlled_same_entity_multi_period_argmax"
            ),
            "contract_source": "controlled_typed_composition_graph_v1",
            "operand_constraints": constraints,
            "cross_operand_rules": [
                {"kind": "same", "field": field, "operands": operand_ids}
                for field in same_fields
            ],
            "different_entity_required": composition_mode == "cross_entity_v1",
            "same_entity_required": composition_mode in {
                "temporal_same_entity_two_period_subtract_v1",
                "single_period_same_entity_subtract_v1",
                "same_entity_multi_period_argmax_v1",
            },
        },
        "composition_lineage": {
            "promotion_packet_sha256": graph.get("promotion_packet_sha256"),
            "promotion_decision_sha256": graph.get("promotion_decision_sha256"),
            "numeric_values_selected_by_reviewer": False,
            "composition_mode": composition_mode,
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


def _source_lineage_feedback_summary(
    evidence_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize diagnostic source-closure receipts without changing gates."""

    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for row in evidence_rows:
        binding = _mapping(row.get("evidence_binding"))
        feedback = _mapping(_mapping(binding.get("binding_lineage")).get("source_lineage_feedback"))
        status_counts[_text(feedback.get("status")) or "MISSING"] += 1
        for reason in feedback.get("reason_codes") or []:
            reason_counts[_text(reason)] += 1
    return {
        "protocol": _SOURCE_LINEAGE_FEEDBACK_PROTOCOL,
        "diagnostic_only": True,
        "may_authorize": False,
        "comparison_scope": "canonical_v2_structured_table_to_canonical_v3_context",
        "external_full_table_assets_comparison": "NOT_AVAILABLE_IN_AUTHORIZATION_INPUT",
        "raw_source_hash_recomputed": False,
        "status_counts": dict(sorted(status_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
    }


def _strict_answer_view(certificate: Mapping[str, Any]) -> dict[str, Any]:
    """Separate strict answer fields from the optional candidate channel.

    ``answer_certificates`` may be compiled by the lower-level certificate
    helper with a historical ``answer`` field for a selector-approved
    best-effort candidate.  The canonical authorizer removes that ambiguity:
    both ``answer`` and the strict ``answer_decimal`` are populated only for a
    complete certificate and are always ``None`` for ``ABSTAIN``.  The
    candidate value remains available only under
    ``best_effort_candidate_decimal`` without receiving evidence, release,
    training, promotion or submission authority.  Candidate metadata is
    redacted from the canonical certificate so no downstream consumer can
    mistake a copied candidate object for an authorized evidence binding.
    """

    payload = {
        key: value
        for key, value in dict(certificate).items()
        if key != "answer_certificate_id"
    }
    strict_authorized = bool(
        payload.get("status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
        and payload.get("answer_authorized") is True
    )
    candidate = _mapping(payload.get("candidate_prediction"))
    candidate_decimal = _text(candidate.get("answer_decimal")) or None
    strict_answer_decimal = _text(payload.get("answer")) or None if strict_authorized else None
    # Do not leave a numeric legacy ``answer`` field in an ABSTAIN certificate:
    # a downstream consumer must opt into the explicitly named best-effort
    # lane rather than accidentally treating a prediction as authorized.
    payload["answer"] = strict_answer_decimal
    payload["answer_decimal"] = strict_answer_decimal
    payload["strict_answer_decimal"] = strict_answer_decimal
    payload["best_effort_candidate_decimal"] = (
        candidate_decimal if not strict_authorized else None
    )
    # Numeric candidate values must not be duplicated in historical nested
    # fields.  The only canonical numeric location for the best-effort lane is
    # ``best_effort_candidate_decimal`` above.
    payload["candidate_prediction"] = None
    execution_receipt = dict(_mapping(payload.get("execution_receipt")))
    if not strict_authorized:
        execution_receipt["answer_decimal"] = None
        execution_receipt["candidate_answer_decimal"] = None
    payload["execution_receipt"] = execution_receipt
    payload["best_effort_candidate_available"] = bool(candidate)
    payload["best_effort_candidate_authority"] = False
    payload["strict_answer_status"] = "AUTHORIZED" if strict_authorized else "ABSTAIN"
    payload["strict_answer_authorized"] = strict_authorized
    payload["strict_answer_available"] = strict_authorized
    payload["answer_channel"] = (
        "STRICT_AUTHORIZED"
        if strict_authorized
        else "BEST_EFFORT_CANDIDATE"
        if candidate
        else "ABSTAIN"
    )
    payload["answer_authorized"] = strict_authorized
    payload["release_authorized"] = False
    payload["submission_eligible"] = False
    payload["training_eligible"] = False
    payload["promotion_allowed"] = False
    return {"answer_certificate_id": _sha_json(payload), **payload}


def materialize_authorization_replay(
    *,
    bindings: Path,
    bindings_manifest: Path,
    execution: Path,
    execution_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    metric_registry: Path,
    evidence_bindings_output: Path,
    answer_certificates_output: Path,
    best_candidate_predictions: Path | None = None,
) -> dict[str, Any]:
    """Materialize full-corpus binding and answer-authority receipts.

    The produced artifacts may still be authorization-abstaining when V2 lacks
    semantic proof.  If an explicit selector output is supplied, the best
    surviving numeric candidate is retained for serving while the authorization
    status remains ``ABSTAIN``.  No missing field is repaired by guessing
    headers, periods, variables, scope, or revision data.
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
    registry = _registry(metric_registry)
    best_candidates, best_candidate_stats = _load_best_candidate_predictions(
        best_candidate_predictions
    )
    evidence_rows: list[dict[str, Any]] = []
    certificate_rows: list[dict[str, Any]] = []

    for question_id in sorted(binding_rows):
        question = binding_rows[question_id]
        execution_row = execution_rows[question_id]
        external_candidate = best_candidates.get(question_id)
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
                    best_candidate=external_candidate,
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
                    best_candidate=(
                        _execution_candidate(
                            question_id=question_id,
                            stage_id=controlled_graph.get("final_node_id"),
                            trace=_mapping(execution_row.get("composition_trace")),
                        )
                        or external_candidate
                    ),
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
                best_candidate=external_candidate,
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
                best_candidate=external_candidate,
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
            best_candidate=(
                _execution_candidate(
                    question_id=question_id,
                    stage_id=stage.get("stage_id"),
                    trace=traces.get(_text(stage.get("stage_id"))),
                )
                or external_candidate
            ),
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

    certificate_rows = [
        {
            **row,
            "answer_certificate": _strict_answer_view(row["answer_certificate"]),
        }
        for row in certificate_rows
    ]
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
    source_lineage_feedback = _source_lineage_feedback_summary(evidence_rows)
    candidate_prediction_count = sum(
        1
        for row in certificate_rows
        if row["answer_certificate"].get("answer_status") == "PREDICTED_CANDIDATE"
    )
    answer_available_count = sum(
        1
        for row in certificate_rows
        if row["answer_certificate"].get("answer_available") is True
    )
    no_prediction_abstain_count = sum(
        1
        for row in certificate_rows
        if row["answer_certificate"].get("answer_status") == "ABSTAIN"
    )
    authorization_ready = bool(certificate_rows) and not blocker_reasons and all(
        status == "BOUND" for status in binding_status_counts
    )
    readiness = {
        "schema_version": 1,
        "protocol": "vifinqa_authorization_readiness_v1",
        "authorization_status": "ready_for_independent_audit" if authorization_ready else "blocked",
        "answer_output_allowed": True,
        "answer_count": certificate_status_counts.get(
            "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY", 0
        ),
        "abstain_count": certificate_status_counts.get("ABSTAIN", 0),
        "candidate_prediction_count": candidate_prediction_count,
        "answer_available_count": answer_available_count,
        "no_prediction_abstain_count": no_prediction_abstain_count,
        "question_count": len(certificate_rows),
        "evidence_binding_count": len(evidence_rows),
        "binding_status_counts": binding_status_counts,
        "answer_certificate_status_counts": certificate_status_counts,
        "blocker_receipt_count": len(blocker_reasons),
        "blocker_category_counts": blocker_counts,
        "source_lineage_feedback": source_lineage_feedback,
        "reviewer_inputs_used": [],
        "machine_semantic_binding_count": 0,
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
            "candidate_prediction_count": candidate_prediction_count,
            "answer_available_count": answer_available_count,
            "no_prediction_abstain_count": no_prediction_abstain_count,
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
            "source_lineage_feedback": source_lineage_feedback,
            "reviewer_inputs_used": [],
            "machine_semantic_binding_count": 0,
        },
        "source_contract": dict(AUTHORIZATION_CONTRACT),
    }
    if best_candidate_predictions is not None:
        manifest["inputs"]["best_candidate_predictions"] = best_candidate_stats
    manifest_path = answer_certificates_output.with_suffix(".manifest.json")
    _write_json(manifest_path, manifest)
    return {
        **manifest,
        "manifest_path": str(manifest_path),
        "readiness_path": str(readiness_path),
    }
