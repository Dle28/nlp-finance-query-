"""Materialize source-only table-structure context for CCL Phase 5.

This is the bridge between a literal report-context route and a future bounded
semantic task.  It exposes only immutable source facts: document identity,
raw-grid shape, source header cells and data-row labels.  It never labels a
table's meaning, repairs OCR, or enables model dispatch.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from finance_query.table_structure import sha256_file

from .phase4_row_label_context import PHASE4_ROW_LABEL_CONTEXT_PROTOCOL
from .phase5_note_context import PHASE5_NOTE_CONTEXT_PROTOCOL
from .phase5_semantic_routing import PHASE5_SEMANTIC_ROUTING_PROTOCOL
from .pipeline import CertifiedCanonicalError


PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL = "vifinqa_ccl_phase5_table_structure_context_v1"
PHASE5_TABLE_STRUCTURE_CONTEXT_SCHEMA_VERSION = 1
_CONTEXT_NAME = "phase5_table_structure_context_v1.jsonl"
_REPORT_NAME = "phase5_table_structure_context_report.json"
_MANIFEST_NAME = "phase5_table_structure_context_manifest.json"
_OUTPUT_NAMES = (_CONTEXT_NAME, _REPORT_NAME, _MANIFEST_NAME)
_ROUTE_STATUSES = frozenset(
    {
        "DETERMINISTIC_SOURCE_PROFILE_CONTEXT_ONLY",
        "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED",
        "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY",
    }
)


@dataclass(frozen=True, slots=True)
class Phase5TableStructureContextResult:
    output_dir: Path
    context_count: int
    materialized_count: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertifiedCanonicalError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise CertifiedCanonicalError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise CertifiedCanonicalError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"expected JSONL object: {path}:{line_number}")
            rows.append(value)
    return rows


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CertifiedCanonicalError(f"SHA-256 mismatch: {label}")


def _require_new_output(output_dir: Path, inputs: Iterable[Path]) -> None:
    if output_dir.resolve() in {path.resolve() for path in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be a Phase 5 source-structure input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; Phase 5 source-structure output is immutable")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise CertifiedCanonicalError(f"{label} violates the non-promotable contract")


def _require_manifest(manifest: Mapping[str, Any], *, protocol: str, run_status: str, label: str) -> None:
    if (
        manifest.get("protocol") != protocol
        or manifest.get("run_status") != run_status
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError(f"{label} manifest is unsupported or promotable")


def _index_unique(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in result:
            raise CertifiedCanonicalError(f"{label} has a missing or duplicate {key}")
        result[value] = row
    return result


def _select_records(path: Path, requested_uids: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise CertifiedCanonicalError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(row, dict):
                raise CertifiedCanonicalError(f"expected JSONL object: {path}:{line_number}")
            uid = str(row.get("internal_table_uid") or "")
            if uid not in requested_uids:
                continue
            if uid in selected:
                raise CertifiedCanonicalError(f"duplicate source record for table UID: {uid}")
            selected[uid] = row
            if len(selected) == len(requested_uids):
                break
    missing = sorted(requested_uids - set(selected))
    if missing:
        raise CertifiedCanonicalError(f"missing source record(s) for table UIDs: {', '.join(missing[:3])}")
    return selected


def _identity_matches(route: Mapping[str, Any], context: Mapping[str, Any], *, label: str) -> None:
    if context.get("phase45_assertion_id") != route.get("phase45_assertion_id"):
        raise CertifiedCanonicalError(f"{label} does not bind the source-first route assertion")
    for field in ("phase3_request_id", "internal_table_uid", "route_id"):
        if not route.get(field) or context.get(field) != route.get(field):
            raise CertifiedCanonicalError(f"{label} and source-first route have mismatched {field}")
    _require_non_promotable(context, label=label)


def _source_header_cells(
    *, raw_by_uid: Mapping[str, Mapping[str, Any]], current_table_uid: str, column: Mapping[str, Any]
) -> list[dict[str, Any]]:
    sources = column.get("header_source_cells")
    if not isinstance(sources, list):
        raise CertifiedCanonicalError("canonical column lacks source header-cell provenance")
    source_table_uid = str(column.get("header_source_table_uid") or current_table_uid)
    source_table = raw_by_uid.get(source_table_uid)
    source_rows = source_table.get("rows") if isinstance(source_table, Mapping) else None
    if not isinstance(source_rows, list) or not source_rows:
        raise CertifiedCanonicalError("canonical column header provenance references an unknown source table")
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            raise CertifiedCanonicalError("canonical column header provenance is invalid")
        row_index, column_index = source.get("row_index"), source.get("column_index")
        if (
            not isinstance(row_index, int)
            or isinstance(row_index, bool)
            or not isinstance(column_index, int)
            or isinstance(column_index, bool)
            or row_index < 0
            or row_index >= len(source_rows)
            or column_index < 0
            or column_index >= len(source_rows[row_index])
            or (row_index, column_index) in seen
        ):
            raise CertifiedCanonicalError("canonical column has invalid source header-cell coordinates")
        literal = str(source_rows[row_index][column_index]).strip()
        if not literal:
            raise CertifiedCanonicalError("canonical column header provenance does not resolve to a source literal")
        seen.add((row_index, column_index))
        result.append(
            {
                "source_table_uid": source_table_uid,
                "raw_row_index": row_index,
                "raw_column_index": column_index,
                "literal": literal,
                "literal_sha256": _text_sha(literal),
            }
        )
    return result


def _table_structure(
    raw: Mapping[str, Any], normalized: Mapping[str, Any], raw_by_uid: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    uid = str(raw.get("internal_table_uid") or "")
    if not uid or normalized.get("internal_table_uid") != uid:
        raise CertifiedCanonicalError("raw/normalized table identity does not match source-first route")
    if normalized.get("source_record_sha256") != _sha_json(raw):
        raise CertifiedCanonicalError("normalized table does not bind the raw source record")
    raw_rows = raw.get("rows")
    grid = normalized.get("canonical_grid")
    if not isinstance(raw_rows, list) or not raw_rows or not isinstance(grid, Mapping):
        raise CertifiedCanonicalError("source table lacks a usable raw grid or canonical grid")
    width = len(raw_rows[0]) if isinstance(raw_rows[0], list) else 0
    if width < 1 or any(not isinstance(row, list) or len(row) != width for row in raw_rows):
        raise CertifiedCanonicalError("raw source grid is not rectangular")
    canonical_rows = grid.get("rows")
    if (
        not isinstance(canonical_rows, list)
        or len(canonical_rows) != len(raw_rows)
        or any(not isinstance(row, list) or len(row) != width for row in canonical_rows)
        or grid.get("row_count") != len(raw_rows)
        or grid.get("width") != width
    ):
        raise CertifiedCanonicalError("canonical grid shape does not match raw source grid")
    raw_headers = raw.get("header_row_indices") or []
    if not isinstance(raw_headers, list) or any(
        not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(raw_rows)
        for index in raw_headers
    ):
        raise CertifiedCanonicalError("raw table has invalid header row indices")
    header_rows = sorted(set(raw_headers))
    columns = grid.get("columns")
    if not isinstance(columns, list) or len(columns) != width:
        raise CertifiedCanonicalError("canonical grid columns do not cover the raw width")
    canonical_columns: list[dict[str, Any]] = []
    for index, column in enumerate(columns):
        if not isinstance(column, Mapping) or column.get("column_index") != index:
            raise CertifiedCanonicalError("canonical columns are not in raw source order")
        role = str(column.get("role") or "")
        if not role:
            raise CertifiedCanonicalError("canonical column has no structural role")
        canonical_columns.append(
            {
                "column_index": index,
                "role": role,
                "source_header_cells": _source_header_cells(
                    raw_by_uid=raw_by_uid, current_table_uid=uid, column=column
                ),
            }
        )
    row_labels = [
        {
            "raw_row_index": index,
            "raw_column_index": 0,
            "literal": str(row[0]).strip(),
            "literal_sha256": _text_sha(str(row[0]).strip()),
        }
        for index, row in enumerate(raw_rows)
        if index not in set(header_rows) and str(row[0]).strip()
    ]
    document = normalized.get("document")
    if not isinstance(document, Mapping):
        raise CertifiedCanonicalError("normalized table has no document identity")
    identity = {field: document.get(field) for field in ("document_id", "ticker", "report_year", "scope", "page_no", "local_ordinal")}
    if any(value is None or value == "" for value in identity.values()):
        raise CertifiedCanonicalError("normalized table has incomplete document identity")
    quality = normalized.get("quality")
    if not isinstance(quality, Mapping):
        raise CertifiedCanonicalError("normalized table has no source quality record")
    reasons = quality.get("reason_codes") or []
    if not isinstance(reasons, list) or not all(isinstance(value, str) for value in reasons):
        raise CertifiedCanonicalError("normalized table has invalid source quality reasons")
    return {
        "document_identity": identity,
        "raw_grid": {
            "row_count": len(raw_rows),
            "column_count": width,
            "header_row_indices": header_rows,
            "data_row_count": len(raw_rows) - len(header_rows),
            "canonical_grid_rows_match_raw": canonical_rows == raw_rows,
        },
        "canonical_columns": canonical_columns,
        "source_row_labels": row_labels,
        "source_quality_status": quality.get("status"),
        "source_quality_reason_codes": reasons,
    }


def _route_context(
    *, route: Mapping[str, Any], notes: Mapping[str, Any], row_labels: Mapping[str, Any], raw: Mapping[str, Any]
) -> dict[str, Any]:
    status = str(route.get("status") or "")
    if status == "DETERMINISTIC_SOURCE_PROFILE_CONTEXT_ONLY":
        literal = str(route.get("source_heading") or "")
        label = str(route.get("source_profile_semantic_label") or "")
        if not literal or not label or route.get("source_heading_sha256") != _text_sha(literal):
            raise CertifiedCanonicalError("statement source-profile route lacks a literal hash-bound heading")
        return {
            "kind": "literal_financial_statement_heading",
            "literal": literal,
            "literal_sha256": _text_sha(literal),
            "deterministic_profile_kind": label,
        }
    assertion_id = str(route["phase45_assertion_id"])
    if status == "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED":
        note = notes.get(assertion_id)
        if note is None:
            raise CertifiedCanonicalError("note route lacks a matching literal note context")
        _identity_matches(route, note, label="numbered-note context")
        if note.get("status") != "SOURCE_NUMBERED_NOTE_CONTEXT_COMPONENTS_ONLY":
            raise CertifiedCanonicalError("numbered-note context is not materialized")
        if note.get("source_heading_sha256") != route.get("source_heading_sha256"):
            raise CertifiedCanonicalError("numbered-note context heading does not match source-first route")
        return {
            "kind": "literal_numbered_note_heading",
            "note_locator_literal": note.get("note_locator_literal"),
            "note_topic_literal": note.get("note_topic_literal"),
            "note_topic_sha256": note.get("note_topic_sha256"),
            "source_heading_sha256": note.get("source_heading_sha256"),
        }
    if status == "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY":
        context = row_labels.get(assertion_id)
        if context is None:
            raise CertifiedCanonicalError("row-label route lacks a matching source row-label context")
        _identity_matches(route, context, label="row-label context")
        if context.get("status") != "SOURCE_ROW_LABEL_RELATION_CONTEXT_ONLY":
            raise CertifiedCanonicalError("row-label context is not materialized")
        row_index, column_index = context.get("raw_row_index"), context.get("raw_column_index")
        rows = raw.get("rows") or []
        if (
            not isinstance(row_index, int)
            or isinstance(row_index, bool)
            or not isinstance(column_index, int)
            or isinstance(column_index, bool)
            or row_index < 0
            or row_index >= len(rows)
            or column_index < 0
            or column_index >= len(rows[row_index])
        ):
            raise CertifiedCanonicalError("row-label context coordinates are invalid")
        literal = str(rows[row_index][column_index]).strip()
        if not literal or literal != context.get("source_cell_text") or _text_sha(literal) != context.get("source_cell_text_sha256"):
            raise CertifiedCanonicalError("row-label context does not match its raw source cell")
        return {
            "kind": "selected_source_cell_relation",
            "raw_row_index": row_index,
            "raw_column_index": column_index,
            "source_cell_role": context.get("source_cell_role"),
            "literal": literal,
            "literal_sha256": _text_sha(literal),
            "row_label_relation_id": context.get("row_label_relation_id"),
        }
    raise CertifiedCanonicalError("source-first route is not eligible for source-structure materialization")


def _record(
    *,
    route: Mapping[str, Any],
    notes: Mapping[str, Any],
    row_labels: Mapping[str, Any],
    raw: Mapping[str, Any],
    normalized: Mapping[str, Any],
    raw_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    _require_non_promotable(route, label="source-first route")
    if route.get("status") not in _ROUTE_STATUSES:
        raise CertifiedCanonicalError("source-first route has unsupported status")
    structure = _table_structure(raw, normalized, raw_by_uid)
    route_context = _route_context(route=route, notes=notes, row_labels=row_labels, raw=raw)
    payload = {
        "schema_version": PHASE5_TABLE_STRUCTURE_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL,
        "source_first_semantic_route_id": route["source_first_semantic_route_id"],
        "phase45_assertion_id": route["phase45_assertion_id"],
        "phase3_request_id": route["phase3_request_id"],
        "internal_table_uid": route["internal_table_uid"],
        "route_id": route["route_id"],
        "source_first_route_status": route["status"],
        "status": "SOURCE_STRUCTURE_CONTEXT_ONLY",
        "evidence_class": "E0",
        "route_context": route_context,
        "table_structure": structure,
        "reason_codes": list(route.get("reason_codes") or []),
        "derivation_rule": "phase5_hash_bound_source_grid_and_context_join_v1",
        "next_actor": "design_bounded_table_semantic_task_with_explicit_abstention",
        "llm_dispatch_allowed": False,
        "campaign_candidate_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    return {"table_structure_context_id": _sha_json(payload), **payload}


def materialize_phase5_table_structure_context(
    *,
    source_first_routes: Path,
    source_first_manifest: Path,
    phase5_note_contexts: Path,
    phase5_note_context_manifest: Path,
    phase4_row_label_contexts: Path,
    phase4_row_label_context_manifest: Path,
    ccl_input_inventory: Path,
    raw_tables: Path,
    normalized_tables: Path,
    output_dir: Path,
    route_id: str,
) -> Phase5TableStructureContextResult:
    """Join source-first routes to immutable table structure without model inference."""
    inputs = [
        path.resolve()
        for path in (
            source_first_routes,
            source_first_manifest,
            phase5_note_contexts,
            phase5_note_context_manifest,
            phase4_row_label_contexts,
            phase4_row_label_context_manifest,
            ccl_input_inventory,
            raw_tables,
            normalized_tables,
        )
    ]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing Phase 5 source-structure context input")
    _require_new_output(output_dir, inputs)
    before_hashes = {path.name: sha256_file(path) for path in inputs}

    route_manifest = _json(source_first_manifest)
    _require_manifest(
        route_manifest,
        protocol=PHASE5_SEMANTIC_ROUTING_PROTOCOL,
        run_status="phase_5_source_first_semantic_routing_complete_not_certified",
        label="source-first routing",
    )
    _require_hash(
        source_first_routes,
        ((route_manifest.get("outputs") or {}).get(source_first_routes.name) or {}).get("sha256"),
        label="source-first routes",
    )
    note_manifest = _json(phase5_note_context_manifest)
    _require_manifest(
        note_manifest,
        protocol=PHASE5_NOTE_CONTEXT_PROTOCOL,
        run_status="phase_5_numbered_note_context_complete_not_certified",
        label="numbered-note context",
    )
    _require_hash(
        phase5_note_contexts,
        ((note_manifest.get("outputs") or {}).get(phase5_note_contexts.name) or {}).get("sha256"),
        label="numbered-note contexts",
    )
    row_manifest = _json(phase4_row_label_context_manifest)
    _require_manifest(
        row_manifest,
        protocol=PHASE4_ROW_LABEL_CONTEXT_PROTOCOL,
        run_status="phase_4_row_label_context_materialization_complete_not_certified",
        label="row-label context",
    )
    _require_hash(
        phase4_row_label_contexts,
        ((row_manifest.get("outputs") or {}).get(phase4_row_label_contexts.name) or {}).get("sha256"),
        label="row-label contexts",
    )
    inventory = _json(ccl_input_inventory)
    if inventory.get("protocol") != "vifinqa_certified_canonical_v1" or inventory.get("stage") != "phase_0_freeze_and_baseline":
        raise CertifiedCanonicalError("CCL input inventory is unsupported")
    inventory_inputs = inventory.get("inputs") or {}
    _require_hash(raw_tables, (inventory_inputs.get("raw_tables") or {}).get("sha256"), label="raw tables")
    _require_hash(
        normalized_tables,
        (inventory_inputs.get("preprocessing_normalized") or {}).get("sha256"),
        label="normalized tables",
    )

    routes = _index_unique(
        (row for row in _jsonl(source_first_routes) if row.get("route_id") == route_id),
        key="phase45_assertion_id",
        label="source-first routes",
    )
    if not routes:
        raise CertifiedCanonicalError("requested route has no source-first semantic routes")
    notes = _index_unique(
        (row for row in _jsonl(phase5_note_contexts) if row.get("route_id") == route_id),
        key="phase45_assertion_id",
        label="numbered-note contexts",
    )
    row_labels = _index_unique(
        (row for row in _jsonl(phase4_row_label_contexts) if row.get("route_id") == route_id),
        key="phase45_assertion_id",
        label="row-label contexts",
    )
    note_ids = {key for key, route in routes.items() if route.get("status") == "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED"}
    row_ids = {key for key, route in routes.items() if route.get("status") == "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY"}
    if set(notes) != note_ids or set(row_labels) != row_ids:
        raise CertifiedCanonicalError("supplemental source contexts do not exactly cover their source-first routes")
    uids = {str(route.get("internal_table_uid") or "") for route in routes.values()}
    if "" in uids:
        raise CertifiedCanonicalError("source-first route has no table UID")
    raw_by_uid = _select_records(raw_tables, uids)
    normalized_by_uid = _select_records(normalized_tables, uids)
    header_source_uids = {
        str(column.get("header_source_table_uid") or uid)
        for uid, normalized in normalized_by_uid.items()
        for column in ((normalized.get("canonical_grid") or {}).get("columns") or [])
        if isinstance(column, Mapping)
    }
    if "" in header_source_uids:
        raise CertifiedCanonicalError("canonical column has an empty header source table UID")
    raw_by_uid.update(_select_records(raw_tables, header_source_uids - set(raw_by_uid)))
    records = [
        _record(
            route=routes[assertion_id],
            notes=notes,
            row_labels=row_labels,
            raw=raw_by_uid[str(routes[assertion_id]["internal_table_uid"])],
            normalized=normalized_by_uid[str(routes[assertion_id]["internal_table_uid"])],
            raw_by_uid=raw_by_uid,
        )
        for assertion_id in sorted(routes)
    ]
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("source-structure context materialization changed a hash-bound input")
    output_dir.mkdir(parents=True)
    contexts_path = output_dir / _CONTEXT_NAME
    with contexts_path.open("x", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    status_counts = Counter(str(record["source_first_route_status"]) for record in records)
    report = {
        "schema_version": PHASE5_TABLE_STRUCTURE_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL,
        "route_id": route_id,
        "run_status": "phase_5_table_structure_context_complete_not_certified",
        "context_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "source_row_label_count": sum(len(record["table_structure"]["source_row_labels"]) for record in records),
        "input_hashes_unchanged": True,
        "next_gate": "define_and_validate_a_bounded_table_semantic_task_before_any_llm_dispatch",
        "training_eligible_output_count": 0,
        "certification_allowed": False,
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE5_TABLE_STRUCTURE_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL,
        "route_id": route_id,
        "run_status": "phase_5_table_structure_context_complete_not_certified",
        "inputs": {path.name: {"sha256": before_hashes[path.name]} for path in inputs},
        "outputs": {
            name: {"sha256": sha256_file(output_dir / name)}
            for name in (_CONTEXT_NAME, _REPORT_NAME)
        },
        "input_hashes_unchanged": True,
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / _MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return Phase5TableStructureContextResult(output_dir=output_dir, context_count=len(records), materialized_count=len(records))
