"""Hash-bound period-column candidate enumeration and no-candidate diagnostics.

This research-only layer consumes prior bounded row-navigation packets.  It
copies only immutable header/cell metadata and never parses a numeric value,
binds a final column, executes a formula, or promotes provenance.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from finance_query.route_packets import AUXILIARY_TABLE_TYPES, build_navigation_index


PERIOD_COLUMN_PROTOCOL = "period_column_candidate_packets_v1"
NO_CANDIDATE_AUDIT_PROTOCOL = "route_packet_no_candidate_audit_v1"
PERIOD_PACKET_STATUSES = frozenset(
    {
        "unique_period_column_candidate",
        "ambiguous_period_columns",
        "no_period_column",
        "unreliable_numeric_source",
        "packet_blocked",
    }
)
EXCLUSIVE_CAUSE_ORDER = (
    "NO_EXACT_CONCEPT_ROW",
    "ENTITY",
    "YEAR",
    "SCOPE",
    "SECTOR",
    "TABLE_TYPE",
    "AUXILIARY_VETO",
    "ROUTING_ELIGIBILITY",
    "NAVIGATION_GATE",
)
CAUSE_TO_GATE = {
    "ENTITY": "entity",
    "YEAR": "year",
    "SCOPE": "scope",
    "SECTOR": "sector",
    "TABLE_TYPE": "table_type",
    "AUXILIARY_VETO": "auxiliary_veto",
    "ROUTING_ELIGIBILITY": "routing_eligible",
    "NAVIGATION_GATE": "navigation_gate",
}
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
END_DATE_RE = re.compile(r"(?:31\s*[/.-]\s*12\s*[/.-]\s*(?:19|20)\d{2})", re.I)
START_DATE_RE = re.compile(r"(?:0?1\s*[/.-]\s*0?1\s*[/.-]\s*(?:19|20)\d{2})", re.I)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError(f"Expected JSON object records in {path}")
    return records


def _candidate_source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_column": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _audit_source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_column": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _assert_non_promotable(contract: Mapping[str, Any], *, label: str) -> None:
    for key in ("evidence_eligible", "training_eligible", "submission_eligible", "promotion_allowed"):
        if bool(contract.get(key, False)):
            raise ValueError(f"{label} enables {key}")


def _manifest_value(manifest: Mapping[str, Any], *path: str) -> Any:
    current: Any = manifest
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            raise ValueError(f"Manifest missing {'/'.join(path)}")
        current = current[key]
    return current


def _manifest_hash(manifest: Mapping[str, Any], *path: str) -> str:
    value = _manifest_value(manifest, *path)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid hash at {'/'.join(path)}")
    return value


def _require_hash(path: Path, expected: str, *, label: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")


def _record_id(record: Mapping[str, Any], *, field: str, label: str) -> str:
    value = str(record.get(field) or "").strip()
    if not value:
        raise ValueError(f"{label} has missing {field}")
    return value


def _unique_index(
    records: Iterable[Mapping[str, Any]], *, field: str, label: str
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = _record_id(record, field=field, label=label)
        if record_id in index:
            raise ValueError(f"{label} has duplicate {field}: {record_id}")
        index[record_id] = dict(record)
    return index


def _question_id(record: Mapping[str, Any], *, label: str) -> int:
    value = record.get("question_id")
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} has invalid question_id")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} has invalid question_id: {value!r}") from exc


def _question_index(records: Sequence[Mapping[str, Any]], *, label: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for record in records:
        question_id = _question_id(record, label=label)
        if question_id in result:
            raise ValueError(f"{label} has duplicate question_id: {question_id}")
        result[question_id] = dict(record)
    return result


def _year_set(values: Any, *, label: str) -> set[int]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} lacks requested years")
    result: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            raise ValueError(f"{label} has invalid year")
        try:
            result.add(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} has invalid year: {value!r}") from exc
    return result


def _canonical_columns(context: Mapping[str, Any], *, uid: str) -> dict[int, dict[str, Any]]:
    columns = ((context.get("canonical_headers") or {}).get("columns") or [])
    result: dict[int, dict[str, Any]] = {}
    for column in columns:
        if not isinstance(column, Mapping):
            raise ValueError(f"V3 canonical header malformed for {uid}")
        value = column.get("column_index")
        if isinstance(value, bool):
            raise ValueError(f"V3 canonical header has invalid index for {uid}")
        try:
            column_index = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"V3 canonical header has invalid index for {uid}") from exc
        if column_index in result:
            raise ValueError(f"V3 canonical header has duplicate index for {uid}: {column_index}")
        result[column_index] = dict(column)
    return result


def _row_profiles(context: Mapping[str, Any], *, uid: str, row_count: int) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for profile in context.get("row_profiles") or []:
        if not isinstance(profile, Mapping):
            raise ValueError(f"V3 row profile malformed for {uid}")
        value = profile.get("row_index")
        if isinstance(value, bool):
            raise ValueError(f"V3 row profile has invalid index for {uid}")
        try:
            row_index = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"V3 row profile has invalid index for {uid}") from exc
        if row_index in result or row_index < 0 or row_index >= row_count:
            raise ValueError(f"V3 row profile coverage mismatch for {uid}")
        result[row_index] = dict(profile)
    if set(result) != set(range(row_count)):
        raise ValueError(f"V3 row profile coverage mismatch for {uid}")
    return result


@dataclass(frozen=True, slots=True)
class ValidatedInputs:
    packets: list[dict[str, Any]]
    routes_by_id: Mapping[int, dict[str, Any]]
    v2_by_uid: Mapping[str, dict[str, Any]]
    v3_by_uid: Mapping[str, dict[str, Any]]
    routing_by_uid: Mapping[str, dict[str, Any]]
    document_by_id: Mapping[str, dict[str, Any]]
    role_by_uid: Mapping[str, dict[str, Any]]
    sector_by_document: Mapping[str, dict[str, Any]]
    exact_candidates_by_concept: Mapping[str, tuple[dict[str, Any], ...]]
    input_hashes: Mapping[str, dict[str, str]]


def _validate_packet_against_route(packet: Mapping[str, Any], route: Mapping[str, Any]) -> None:
    if packet.get("route_status") != route.get("route_status"):
        raise ValueError(f"Packet route_status mismatch for Q{packet['question_id']}")
    if packet.get("question_context") != route.get("question_context"):
        raise ValueError(f"Packet question_context mismatch for Q{packet['question_id']}")
    packet_stages = {str(stage.get("stage_id") or ""): stage for stage in packet.get("stages") or []}
    route_stages = {str(stage.get("stage_id") or ""): stage for stage in route.get("stages") or []}
    if set(packet_stages) != set(route_stages):
        raise ValueError(f"Packet stage coverage mismatch for Q{packet['question_id']}")
    for stage_id, route_stage in route_stages.items():
        packet_stage = packet_stages[stage_id]
        route_operands = list(route_stage.get("required_operands") or [])
        packet_operands = list(packet_stage.get("required_operands") or [])
        if len(route_operands) != len(packet_operands):
            raise ValueError(f"Packet operand count mismatch for Q{packet['question_id']} {stage_id}")
        for route_operand, packet_operand in zip(route_operands, packet_operands):
            for field in ("role", "concept_id", "period_type"):
                if route_operand.get(field) != packet_operand.get(field):
                    raise ValueError(f"Packet operand {field} mismatch for Q{packet['question_id']} {stage_id}")
            if list(route_operand.get("statement_types") or []) != list(packet_operand.get("expected_table_types") or []):
                raise ValueError(f"Packet operand table type mismatch for Q{packet['question_id']} {stage_id}")


def _validate_v2_v3_lineage(
    v2_rows: Sequence[Mapping[str, Any]], v3_rows: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    v2_by_uid = _unique_index(v2_rows, field="internal_table_uid", label="V2 tables")
    v3_by_uid = _unique_index(v3_rows, field="internal_table_uid", label="V3 contexts")
    if set(v2_by_uid) != set(v3_by_uid):
        raise ValueError("V2/V3 table UID coverage mismatch")
    for uid, v2 in v2_by_uid.items():
        v3 = v3_by_uid[uid]
        if v2.get("document_id") != v3.get("document_id"):
            raise ValueError(f"V2/V3 document mismatch for UID {uid}")
        v2_rows_grid = [[str(cell) for cell in row] for row in v2.get("rows") or []]
        grid = v3.get("grid") or {}
        if not isinstance(grid, Mapping) or not bool(grid.get("rectangular")) or not bool(grid.get("provenance_complete")):
            raise ValueError(f"V3 grid integrity mismatch for UID {uid}")
        if int(grid.get("width") or -1) != (len(v2_rows_grid[0]) if v2_rows_grid else 0):
            raise ValueError(f"V2/V3 grid width mismatch for UID {uid}")
        if v3.get("source_provenance") != v2.get("source_provenance"):
            raise ValueError(f"V2/V3 source provenance mismatch for UID {uid}")
        provenance = v2.get("cell_provenance") or []
        if len(provenance) != len(v2_rows_grid) or any(
            not isinstance(row, list) or len(row) != len(v2_rows_grid[index])
            for index, row in enumerate(provenance)
        ):
            raise ValueError(f"V2 cell provenance coverage mismatch for UID {uid}")
        columns = _canonical_columns(v3, uid=uid)
        width = len(v2_rows_grid[0]) if v2_rows_grid else 0
        if set(columns) != set(range(width)):
            raise ValueError(f"V3 canonical header coverage mismatch for UID {uid}")
        _row_profiles(v3, uid=uid, row_count=len(v2_rows_grid))
    return v2_by_uid, v3_by_uid


def _exact_concept_index(candidates: Sequence[Mapping[str, Any]]) -> dict[str, tuple[dict[str, Any], ...]]:
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        if str(candidate.get("match_status") or "") != "exact_unique":
            continue
        concept_candidates = list(candidate.get("concept_candidates") or [])
        if len(concept_candidates) != 1:
            raise ValueError("exact_unique taxonomy candidate does not have exactly one concept")
        concept_id = str(concept_candidates[0].get("concept_id") or "").strip()
        if not concept_id:
            raise ValueError("exact_unique taxonomy candidate lacks concept_id")
        values[concept_id].append(dict(candidate))
    return {
        concept: tuple(sorted(rows, key=lambda row: (
            str(row.get("document_id") or ""), str(row.get("internal_table_uid") or ""), int(row.get("row_index") or 0)
        )))
        for concept, rows in values.items()
    }


def validate_inputs(
    *,
    packets_path: Path,
    packets_manifest_path: Path,
    routes_path: Path,
    routes_manifest_path: Path,
    candidates_path: Path,
    candidates_manifest_path: Path,
    table_roles_path: Path,
    sectors_path: Path,
    routing_catalog_path: Path,
    routing_manifest_path: Path,
    document_metadata_path: Path,
    structured_tables_path: Path,
    structure_manifest_path: Path,
    evidence_context_path: Path,
    evidence_manifest_path: Path,
) -> ValidatedInputs:
    """Validate every declared immutable dependency before any materialization."""
    packet_manifest = load_json(packets_manifest_path)
    routes_manifest = load_json(routes_manifest_path)
    candidate_manifest = load_json(candidates_manifest_path)
    routing_manifest = load_json(routing_manifest_path)
    structure_manifest = load_json(structure_manifest_path)
    evidence_manifest = load_json(evidence_manifest_path)
    _require_hash(packets_path, _manifest_hash(packet_manifest, "outputs", "packets", "sha256"), label="route packets")
    _require_hash(routes_path, _manifest_hash(packet_manifest, "inputs", "question_routes", "sha256"), label="question routes against packet manifest")
    _require_hash(routes_manifest_path, _manifest_hash(packet_manifest, "inputs", "question_routes_manifest", "sha256"), label="question routes manifest against packet manifest")
    _require_hash(candidates_path, _manifest_hash(packet_manifest, "inputs", "taxonomy_candidates", "sha256"), label="taxonomy candidates against packet manifest")
    _require_hash(candidates_manifest_path, _manifest_hash(packet_manifest, "inputs", "taxonomy_candidates_manifest", "sha256"), label="candidate manifest against packet manifest")
    _require_hash(table_roles_path, _manifest_hash(packet_manifest, "inputs", "table_roles", "sha256"), label="table roles against packet manifest")
    _require_hash(sectors_path, _manifest_hash(packet_manifest, "inputs", "sectors", "sha256"), label="sectors against packet manifest")
    _require_hash(routing_catalog_path, _manifest_hash(packet_manifest, "inputs", "routing_catalog", "sha256"), label="routing catalog against packet manifest")
    _require_hash(routing_manifest_path, _manifest_hash(packet_manifest, "inputs", "routing_manifest", "sha256"), label="routing manifest against packet manifest")
    _require_hash(document_metadata_path, _manifest_hash(packet_manifest, "inputs", "document_metadata", "sha256"), label="document metadata against packet manifest")
    _require_hash(structured_tables_path, _manifest_hash(packet_manifest, "inputs", "structured_tables", "sha256"), label="V2 tables against packet manifest")
    _require_hash(structure_manifest_path, _manifest_hash(packet_manifest, "inputs", "structure_manifest", "sha256"), label="V2 manifest against packet manifest")
    _require_hash(routes_path, _manifest_hash(routes_manifest, "output", "sha256"), label="question routes against route manifest")
    _require_hash(candidates_path, _manifest_hash(candidate_manifest, "outputs", "row_candidates", "sha256"), label="taxonomy candidates against candidate manifest")
    _require_hash(table_roles_path, _manifest_hash(candidate_manifest, "outputs", "table_role_candidates", "sha256"), label="table roles against candidate manifest")
    _require_hash(sectors_path, _manifest_hash(candidate_manifest, "outputs", "sector_candidates", "sha256"), label="sectors against candidate manifest")
    _require_hash(routing_catalog_path, _manifest_hash(routing_manifest, "table_catalog_sha256"), label="routing catalog against routing manifest")
    _require_hash(document_metadata_path, _manifest_hash(routing_manifest, "document_metadata_sha256"), label="document metadata against routing manifest")
    _require_hash(structured_tables_path, _manifest_hash(routing_manifest, "input_structure_sha256"), label="V2 tables against routing manifest")
    _require_hash(structured_tables_path, _manifest_hash(structure_manifest, "sidecar_sha256"), label="V2 tables against V2 manifest")
    _require_hash(evidence_context_path, _manifest_hash(evidence_manifest, "sidecar_sha256"), label="V3 contexts against V3 manifest")
    _require_hash(structured_tables_path, _manifest_hash(evidence_manifest, "input_structure_sha256"), label="V2 tables against V3 manifest")
    for manifest, label in ((packet_manifest, "packet manifest"), (routes_manifest, "route manifest"), (candidate_manifest, "candidate manifest")):
        _assert_non_promotable(manifest.get("source_contract") or {}, label=label)
        _assert_non_promotable(manifest, label=label)

    packets = load_jsonl(packets_path)
    routes = load_jsonl(routes_path)
    packet_by_id = _question_index(packets, label="Route packets")
    routes_by_id = _question_index(routes, label="Question routes")
    if set(packet_by_id) != set(routes_by_id):
        raise ValueError("Route packet/question route ID coverage mismatch")
    if int(packet_manifest.get("question_count") or -1) != len(packets):
        raise ValueError("Route packet manifest question_count mismatch")
    if int(packet_manifest.get("question_id_count") or -1) != len(packet_by_id):
        raise ValueError("Route packet manifest question_id_count mismatch")
    for question_id, packet in packet_by_id.items():
        if str(packet.get("packet_status") or "") not in {"bounded", "no_candidate", "route_blocked"}:
            raise ValueError(f"Unsupported route packet status for Q{question_id}")
        _assert_non_promotable(packet.get("source_contract") or {}, label=f"Route packet Q{question_id}")
        _validate_packet_against_route(packet, routes_by_id[question_id])

    v2_rows = load_jsonl(structured_tables_path)
    v3_rows = load_jsonl(evidence_context_path)
    routing_rows = load_jsonl(routing_catalog_path)
    document_rows = load_jsonl(document_metadata_path)
    role_rows = load_jsonl(table_roles_path)
    sector_rows = load_jsonl(sectors_path)
    candidates = load_jsonl(candidates_path)
    v2_by_uid, v3_by_uid = _validate_v2_v3_lineage(v2_rows, v3_rows)
    navigation_index = build_navigation_index(
        candidate_rows=candidates,
        routing_rows=routing_rows,
        document_rows=document_rows,
        structured_rows=v2_rows,
        table_role_rows=role_rows,
        sector_rows=sector_rows,
    )
    if set(navigation_index.table_by_uid) != set(v3_by_uid):
        raise ValueError("Routing/V3 table UID coverage mismatch")
    for question_id, packet in packet_by_id.items():
        for stage in packet.get("stages") or []:
            for operand in stage.get("required_operands") or []:
                coordinates: set[tuple[str, int]] = set()
                for row in operand.get("navigation_candidates") or []:
                    uid = _record_id(row, field="internal_table_uid", label=f"Packet Q{question_id}")
                    if uid not in v2_by_uid:
                        raise ValueError(f"Packet references unknown UID {uid}")
                    row_index = row.get("row_index")
                    if isinstance(row_index, bool):
                        raise ValueError(f"Packet has invalid row index for UID {uid}")
                    row_index = int(row_index)
                    rows = v2_by_uid[uid].get("rows") or []
                    if row_index < 0 or row_index >= len(rows):
                        raise ValueError(f"Packet row index out of range for UID {uid}")
                    coordinate = (uid, row_index)
                    if coordinate in coordinates:
                        raise ValueError(f"Packet operand has duplicate navigation coordinate {coordinate}")
                    coordinates.add(coordinate)
                    raw = [str(cell) for cell in row.get("raw_source_row") or []]
                    if raw != [str(cell) for cell in rows[row_index]]:
                        raise ValueError(f"Packet/V2 raw source row mismatch for UID {uid}, row {row_index}")
                    # V3 intentionally stores grid-integrity metadata rather than a second raw
                    # cell matrix.  The V3 table identity, raw-source provenance, row-profile
                    # coverage, and width were validated against V2 above; V2 remains the only
                    # immutable raw-row representation.
                    _row_profiles(v3_by_uid[uid], uid=uid, row_count=len(rows))[row_index]
                    if row.get("document_id") != v2_by_uid[uid].get("document_id"):
                        raise ValueError(f"Packet document mismatch for UID {uid}")
                    _assert_non_promotable(row.get("source_contract") or {}, label=f"Packet navigation row {uid}")

    inputs = {
        "route_packets": {"path": str(packets_path), "sha256": sha256_file(packets_path)},
        "route_packets_manifest": {"path": str(packets_manifest_path), "sha256": sha256_file(packets_manifest_path)},
        "question_routes": {"path": str(routes_path), "sha256": sha256_file(routes_path)},
        "question_routes_manifest": {"path": str(routes_manifest_path), "sha256": sha256_file(routes_manifest_path)},
        "taxonomy_candidates": {"path": str(candidates_path), "sha256": sha256_file(candidates_path)},
        "taxonomy_candidates_manifest": {"path": str(candidates_manifest_path), "sha256": sha256_file(candidates_manifest_path)},
        "table_roles": {"path": str(table_roles_path), "sha256": sha256_file(table_roles_path)},
        "sectors": {"path": str(sectors_path), "sha256": sha256_file(sectors_path)},
        "routing_catalog": {"path": str(routing_catalog_path), "sha256": sha256_file(routing_catalog_path)},
        "routing_manifest": {"path": str(routing_manifest_path), "sha256": sha256_file(routing_manifest_path)},
        "document_metadata": {"path": str(document_metadata_path), "sha256": sha256_file(document_metadata_path)},
        "structured_tables_v2": {"path": str(structured_tables_path), "sha256": sha256_file(structured_tables_path)},
        "structure_manifest_v2": {"path": str(structure_manifest_path), "sha256": sha256_file(structure_manifest_path)},
        "evidence_context_v3": {"path": str(evidence_context_path), "sha256": sha256_file(evidence_context_path)},
        "evidence_manifest_v3": {"path": str(evidence_manifest_path), "sha256": sha256_file(evidence_manifest_path)},
    }
    return ValidatedInputs(
        packets=[packet_by_id[item_id] for item_id in sorted(packet_by_id)],
        routes_by_id=routes_by_id,
        v2_by_uid=v2_by_uid,
        v3_by_uid=v3_by_uid,
        routing_by_uid=navigation_index.routing_by_uid,
        document_by_id=navigation_index.document_by_id,
        role_by_uid=navigation_index.role_by_uid,
        sector_by_document=navigation_index.sector_by_document,
        exact_candidates_by_concept=_exact_concept_index(candidates),
        input_hashes=inputs,
    )


def _period_kind(source_label: str) -> str:
    text = source_label.casefold()
    if "lũy kế" in text or "luy ke" in text:
        return "cumulative"
    if START_DATE_RE.search(text) or "số đầu năm" in text or "đầu kỳ" in text:
        return "instant_start"
    if END_DATE_RE.search(text) or "số cuối năm" in text or "cuối kỳ" in text or "tại ngày" in text:
        return "instant_end"
    if "năm" in text or "kỳ" in text:
        return "duration"
    return "bare_year"


def _period_years(header: Mapping[str, Any]) -> set[int]:
    values = [str(value) for value in header.get("period_labels") or []]
    return {int(match.group(1)) for value in values for match in YEAR_RE.finditer(value)}


def _period_compatible(*, period_type: str, source_label: str) -> tuple[bool, str]:
    kind = _period_kind(source_label)
    if kind == "cumulative":
        return False, "CUMULATIVE_PERIOD_LABEL_UNSUPPORTED"
    if period_type == "instant":
        if kind == "instant_end":
            return True, "INSTANT_PERIOD_LABEL"
        return False, "PERIOD_TYPE_MISMATCH"
    if period_type == "duration":
        if kind in {"duration", "bare_year"}:
            return True, "DURATION_PERIOD_LABEL" if kind == "duration" else "BARE_YEAR_PERIOD_LABEL"
        return False, "PERIOD_TYPE_MISMATCH"
    return False, "UNSUPPORTED_PERIOD_TYPE"


def _column_candidate(
    *, uid: str, row_index: int, column: Mapping[str, Any], requested_year: int,
    raw_source_cell: str, cell_provenance: Mapping[str, Any], compatibility_code: str,
) -> dict[str, Any]:
    return {
        "internal_table_uid": uid,
        "row_index": row_index,
        "column_index": int(column["column_index"]),
        "requested_year": requested_year,
        "source_label": str(column.get("source_label") or ""),
        "header_source_cells": list(column.get("header_source_cells") or []),
        "period_labels": list(column.get("period_labels") or []),
        "unit_labels": list(column.get("unit_labels") or []),
        "raw_source_cell": raw_source_cell,
        "cell_provenance": dict(cell_provenance),
        "reason_codes": ["EXACT_REQUESTED_YEAR", compatibility_code],
        "source_contract": _candidate_source_contract(),
    }


def _prefer_exact_document_year_when_equivalent(
    *,
    candidates: Sequence[Mapping[str, Any]],
    v2_by_uid: Mapping[str, Mapping[str, Any]],
    document_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse only an identical direct-vs-comparative source duplicate.

    A requested period can appear both in the report for that period and in a
    following report's comparative column.  This is not enough on its own to
    choose one source.  We collapse the duplicate only if the raw source cell
    and the literal unit labels agree exactly, and exactly one candidate comes
    from a document whose report year equals the requested year.  The returned
    record remains a non-promotable column candidate; no answer or value is
    computed here.
    """
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        copied = dict(candidate)
        requested_year = copied.get("requested_year")
        if isinstance(requested_year, bool):
            raise ValueError("Period candidate has invalid requested_year")
        try:
            grouped[int(requested_year)].append(copied)
        except (TypeError, ValueError) as error:
            raise ValueError("Period candidate has invalid requested_year") from error

    retained: list[dict[str, Any]] = []
    for requested_year in sorted(grouped):
        group = grouped[requested_year]
        direct: list[dict[str, Any]] = []
        for candidate in group:
            uid = str(candidate.get("internal_table_uid") or "")
            table = v2_by_uid.get(uid)
            if not isinstance(table, Mapping):
                raise ValueError(f"Period candidate references unknown table {uid}")
            document_id = str(table.get("document_id") or "")
            document = document_by_id.get(document_id)
            if not isinstance(document, Mapping):
                raise ValueError(f"Period candidate references unknown document {document_id}")
            report_year = document.get("report_year")
            if isinstance(report_year, bool):
                raise ValueError(f"Period candidate document has invalid report_year {document_id}")
            try:
                is_direct = int(report_year) == requested_year
            except (TypeError, ValueError) as error:
                raise ValueError(f"Period candidate document has invalid report_year {document_id}") from error
            if is_direct:
                direct.append(candidate)

        equivalent_values = {
            (
                str(candidate.get("raw_source_cell") or ""),
                tuple(str(unit) for unit in candidate.get("unit_labels") or []),
            )
            for candidate in group
        }
        if len(group) > 1 and len(direct) == 1 and len(equivalent_values) == 1:
            preferred = dict(direct[0])
            preferred["reason_codes"] = list(preferred.get("reason_codes") or []) + [
                "DOCUMENT_REPORT_YEAR_EXACT_PREFERENCE_AMONG_EQUAL_SOURCE_VALUES"
            ]
            preferred["equivalent_candidate_count"] = len(group)
            retained.append(preferred)
        else:
            retained.extend(group)

    retained.sort(key=lambda candidate: (
        str(candidate["internal_table_uid"]),
        int(candidate["row_index"]),
        int(candidate["column_index"]),
        int(candidate["requested_year"]),
    ))
    return retained


def enumerate_period_columns(
    *, navigation_row: Mapping[str, Any], period_type: str, requested_years: set[int],
    v2: Mapping[str, Any], v3: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, int], int]:
    """Enumerate raw period-column candidates for one already-validated row."""
    uid = str(navigation_row["internal_table_uid"])
    row_index = int(navigation_row["row_index"])
    columns = _canonical_columns(v3, uid=uid)
    profiles = _row_profiles(v3, uid=uid, row_count=len(v2.get("rows") or []))
    profile = profiles[row_index]
    numeric = {int(value) for value in profile.get("numeric_columns") or []}
    structural = {int(value) for value in profile.get("structural_numeric_columns") or []}
    unreliable = {int(value) for value in profile.get("unreliable_numeric_columns") or []}
    row = [str(value) for value in (v2.get("rows") or [])[row_index]]
    provenance_row = (v2.get("cell_provenance") or [])[row_index]
    reasons: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    matching_unreliable = 0
    for column_index in sorted(columns):
        column = columns[column_index]
        if str(column.get("role") or "") != "value_or_text":
            reasons["COLUMN_ROLE_NOT_VALUE_OR_TEXT"] += 1
            continue
        years = _period_years(column)
        exact_years = sorted(years.intersection(requested_years))
        if not exact_years:
            reasons["NO_EXACT_REQUESTED_YEAR_PERIOD_LABEL"] += 1
            continue
        compatible, compatibility_code = _period_compatible(
            period_type=period_type, source_label=str(column.get("source_label") or "")
        )
        if not compatible:
            reasons[compatibility_code] += 1
            continue
        if column_index in unreliable or (column_index in structural and column_index not in numeric):
            matching_unreliable += 1
            reasons["UNRELIABLE_NUMERIC_SOURCE_VETO"] += 1
            continue
        if column_index not in numeric:
            reasons["ROW_COLUMN_NOT_RELIABLE_NUMERIC"] += 1
            continue
        for requested_year in exact_years:
            candidates.append(
                _column_candidate(
                    uid=uid,
                    row_index=row_index,
                    column=column,
                    requested_year=requested_year,
                    raw_source_cell=row[column_index],
                    cell_provenance=provenance_row[column_index],
                    compatibility_code=compatibility_code,
                )
            )
    candidates.sort(key=lambda candidate: (
        str(candidate["internal_table_uid"]), int(candidate["row_index"]), int(candidate["column_index"]), int(candidate["requested_year"])
    ))
    if len(candidates) == 1:
        status = "unique_period_column_candidate"
    elif len(candidates) > 1:
        status = "ambiguous_period_columns"
    elif matching_unreliable:
        status = "unreliable_numeric_source"
    else:
        status = "no_period_column"
    return status, candidates, dict(sorted(reasons.items())), matching_unreliable


def _blocked_operand(operand: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": operand.get("role"),
        "concept_id": operand.get("concept_id"),
        "period_type": operand.get("period_type"),
        "expected_table_types": list(operand.get("expected_table_types") or []),
        "navigation_row_count": 0,
        "period_column_candidate_count": 0,
        "column_status": "packet_blocked",
        "column_candidate_reason_counts": {},
        "period_column_candidates": [],
    }


def _bounded_operand(
    *, operand: Mapping[str, Any], requested_years: set[int], v2_by_uid: Mapping[str, dict[str, Any]],
    v3_by_uid: Mapping[str, dict[str, Any]], document_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], int]:
    all_candidates: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    row_statuses: Counter[str] = Counter()
    unreliable_cells = 0
    navigation_rows = list(operand.get("navigation_candidates") or [])
    for navigation_row in navigation_rows:
        uid = str(navigation_row["internal_table_uid"])
        status, candidates, reasons, unreliable = enumerate_period_columns(
            navigation_row=navigation_row,
            period_type=str(operand.get("period_type") or ""),
            requested_years=requested_years,
            v2=v2_by_uid[uid],
            v3=v3_by_uid[uid],
        )
        all_candidates.extend(candidates)
        reason_counts.update(reasons)
        row_statuses[status] += 1
        unreliable_cells += unreliable
    all_candidates = _prefer_exact_document_year_when_equivalent(
        candidates=all_candidates,
        v2_by_uid=v2_by_uid,
        document_by_id=document_by_id,
    )
    if len(all_candidates) == 1:
        status = "unique_period_column_candidate"
    elif len(all_candidates) > 1:
        status = "ambiguous_period_columns"
    elif row_statuses.get("unreliable_numeric_source"):
        status = "unreliable_numeric_source"
    else:
        status = "no_period_column"
    return {
        "role": operand.get("role"),
        "concept_id": operand.get("concept_id"),
        "period_type": operand.get("period_type"),
        "expected_table_types": list(operand.get("expected_table_types") or []),
        "navigation_row_count": len(navigation_rows),
        "period_column_candidate_count": len(all_candidates),
        "column_status": status,
        "column_candidate_reason_counts": dict(sorted(reason_counts.items())),
        "row_status_counts": dict(sorted(row_statuses.items())),
        "period_column_candidates": all_candidates,
    }, unreliable_cells


def _packet_status(operands: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(operand.get("column_status") or "") for operand in operands}
    if "no_period_column" in statuses:
        return "no_period_column"
    if "unreliable_numeric_source" in statuses:
        return "unreliable_numeric_source"
    if "ambiguous_period_columns" in statuses:
        return "ambiguous_period_columns"
    if statuses == {"unique_period_column_candidate"} and operands:
        return "unique_period_column_candidate"
    raise ValueError(f"Unsupported bounded operand status combination: {sorted(statuses)}")


def build_period_packet(packet: Mapping[str, Any], *, inputs: ValidatedInputs) -> tuple[dict[str, Any], int]:
    """Build one packet without changing the prior route packet."""
    question_id = _question_id(packet, label="Route packet")
    source_status = str(packet.get("packet_status") or "")
    requested_years = (
        _year_set((packet.get("question_context") or {}).get("years"), label=f"Packet Q{question_id}")
        if source_status == "bounded"
        else set()
    )
    stages: list[dict[str, Any]] = []
    unreliable_cells = 0
    for source_stage in packet.get("stages") or []:
        operands: list[dict[str, Any]] = []
        for operand in source_stage.get("required_operands") or []:
            if source_status != "bounded":
                operands.append(_blocked_operand(operand))
            else:
                built, count = _bounded_operand(
                    operand=operand,
                    requested_years=requested_years,
                    v2_by_uid=inputs.v2_by_uid,
                    v3_by_uid=inputs.v3_by_uid,
                    document_by_id=inputs.document_by_id,
                )
                operands.append(built)
                unreliable_cells += count
        stages.append({
            "stage_id": source_stage.get("stage_id"),
            "route_kind": source_stage.get("route_kind"),
            "metric_id": source_stage.get("metric_id"),
            "concept_id": source_stage.get("concept_id"),
            "required_operands": operands,
        })
    operands = [operand for stage in stages for operand in stage["required_operands"]]
    status = "packet_blocked" if source_status != "bounded" else _packet_status(operands)
    if status not in PERIOD_PACKET_STATUSES:
        raise AssertionError("Invalid period packet status")
    return {
        "schema_version": 1,
        "protocol": PERIOD_COLUMN_PROTOCOL,
        "question_id": question_id,
        "input_packet_status": source_status,
        "route_status": packet.get("route_status"),
        "question_context": packet.get("question_context"),
        "packet_status": status,
        "stages": stages,
        "source_contract": _candidate_source_contract(),
    }, unreliable_cells


def _stage_sector_constraints(route: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for stage in route.get("stages") or []:
        values = ((stage.get("retrieval_filters") or {}).get("sectors") or [])
        result[str(stage.get("stage_id") or "")] = {str(value) for value in values if str(value)}
    return result


def _candidate_fail_vector(
    *, candidate: Mapping[str, Any], operand: Mapping[str, Any], context: Mapping[str, Any],
    allowed_sectors: set[str], inputs: ValidatedInputs,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    uid = str(candidate["internal_table_uid"])
    routing = inputs.routing_by_uid[uid]
    metadata = inputs.document_by_id[str(candidate["document_id"])]
    role = inputs.role_by_uid[uid]
    entities = {str(value) for value in context.get("entities") or [] if str(value)}
    years = _year_set(context.get("years"), label="No-candidate audit context")
    scope = str(context.get("scope") or "")
    report_year = metadata.get("report_year")
    available_years = {
        int(value) for value in routing.get("available_period_years") or []
        if not isinstance(value, bool) and str(value).strip()
    }
    period_pass = report_year in years or bool(years.intersection(available_years))
    table_type = str(routing.get("table_type") or "")
    role_veto = (
        str(role.get("existing_table_type") or "") in AUXILIARY_TABLE_TYPES
        and str(role.get("proposed_table_type") or "") not in AUXILIARY_TABLE_TYPES
    )
    auxiliary_pass = table_type not in AUXILIARY_TABLE_TYPES and not role_veto
    sector = str(inputs.sector_by_document[str(candidate["document_id"])].get("sector") or "unknown")
    values: dict[str, dict[str, Any]] = {
        "entity": {
            "pass": str(metadata.get("company") or "") in entities,
            "expected": sorted(entities),
            "observed": metadata.get("company"),
        },
        "year": {
            "pass": period_pass,
            "expected": sorted(years),
            "observed_report_year": report_year,
            "observed_available_period_years": sorted(available_years),
        },
        "scope": {"pass": str(metadata.get("report_scope") or "") == scope, "expected": scope, "observed": metadata.get("report_scope")},
        "sector": {"pass": not allowed_sectors or sector in allowed_sectors, "expected": sorted(allowed_sectors), "observed": sector},
        "table_type": {
            "pass": table_type in {str(value) for value in operand.get("expected_table_types") or [] if str(value)},
            "expected": list(operand.get("expected_table_types") or []),
            "observed": table_type,
        },
        "routing_eligible": {"pass": bool(routing.get("routing_eligible")), "observed": bool(routing.get("routing_eligible"))},
        "navigation_gate": {"pass": str(candidate.get("navigation_gate_status") or "") == "ready", "observed": candidate.get("navigation_gate_status")},
        "auxiliary_veto": {"pass": auxiliary_pass, "observed_table_type": table_type, "table_role_conflict": role_veto},
    }
    failed = {name for name, value in values.items() if not value["pass"]}
    return values, failed


def _exclusive_cause(fail_sets: Sequence[set[str]]) -> tuple[str, list[str]]:
    if not fail_sets:
        return "NO_EXACT_CONCEPT_ROW", []
    for cause in EXCLUSIVE_CAUSE_ORDER[1:]:
        gate = CAUSE_TO_GATE[cause]
        if all(gate in failures for failures in fail_sets):
            return cause, [gate]
    universe = sorted(set().union(*fail_sets))
    smallest_hitting_sets: list[tuple[str, ...]] = []
    for size in range(2, len(universe) + 1):
        from itertools import combinations
        hits = [combo for combo in combinations(universe, size) if all(set(combo).intersection(failures) for failures in fail_sets)]
        if hits:
            smallest_hitting_sets = hits
            break
    if not smallest_hitting_sets:
        raise ValueError("Exact-concept audit found a gate-passing candidate in a no_candidate packet")
    return "MULTIPLE_INSEPARABLE_BLOCKERS", list(smallest_hitting_sets[0])


def _recommended_queue(cause: str) -> str:
    if cause == "NO_EXACT_CONCEPT_ROW":
        return "taxonomy_alias"
    if cause in {"ENTITY", "YEAR", "SCOPE", "SECTOR"}:
        return "document_metadata"
    if cause in {"TABLE_TYPE", "AUXILIARY_VETO"}:
        return "table_role"
    if cause in {"ROUTING_ELIGIBILITY", "NAVIGATION_GATE", "MULTIPLE_INSEPARABLE_BLOCKERS"}:
        return "navigation_gate"
    return "source_absent"


def _minimal_failure_sets(fail_sets: Sequence[set[str]]) -> list[list[str]]:
    if not fail_sets:
        return [["NO_EXACT_CONCEPT_ROW"]]
    smallest = min(len(failures) for failures in fail_sets)
    return sorted({tuple(sorted(failures)) for failures in fail_sets if len(failures) == smallest})


def audit_no_candidate_packets(*, inputs: ValidatedInputs) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    audits: list[dict[str, Any]] = []
    raw_rejections: Counter[str] = Counter()
    exclusive_counts: Counter[str] = Counter()
    for packet in inputs.packets:
        if packet.get("packet_status") != "no_candidate":
            continue
        question_id = _question_id(packet, label="Route packet")
        route = inputs.routes_by_id[question_id]
        sector_constraints = _stage_sector_constraints(route)
        for stage in packet.get("stages") or []:
            for operand in stage.get("required_operands") or []:
                if int(operand.get("candidate_count_total") or 0) != 0:
                    continue
                concept_id = str(operand.get("concept_id") or "")
                exact_rows = inputs.exact_candidates_by_concept.get(concept_id, ())
                vectors: list[dict[str, Any]] = []
                fail_sets: list[set[str]] = []
                for candidate in exact_rows:
                    vector, failures = _candidate_fail_vector(
                        candidate=candidate,
                        operand=operand,
                        context=packet.get("question_context") or {},
                        allowed_sectors=sector_constraints.get(str(stage.get("stage_id") or ""), set()),
                        inputs=inputs,
                    )
                    raw_rejections.update(failures)
                    fail_sets.append(failures)
                    vectors.append({
                        "internal_table_uid": candidate.get("internal_table_uid"),
                        "document_id": candidate.get("document_id"),
                        "row_index": candidate.get("row_index"),
                        "gate_vector": vector,
                        "failure_codes": sorted(failures),
                    })
                if any(not failures for failures in fail_sets):
                    raise ValueError(f"No-candidate audit found gate-passing exact row for Q{question_id} {concept_id}")
                cause, cause_set = _exclusive_cause(fail_sets)
                exclusive_counts[cause] += 1
                nearest_count = min((len(failures) for failures in fail_sets), default=0)
                nearby = [
                    vector for vector in vectors
                    if len(vector["failure_codes"]) == nearest_count
                ]
                nearby.sort(key=lambda row: (
                    str(row["document_id"]), str(row["internal_table_uid"]), int(row["row_index"])
                ))
                audits.append({
                    "schema_version": 1,
                    "protocol": NO_CANDIDATE_AUDIT_PROTOCOL,
                    "question_id": question_id,
                    "stage_id": stage.get("stage_id"),
                    "role": operand.get("role"),
                    "concept_id": concept_id,
                    "exact_concept_row_count": len(exact_rows),
                    "nearby_exact_concept_candidates": nearby,
                    "minimal_blocker_sets": _minimal_failure_sets(fail_sets),
                    "exclusive_primary_cause": cause,
                    "exclusive_primary_cause_blocker_set": cause_set,
                    "recommended_review_queue": _recommended_queue(cause),
                    "source_contract": _audit_source_contract(),
                })
    audits.sort(key=lambda row: (int(row["question_id"]), str(row["stage_id"]), str(row["role"])))
    return audits, raw_rejections, exclusive_counts


def _compact_example(packet: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question_id": packet["question_id"],
        "input_packet_status": packet["input_packet_status"],
        "packet_status": packet["packet_status"],
        "stages": [{
            "stage_id": stage.get("stage_id"),
            "required_operands": [{
                "role": operand.get("role"),
                "concept_id": operand.get("concept_id"),
                "navigation_row_count": operand.get("navigation_row_count"),
                "period_column_candidate_count": operand.get("period_column_candidate_count"),
                "column_status": operand.get("column_status"),
            } for operand in stage.get("required_operands") or []],
        } for stage in packet.get("stages") or []],
        "source_contract": _candidate_source_contract(),
    }


def materialize_period_column_packets(
    *,
    packets_path: Path,
    packets_manifest_path: Path,
    routes_path: Path,
    routes_manifest_path: Path,
    candidates_path: Path,
    candidates_manifest_path: Path,
    table_roles_path: Path,
    sectors_path: Path,
    routing_catalog_path: Path,
    routing_manifest_path: Path,
    document_metadata_path: Path,
    structured_tables_path: Path,
    structure_manifest_path: Path,
    evidence_context_path: Path,
    evidence_manifest_path: Path,
    output: Path,
    no_candidate_audit_output: Path,
) -> dict[str, Any]:
    """Fail closed on lineage, then materialize packets and a diagnostic sidecar."""
    inputs = validate_inputs(
        packets_path=packets_path, packets_manifest_path=packets_manifest_path,
        routes_path=routes_path, routes_manifest_path=routes_manifest_path,
        candidates_path=candidates_path, candidates_manifest_path=candidates_manifest_path,
        table_roles_path=table_roles_path, sectors_path=sectors_path,
        routing_catalog_path=routing_catalog_path, routing_manifest_path=routing_manifest_path,
        document_metadata_path=document_metadata_path, structured_tables_path=structured_tables_path,
        structure_manifest_path=structure_manifest_path, evidence_context_path=evidence_context_path,
        evidence_manifest_path=evidence_manifest_path,
    )
    period_packets: list[dict[str, Any]] = []
    unreliable_cells = 0
    for packet in inputs.packets:
        built, count = build_period_packet(packet, inputs=inputs)
        period_packets.append(built)
        unreliable_cells += count
    period_packets.sort(key=lambda packet: int(packet["question_id"]))
    ids = [int(packet["question_id"]) for packet in period_packets]
    if len(ids) != len(set(ids)) or ids != sorted(inputs.routes_by_id):
        raise ValueError("Period packet ID coverage mismatch")
    audits, raw_rejections, exclusive_causes = audit_no_candidate_packets(inputs=inputs)
    no_candidate_input_count = sum(packet.get("packet_status") == "no_candidate" for packet in inputs.packets)
    if no_candidate_input_count and not audits:
        raise ValueError("No-candidate audit unexpectedly empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    no_candidate_audit_output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for packet in period_packets:
            handle.write(json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    with no_candidate_audit_output.open("w", encoding="utf-8") as handle:
        for audit in audits:
            handle.write(json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    examples_path = output.with_name(output.stem + ".examples.jsonl")
    examples = [
        _compact_example(packet)
        for status in sorted(PERIOD_PACKET_STATUSES)
        for packet in [packet for packet in period_packets if packet["packet_status"] == status][:20]
    ]
    with examples_path.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    status_counts = Counter(str(packet["packet_status"]) for packet in period_packets)
    input_status_counts = Counter(str(packet["input_packet_status"]) for packet in period_packets)
    operands = [operand for packet in period_packets for stage in packet["stages"] for operand in stage["required_operands"]]
    bounded_operands = [operand for packet in period_packets if packet["input_packet_status"] == "bounded" for stage in packet["stages"] for operand in stage["required_operands"]]
    column_count_distribution = Counter(str(int(operand["period_column_candidate_count"])) for operand in operands)
    manifest = {
        "schema_version": 1,
        "protocol": PERIOD_COLUMN_PROTOCOL,
        "question_count": len(period_packets),
        "question_id_count": len(ids),
        "input_packet_status_counts": dict(sorted(input_status_counts.items())),
        "packet_status_counts": dict(sorted(status_counts.items())),
        "bounded_input_packet_count": input_status_counts.get("bounded", 0),
        "no_candidate_input_packet_count": no_candidate_input_count,
        "route_blocked_input_packet_count": input_status_counts.get("route_blocked", 0),
        "stage_count": sum(len(packet["stages"]) for packet in period_packets),
        "operand_count": len(operands),
        "bounded_operand_count": len(bounded_operands),
        "navigation_row_count": sum(int(operand["navigation_row_count"]) for operand in bounded_operands),
        "period_column_candidate_count": sum(int(operand["period_column_candidate_count"]) for operand in bounded_operands),
        "column_candidate_count_distribution": dict(sorted(column_count_distribution.items(), key=lambda pair: int(pair[0]))),
        "unique_period_column_candidate_count": sum(operand["column_status"] == "unique_period_column_candidate" for operand in bounded_operands),
        "ambiguous_period_columns_count": sum(operand["column_status"] == "ambiguous_period_columns" for operand in bounded_operands),
        "no_period_column_count": sum(operand["column_status"] == "no_period_column" for operand in bounded_operands),
        "unreliable_numeric_source_count": sum(operand["column_status"] == "unreliable_numeric_source" for operand in bounded_operands),
        "unreliable_numeric_source_cell_veto_count": unreliable_cells,
        "raw_rejection_counts": dict(sorted(raw_rejections.items())),
        "exclusive_primary_cause_counts": dict(sorted(exclusive_causes.items())),
        "no_candidate_audit_record_count": len(audits),
        "cap": None,
        "truncated_operand_count": 0,
        "truncated_candidate_omitted_count": 0,
        "example_counts": dict(sorted(Counter(example["packet_status"] for example in examples).items())),
        "inputs": dict(inputs.input_hashes),
        "outputs": {
            "period_packets": {"path": str(output), "sha256": sha256_file(output)},
            "examples": {"path": str(examples_path), "sha256": sha256_file(examples_path)},
            "no_candidate_audit": {"path": str(no_candidate_audit_output), "sha256": sha256_file(no_candidate_audit_output)},
        },
        "source_contract": _candidate_source_contract(),
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_column": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
