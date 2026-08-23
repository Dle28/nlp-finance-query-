"""Bounded, non-promotable navigation packets for question route candidates.

This module only validates immutable research sidecars and copies bounded row
excerpts that satisfy explicit route metadata.  It never chooses a final table,
row, column, or value; executes a formula; or promotes provenance.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROUTE_PACKET_PROTOCOL = "bounded_question_route_packet_v1"
DEFAULT_OPERAND_CANDIDATE_CAP = 20
PRIMARY_STATEMENT_TYPES = frozenset(
    {"balance_sheet", "income_statement", "cash_flow_statement", "equity_change_statement"}
)
AUXILIARY_TABLE_TYPES = frozenset({"other", "schedule"})


def sha256_file(path: Path) -> str:
    """Hash a complete immutable input or output file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL records and reject non-object lines."""
    with path.open(encoding="utf-8-sig") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError(f"{path} must contain JSON object records")
    return records


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _source_contract() -> dict[str, bool]:
    return {
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_execute_formula": False,
        "may_select_value_cell": False,
        "may_compute_answer": False,
        "may_repair_ocr": False,
    }


def _assert_non_promotable(contract: Mapping[str, Any], *, label: str) -> None:
    for key in (
        "evidence_eligible",
        "training_eligible",
        "submission_eligible",
        "promotion_allowed",
    ):
        if bool(contract.get(key, False)):
            raise ValueError(f"{label} improperly enables {key}")


def _manifest_hash(manifest: Mapping[str, Any], *path: str) -> str:
    current: Any = manifest
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            raise ValueError(f"Manifest is missing {'/'.join(path)}")
        current = current[key]
    if not isinstance(current, str) or not current:
        raise ValueError(f"Manifest has invalid hash at {'/'.join(path)}")
    return current


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
        identifier = _record_id(record, field=field, label=label)
        if identifier in index:
            raise ValueError(f"{label} has duplicate {field}: {identifier}")
        index[identifier] = dict(record)
    return index


def _question_id(record: Mapping[str, Any]) -> int:
    value = record.get("question_id")
    if value is None or isinstance(value, bool):
        raise ValueError("Question route has missing or invalid question_id")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Question route has invalid question_id: {value!r}") from exc


def _unique_route_ids(records: Sequence[Mapping[str, Any]]) -> list[int]:
    ids = [_question_id(record) for record in records]
    duplicates = sorted(identifier for identifier, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"Question routes have duplicate IDs: {duplicates}")
    return ids


@dataclass(frozen=True, slots=True)
class NavigationIndex:
    """Validated indexes over immutable candidate and source metadata."""

    candidates_by_concept: Mapping[str, tuple[dict[str, Any], ...]]
    routing_by_uid: Mapping[str, dict[str, Any]]
    document_by_id: Mapping[str, dict[str, Any]]
    table_by_uid: Mapping[str, dict[str, Any]]
    role_by_uid: Mapping[str, dict[str, Any]]
    sector_by_document: Mapping[str, dict[str, Any]]


def build_navigation_index(
    *,
    candidate_rows: Sequence[Mapping[str, Any]],
    routing_rows: Sequence[Mapping[str, Any]],
    document_rows: Sequence[Mapping[str, Any]],
    structured_rows: Sequence[Mapping[str, Any]],
    table_role_rows: Sequence[Mapping[str, Any]],
    sector_rows: Sequence[Mapping[str, Any]],
) -> NavigationIndex:
    """Validate UID/document/row lineage and build exact-concept indexes."""
    routing_by_uid = _unique_index(
        routing_rows, field="internal_table_uid", label="Routing catalog"
    )
    table_by_uid = _unique_index(
        structured_rows, field="internal_table_uid", label="Structured tables"
    )
    if set(routing_by_uid) != set(table_by_uid):
        raise ValueError("Routing catalog and structured tables have UID coverage mismatch")

    document_by_id = _unique_index(
        document_rows, field="document_id", label="Document metadata"
    )
    routing_documents = {
        _record_id(record, field="document_id", label="Routing catalog")
        for record in routing_by_uid.values()
    }
    if routing_documents != set(document_by_id):
        raise ValueError("Routing catalog and document metadata have document ID coverage mismatch")

    role_by_uid = _unique_index(
        table_role_rows, field="internal_table_uid", label="Table-role sidecar"
    )
    if set(role_by_uid) != set(routing_by_uid):
        raise ValueError("Table-role sidecar and routing catalog have UID coverage mismatch")

    sector_by_document = _unique_index(
        sector_rows, field="document_id", label="Sector sidecar"
    )
    if set(sector_by_document) != set(document_by_id):
        raise ValueError("Sector sidecar and document metadata have document ID coverage mismatch")

    for uid, routing in routing_by_uid.items():
        table = table_by_uid[uid]
        document_id = _record_id(routing, field="document_id", label="Routing catalog")
        if _record_id(table, field="document_id", label="Structured table") != document_id:
            raise ValueError(f"Structured table document mismatch for UID {uid}")
        metadata = document_by_id[document_id]
        for field in ("company", "report_scope", "report_year"):
            if routing.get(field) != metadata.get(field):
                raise ValueError(f"Routing/document metadata mismatch for {field} at UID {uid}")
        role = role_by_uid[uid]
        if _record_id(role, field="document_id", label="Table-role sidecar") != document_id:
            raise ValueError(f"Table-role document mismatch for UID {uid}")
        if role.get("existing_table_type") != routing.get("table_type"):
            raise ValueError(f"Table-role table type mismatch for UID {uid}")
        if role.get("existing_table_type_status") != routing.get("table_type_status"):
            raise ValueError(f"Table-role table type status mismatch for UID {uid}")

    coordinates: set[tuple[str, int]] = set()
    candidates_by_concept: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidate_rows:
        uid = _record_id(candidate, field="internal_table_uid", label="Taxonomy candidate")
        if uid not in table_by_uid:
            raise ValueError(f"Taxonomy candidate references unknown UID {uid}")
        document_id = _record_id(candidate, field="document_id", label="Taxonomy candidate")
        if document_id != table_by_uid[uid].get("document_id"):
            raise ValueError(f"Taxonomy candidate document mismatch for UID {uid}")
        row_index = candidate.get("row_index")
        if isinstance(row_index, bool):
            raise ValueError(f"Taxonomy candidate has invalid row_index for UID {uid}")
        try:
            row_index = int(row_index)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Taxonomy candidate has invalid row_index for UID {uid}") from exc
        rows = table_by_uid[uid].get("rows") or []
        if row_index < 0 or row_index >= len(rows):
            raise ValueError(f"Taxonomy candidate row_index is out of range for UID {uid}")
        exact_raw_row = [str(value) for value in rows[row_index]]
        if list(candidate.get("raw_source_row") or []) != exact_raw_row:
            raise ValueError(f"Taxonomy candidate raw source row mismatch for UID {uid}, row {row_index}")
        coordinate = (uid, row_index)
        if coordinate in coordinates:
            raise ValueError(f"Taxonomy candidate has duplicate source coordinate {coordinate}")
        coordinates.add(coordinate)
        if candidate.get("table_type") != routing_by_uid[uid].get("table_type"):
            raise ValueError(f"Taxonomy candidate table type mismatch for UID {uid}")
        if candidate.get("table_type_status") != routing_by_uid[uid].get("table_type_status"):
            raise ValueError(f"Taxonomy candidate table type status mismatch for UID {uid}")
        _assert_non_promotable(
            candidate.get("source_contract") or {}, label="Taxonomy candidate source contract"
        )

        if str(candidate.get("match_status") or "") != "exact_unique":
            continue
        concept_candidates = list(candidate.get("concept_candidates") or [])
        if len(concept_candidates) != 1:
            raise ValueError(f"Exact-unique candidate has invalid concept count at {coordinate}")
        concept_id = str(concept_candidates[0].get("concept_id") or "").strip()
        if not concept_id:
            raise ValueError(f"Exact-unique candidate has missing concept at {coordinate}")
        candidates_by_concept[concept_id].append(dict(candidate))

    frozen_candidates = {
        concept_id: tuple(
            sorted(
                values,
                key=lambda item: (
                    str(item.get("document_id") or ""),
                    str(item.get("internal_table_uid") or ""),
                    int(item.get("row_index") or 0),
                ),
            )
        )
        for concept_id, values in candidates_by_concept.items()
    }
    return NavigationIndex(
        candidates_by_concept=frozen_candidates,
        routing_by_uid=routing_by_uid,
        document_by_id=document_by_id,
        table_by_uid=table_by_uid,
        role_by_uid=role_by_uid,
        sector_by_document=sector_by_document,
    )


def _route_context_blockers(route: Mapping[str, Any]) -> list[str]:
    context = route.get("question_context") or {}
    blockers: list[str] = []
    if not isinstance(context.get("entities"), list) or not context.get("entities"):
        blockers.append("MISSING_ENTITY_CONTEXT")
    if not isinstance(context.get("years"), list) or not context.get("years"):
        blockers.append("MISSING_YEAR_CONTEXT")
    if not str(context.get("scope") or "").strip():
        blockers.append("MISSING_SCOPE_CONTEXT")
    return blockers


def _empty_operand(operand: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": str(operand.get("role") or ""),
        "concept_id": str(operand.get("concept_id") or ""),
        "concept_path": list(operand.get("concept_path") or []),
        "period_type": operand.get("period_type"),
        "expected_table_types": list(operand.get("statement_types") or []),
        "candidate_count_total": 0,
        "candidate_count_in_packet": 0,
        "truncated": False,
        "navigation_candidates": [],
        "blocked_candidate_reason_counts": {},
    }


def _empty_stages(route: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "stage_id": str(stage.get("stage_id") or ""),
            "route_kind": stage.get("route_kind"),
            "metric_id": stage.get("metric_id"),
            "concept_id": stage.get("concept_id"),
            "required_operands": [_empty_operand(operand) for operand in stage.get("required_operands") or []],
        }
        for stage in route.get("stages") or []
    ]


def _candidate_blockers(
    *,
    candidate: Mapping[str, Any],
    index: NavigationIndex,
    entities: set[str],
    years: set[int],
    scope: str,
    allowed_table_types: set[str],
    allowed_sectors: set[str],
) -> tuple[list[str], str | None]:
    uid = str(candidate["internal_table_uid"])
    routing = index.routing_by_uid[uid]
    metadata = index.document_by_id[str(candidate["document_id"])]
    role = index.role_by_uid[uid]
    source_structural_account_code_fallback = _may_use_unknown_sector_fallback(
        candidate=candidate,
        routing=routing,
        allowed_sectors=allowed_sectors,
        observed_sector=str(
            index.sector_by_document[str(candidate["document_id"])].get("sector") or "unknown"
        ),
    )
    blockers: list[str] = []
    if str(metadata.get("company") or "") not in entities:
        blockers.append("ENTITY_MISMATCH")
    report_year = metadata.get("report_year")
    available_years = {
        int(year)
        for year in routing.get("available_period_years") or []
        if not isinstance(year, bool) and str(year).strip()
    }
    if report_year in years:
        period_match_basis: str | None = "report_year"
    elif years.intersection(available_years):
        period_match_basis = "available_period_year"
    else:
        period_match_basis = None
        blockers.append("YEAR_MISMATCH")
    if str(metadata.get("report_scope") or "") != scope:
        blockers.append("SCOPE_MISMATCH")
    table_type = str(routing.get("table_type") or "")
    if table_type not in allowed_table_types:
        blockers.append("TABLE_TYPE_MISMATCH")
    if table_type in AUXILIARY_TABLE_TYPES:
        blockers.append("AUXILIARY_OR_RESTATEMENT_TABLE_VETO")
    if not bool(routing.get("routing_eligible")):
        blockers.append("TABLE_NOT_ROUTING_ELIGIBLE")
    if (
        str(candidate.get("navigation_gate_status") or "") != "ready"
        and not source_structural_account_code_fallback
    ):
        blockers.append("NAVIGATION_GATE_NOT_READY")
    if allowed_sectors:
        sector = str(index.sector_by_document[str(candidate["document_id"])].get("sector") or "unknown")
        if sector not in allowed_sectors and not source_structural_account_code_fallback:
            blockers.append("SECTOR_MISMATCH")
    # Never use a semantic proposal to transform an other/note/schedule table
    # into a primary statement. The catalog's existing type is the filter.
    if (
        str(role.get("existing_table_type") or "") in AUXILIARY_TABLE_TYPES
        and str(role.get("proposed_table_type") or "") in PRIMARY_STATEMENT_TYPES
    ):
        blockers.append("AUXILIARY_OR_RESTATEMENT_TABLE_ROLE_VETO")
    return sorted(set(blockers)), period_match_basis


def _may_use_unknown_sector_fallback(
    *,
    candidate: Mapping[str, Any],
    routing: Mapping[str, Any],
    allowed_sectors: set[str],
    observed_sector: str,
) -> bool:
    """Allow only a code-disambiguated primary-statement row past unknown sector.

    A document-level sector candidate is optional navigation metadata.  It must
    not suppress a literal chart-of-accounts row when all source-structural
    gates already identify the concept.  This is deliberately *not* a sector
    inference: it applies only to an unknown sector, never to a known
    disagreement, and only when the taxonomy record explicitly used a literal
    account-code constraint to obtain its unique concept.
    """
    if not allowed_sectors or observed_sector != "unknown":
        return False
    if str(candidate.get("match_status") or "") != "exact_unique":
        return False
    concepts = list(candidate.get("concept_candidates") or [])
    if len(concepts) != 1 or "account_code" not in (concepts[0].get("constraints_applied") or []):
        return False
    if not any(str(code).strip() for code in candidate.get("source_account_codes") or []):
        return False
    if str(routing.get("table_type") or "") not in PRIMARY_STATEMENT_TYPES:
        return False
    if str(routing.get("table_type_status") or "") != "source_structural":
        return False
    if not bool(routing.get("routing_eligible")):
        return False
    return set(candidate.get("navigation_reason_codes") or []) == {"sector_unresolved"}


def _navigation_candidate(
    *,
    candidate: Mapping[str, Any],
    index: NavigationIndex,
    period_match_basis: str,
    admission_basis: str,
) -> dict[str, Any]:
    """Copy a bounded excerpt with source coordinates, never a selected value."""
    uid = str(candidate["internal_table_uid"])
    routing = index.routing_by_uid[uid]
    metadata = index.document_by_id[str(candidate["document_id"])]
    role = index.role_by_uid[uid]
    return {
        "internal_table_uid": uid,
        "document_id": str(candidate["document_id"]),
        "company": metadata.get("company"),
        "report_year": metadata.get("report_year"),
        "available_period_years": list(routing.get("available_period_years") or []),
        "period_match_basis": period_match_basis,
        "report_scope": metadata.get("report_scope"),
        "table_type": routing.get("table_type"),
        "table_type_status": routing.get("table_type_status"),
        "routing_eligible": bool(routing.get("routing_eligible")),
        "navigation_gate_status": candidate.get("navigation_gate_status"),
        "navigation_reason_codes": list(candidate.get("navigation_reason_codes") or []),
        "navigation_admission_basis": admission_basis,
        "table_role_status": role.get("status"),
        "table_role_reason_codes": list(role.get("reason_codes") or []),
        "table_role_existing_type": role.get("existing_table_type"),
        "table_role_proposed_type": role.get("proposed_table_type"),
        "sector_candidate": index.sector_by_document[str(candidate["document_id"])].get("sector"),
        "row_index": int(candidate["row_index"]),
        "raw_source_row": list(candidate["raw_source_row"]),
        "candidate_blocker_reason_codes": [],
        "source_contract": _source_contract(),
    }


def _operand_packet(
    *,
    operand: Mapping[str, Any],
    stage: Mapping[str, Any],
    context: Mapping[str, Any],
    index: NavigationIndex,
    cap: int,
) -> dict[str, Any]:
    result = _empty_operand(operand)
    concept_id = result["concept_id"]
    allowed_table_types = {str(value) for value in result["expected_table_types"] if str(value)}
    entities = {str(value) for value in context.get("entities") or [] if str(value)}
    years = {
        int(value)
        for value in context.get("years") or []
        if not isinstance(value, bool) and str(value).strip()
    }
    scope = str(context.get("scope") or "")
    allowed_sectors = {
        str(value)
        for value in (stage.get("retrieval_filters") or {}).get("sectors") or []
        if str(value)
    }
    if not concept_id:
        result["blocked_candidate_reason_counts"] = {"MISSING_CONCEPT_CONTRACT": 1}
        return result
    if not allowed_table_types:
        result["blocked_candidate_reason_counts"] = {"MISSING_TABLE_TYPE_CONTRACT": 1}
        return result

    accepted: list[dict[str, Any]] = []
    blocked_counts: Counter[str] = Counter()
    for candidate in index.candidates_by_concept.get(concept_id, ()):
        uid = str(candidate["internal_table_uid"])
        observed_sector = str(
            index.sector_by_document[str(candidate["document_id"])].get("sector") or "unknown"
        )
        fallback = _may_use_unknown_sector_fallback(
            candidate=candidate,
            routing=index.routing_by_uid[uid],
            allowed_sectors=allowed_sectors,
            observed_sector=observed_sector,
        )
        blockers, period_match_basis = _candidate_blockers(
            candidate=candidate,
            index=index,
            entities=entities,
            years=years,
            scope=scope,
            allowed_table_types=allowed_table_types,
            allowed_sectors=allowed_sectors,
        )
        if blockers:
            blocked_counts.update(blockers)
            continue
        if period_match_basis is None:
            raise AssertionError("Accepted navigation candidate lacks a period match basis")
        accepted.append(
            _navigation_candidate(
                candidate=candidate,
                index=index,
                period_match_basis=period_match_basis,
                admission_basis=(
                    "source_structural_account_code_unknown_sector_fallback"
                    if fallback
                    else "strict_navigation_gate"
                ),
            )
        )
    accepted.sort(
        key=lambda item: (
            str(item["document_id"]),
            str(item["internal_table_uid"]),
            int(item["row_index"]),
        )
    )
    result["candidate_count_total"] = len(accepted)
    result["candidate_count_in_packet"] = min(len(accepted), cap)
    result["truncated"] = len(accepted) > cap
    result["navigation_candidates"] = accepted[:cap]
    result["blocked_candidate_reason_counts"] = dict(sorted(blocked_counts.items()))
    return result


def _no_candidate_feedback(
    stages: Sequence[Mapping[str, Any]], *, context: Mapping[str, Any]
) -> dict[str, Any]:
    missing_operands: list[dict[str, Any]] = []
    for stage in stages:
        for operand in stage.get("required_operands") or []:
            if int(operand.get("candidate_count_total") or 0) != 0:
                continue
            blockers = dict(operand.get("blocked_candidate_reason_counts") or {})
            if not blockers:
                blockers = {"NO_EXACT_UNIQUE_CONCEPT_CANDIDATE": 1}
            missing_operands.append(
                {
                    "stage_id": stage.get("stage_id"),
                    "role": operand.get("role"),
                    "concept_id": operand.get("concept_id"),
                    "expected_table_types": list(operand.get("expected_table_types") or []),
                    "required_context": {
                        "entities": list(context.get("entities") or []),
                        "years": list(context.get("years") or []),
                        "scope": context.get("scope"),
                    },
                    "blocked_candidate_reason_counts": blockers,
                }
            )
    return {
        "status": "no_candidate",
        "missing_operands": missing_operands,
        "suggested_next_action": (
            "audit the source-bound taxonomy, table-role, and context lineage; do not broaden "
            "entity, year, scope, table type, or substitute a candidate as evidence"
        ),
        "evidence_invention_allowed": False,
    }


def build_route_packet(
    route: Mapping[str, Any], *, index: NavigationIndex, cap: int = DEFAULT_OPERAND_CANDIDATE_CAP
) -> dict[str, Any]:
    """Create one bounded packet from one already-hash-validated route record."""
    if cap <= 0:
        raise ValueError("Operand candidate cap must be positive")
    question_id = _question_id(route)
    route_status = str(route.get("route_status") or "")
    context = dict(route.get("question_context") or {})
    stages = _empty_stages(route)
    blockers = _route_context_blockers(route)
    if route_status == "abstain":
        blockers.insert(0, "ROUTE_STATUS_ABSTAIN")
    if route_status not in {"abstain", "concept_lookup_candidate", "metric_candidate", "staged_candidate"}:
        blockers.append("UNSUPPORTED_ROUTE_STATUS")
    if blockers:
        route_feedback = dict(route.get("feedback") or {})
        return {
            "schema_version": 1,
            "protocol": ROUTE_PACKET_PROTOCOL,
            "question_id": question_id,
            "question": str(route.get("question") or ""),
            "packet_status": "route_blocked",
            "route_status": route_status,
            "question_context": context,
            "stages": stages,
            "feedback": {
                "status": "route_blocked",
                "reason_codes": sorted(set(blockers)),
                "missing_contract": list(route_feedback.get("missing_contract") or []),
                "suggested_next_action": route_feedback.get("suggested_next_action")
                or "supply the missing source-bound route contract; do not infer context from candidate rows",
                "evidence_invention_allowed": False,
            },
            "source_contract": _source_contract(),
        }

    packet_stages: list[dict[str, Any]] = []
    for source_stage in route.get("stages") or []:
        stage = {
            "stage_id": str(source_stage.get("stage_id") or ""),
            "route_kind": source_stage.get("route_kind"),
            "metric_id": source_stage.get("metric_id"),
            "concept_id": source_stage.get("concept_id"),
            "required_operands": [
                _operand_packet(
                    operand=operand,
                    stage=source_stage,
                    context=context,
                    index=index,
                    cap=cap,
                )
                for operand in source_stage.get("required_operands") or []
            ],
        }
        packet_stages.append(stage)
    operands = [
        operand
        for stage in packet_stages
        for operand in stage.get("required_operands") or []
    ]
    if not operands:
        packet_status = "no_candidate"
        feedback = {
            "status": "no_candidate",
            "missing_operands": [],
            "reason_codes": ["ROUTE_HAS_NO_OPERANDS"],
            "suggested_next_action": "audit the source-bound route contract; do not infer operands",
            "evidence_invention_allowed": False,
        }
    elif any(int(operand["candidate_count_total"]) == 0 for operand in operands):
        packet_status = "no_candidate"
        feedback = _no_candidate_feedback(packet_stages, context=context)
    else:
        packet_status = "bounded"
        feedback = None
    return {
        "schema_version": 1,
        "protocol": ROUTE_PACKET_PROTOCOL,
        "question_id": question_id,
        "question": str(route.get("question") or ""),
        "packet_status": packet_status,
        "route_status": route_status,
        "question_context": context,
        "stages": packet_stages,
        "feedback": feedback,
        "source_contract": _source_contract(),
    }


def _validate_manifests_and_load_index(
    *,
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
) -> tuple[list[dict[str, Any]], NavigationIndex, dict[str, dict[str, Any]]]:
    """Fail closed before materialization when any declared lineage differs."""
    route_manifest = load_json(routes_manifest_path)
    candidate_manifest = load_json(candidates_manifest_path)
    routing_manifest = load_json(routing_manifest_path)
    structure_manifest = load_json(structure_manifest_path)

    _require_hash(
        routes_path, _manifest_hash(route_manifest, "output", "sha256"), label="question routes"
    )
    _assert_non_promotable(route_manifest.get("source_contract") or {}, label="Route manifest")
    _assert_non_promotable(route_manifest, label="Route manifest top-level flags")
    _require_hash(
        candidates_path,
        _manifest_hash(candidate_manifest, "outputs", "row_candidates", "sha256"),
        label="taxonomy candidates",
    )
    _require_hash(
        table_roles_path,
        _manifest_hash(candidate_manifest, "outputs", "table_role_candidates", "sha256"),
        label="table-role sidecar",
    )
    _require_hash(
        sectors_path,
        _manifest_hash(candidate_manifest, "outputs", "sector_candidates", "sha256"),
        label="sector sidecar",
    )
    _assert_non_promotable(
        candidate_manifest.get("source_contract") or {}, label="Candidate manifest"
    )
    _assert_non_promotable(candidate_manifest, label="Candidate manifest top-level flags")
    _require_hash(
        routing_catalog_path,
        _manifest_hash(candidate_manifest, "inputs", "routing_catalog", "sha256"),
        label="routing catalog against candidate manifest",
    )
    _require_hash(
        structured_tables_path,
        _manifest_hash(candidate_manifest, "inputs", "structured_tables", "sha256"),
        label="structured tables against candidate manifest",
    )
    route_taxonomy_hash = _manifest_hash(route_manifest, "inputs", "taxonomy", "sha256")
    if route_taxonomy_hash != _manifest_hash(candidate_manifest, "inputs", "taxonomy", "sha256"):
        raise ValueError("Question routes and taxonomy candidates use different taxonomy hashes")

    _require_hash(
        routing_catalog_path,
        _manifest_hash(routing_manifest, "table_catalog_sha256"),
        label="routing catalog against routing manifest",
    )
    _require_hash(
        document_metadata_path,
        _manifest_hash(routing_manifest, "document_metadata_sha256"),
        label="document metadata against routing manifest",
    )
    _require_hash(
        structured_tables_path,
        _manifest_hash(routing_manifest, "input_structure_sha256"),
        label="structured tables against routing manifest",
    )
    _require_hash(
        structured_tables_path,
        _manifest_hash(structure_manifest, "sidecar_sha256"),
        label="structured tables against V2 manifest",
    )

    routes = load_jsonl(routes_path)
    route_ids = _unique_route_ids(routes)
    if int(route_manifest.get("question_count") or -1) != len(routes):
        raise ValueError("Question route manifest question_count mismatch")
    if int(route_manifest.get("question_id_count") or -1) != len(route_ids):
        raise ValueError("Question route manifest question_id_count mismatch")
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("Question routes have duplicate IDs")
    for route in routes:
        _assert_non_promotable(route.get("source_contract") or {}, label="Question route")

    candidates = load_jsonl(candidates_path)
    roles = load_jsonl(table_roles_path)
    sectors = load_jsonl(sectors_path)
    routing = load_jsonl(routing_catalog_path)
    documents = load_jsonl(document_metadata_path)
    tables = load_jsonl(structured_tables_path)
    if int(candidate_manifest.get("candidate_record_count") or -1) != len(candidates):
        raise ValueError("Candidate manifest candidate_record_count mismatch")
    if int(candidate_manifest.get("table_count") or -1) != len(tables):
        raise ValueError("Candidate manifest table_count mismatch")
    if int(candidate_manifest.get("document_count") or -1) != len(documents):
        raise ValueError("Candidate manifest document_count mismatch")
    if int(routing_manifest.get("table_count") or -1) != len(tables):
        raise ValueError("Routing manifest table_count mismatch")
    if int(routing_manifest.get("document_count") or -1) != len(documents):
        raise ValueError("Routing manifest document_count mismatch")
    if int(structure_manifest.get("table_count") or -1) != len(tables):
        raise ValueError("V2 manifest table_count mismatch")

    index = build_navigation_index(
        candidate_rows=candidates,
        routing_rows=routing,
        document_rows=documents,
        structured_rows=tables,
        table_role_rows=roles,
        sector_rows=sectors,
    )
    manifests = {
        "routes": route_manifest,
        "candidates": candidate_manifest,
        "routing": routing_manifest,
        "structure": structure_manifest,
    }
    return routes, index, manifests


def _compact_example(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Export a deterministic packet example without adding candidate selection."""
    return {
        "question_id": packet["question_id"],
        "packet_status": packet["packet_status"],
        "route_status": packet["route_status"],
        "feedback": packet.get("feedback"),
        "stages": [
            {
                "stage_id": stage.get("stage_id"),
                "required_operands": [
                    {
                        "role": operand.get("role"),
                        "concept_id": operand.get("concept_id"),
                        "candidate_count_total": operand.get("candidate_count_total"),
                        "candidate_count_in_packet": operand.get("candidate_count_in_packet"),
                        "truncated": operand.get("truncated"),
                        "blocked_candidate_reason_counts": operand.get(
                            "blocked_candidate_reason_counts"
                        ),
                    }
                    for operand in stage.get("required_operands") or []
                ],
            }
            for stage in packet.get("stages") or []
        ],
        "source_contract": _source_contract(),
    }


def materialize_route_packets(
    *,
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
    output: Path,
    cap: int = DEFAULT_OPERAND_CANDIDATE_CAP,
) -> dict[str, Any]:
    """Validate all lineage, then materialize one bounded packet per route ID."""
    if cap <= 0:
        raise ValueError("Operand candidate cap must be positive")
    routes, index, _manifests = _validate_manifests_and_load_index(
        routes_path=routes_path,
        routes_manifest_path=routes_manifest_path,
        candidates_path=candidates_path,
        candidates_manifest_path=candidates_manifest_path,
        table_roles_path=table_roles_path,
        sectors_path=sectors_path,
        routing_catalog_path=routing_catalog_path,
        routing_manifest_path=routing_manifest_path,
        document_metadata_path=document_metadata_path,
        structured_tables_path=structured_tables_path,
        structure_manifest_path=structure_manifest_path,
    )
    packets = [build_route_packet(route, index=index, cap=cap) for route in routes]
    packets.sort(key=lambda packet: int(packet["question_id"]))
    packet_ids = [int(packet["question_id"]) for packet in packets]
    route_ids = sorted(_unique_route_ids(routes))
    if packet_ids != route_ids or len(packet_ids) != len(set(packet_ids)):
        raise ValueError("Packet materialization did not preserve exactly one record per route ID")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for packet in packets:
            handle.write(
                json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            )

    status_counts = Counter(str(packet["packet_status"]) for packet in packets)
    route_status_counts = Counter(str(packet["route_status"]) for packet in packets)
    operands = [
        operand
        for packet in packets
        for stage in packet.get("stages") or []
        for operand in stage.get("required_operands") or []
    ]
    route_ready_packets = [packet for packet in packets if packet["route_status"] != "abstain"]
    route_ready_operands = [
        operand
        for packet in route_ready_packets
        for stage in packet.get("stages") or []
        for operand in stage.get("required_operands") or []
    ]
    no_candidate_reason_counts: Counter[str] = Counter()
    for packet in packets:
        if packet["packet_status"] != "no_candidate":
            continue
        for missing in (packet.get("feedback") or {}).get("missing_operands") or []:
            no_candidate_reason_counts.update(
                str(reason)
                for reason in (missing.get("blocked_candidate_reason_counts") or {})
            )
    candidate_distribution = Counter(
        str(int(operand.get("candidate_count_total") or 0)) for operand in operands
    )
    truncated_operands = [operand for operand in operands if bool(operand.get("truncated"))]
    examples_path = output.with_name(output.stem + ".examples.jsonl")
    # Keep at most twenty per status while preserving ascending question IDs.
    selected_examples = [
        _compact_example(packet)
        for status in ("bounded", "no_candidate", "route_blocked")
        for packet in [
            packet for packet in packets if packet["packet_status"] == status
        ][:20]
    ]
    with examples_path.open("w", encoding="utf-8") as handle:
        for example in selected_examples:
            handle.write(
                json.dumps(example, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            )

    route_ready_operand_count = len(route_ready_operands)
    route_ready_operands_with_candidate = sum(
        1 for operand in route_ready_operands if int(operand.get("candidate_count_total") or 0) > 0
    )
    manifest = {
        "schema_version": 1,
        "protocol": ROUTE_PACKET_PROTOCOL,
        "question_count": len(packets),
        "question_id_count": len(packet_ids),
        "route_ready_question_count": len(route_ready_packets),
        "packet_status_counts": dict(sorted(status_counts.items())),
        "route_status_counts": dict(sorted(route_status_counts.items())),
        "stage_count": sum(len(packet.get("stages") or []) for packet in packets),
        "operand_count": len(operands),
        "route_ready_operand_count": route_ready_operand_count,
        "route_ready_operands_with_candidate": route_ready_operands_with_candidate,
        "operand_candidate_recall_proxy": (
            route_ready_operands_with_candidate / route_ready_operand_count
            if route_ready_operand_count
            else 0.0
        ),
        "candidate_count_distribution": dict(
            sorted(candidate_distribution.items(), key=lambda pair: int(pair[0]))
        ),
        "candidate_count_total": sum(int(operand.get("candidate_count_total") or 0) for operand in operands),
        "candidate_count_in_packet": sum(
            int(operand.get("candidate_count_in_packet") or 0) for operand in operands
        ),
        "truncated_operand_count": len(truncated_operands),
        "truncated_candidate_omitted_count": sum(
            int(operand["candidate_count_total"]) - int(operand["candidate_count_in_packet"])
            for operand in truncated_operands
        ),
        "no_candidate_reason_counts": dict(sorted(no_candidate_reason_counts.items())),
        "example_counts": dict(
            sorted(Counter(example["packet_status"] for example in selected_examples).items())
        ),
        "inputs": {
            "question_routes": {"path": str(routes_path), "sha256": sha256_file(routes_path)},
            "question_routes_manifest": {
                "path": str(routes_manifest_path),
                "sha256": sha256_file(routes_manifest_path),
            },
            "taxonomy_candidates": {"path": str(candidates_path), "sha256": sha256_file(candidates_path)},
            "taxonomy_candidates_manifest": {
                "path": str(candidates_manifest_path),
                "sha256": sha256_file(candidates_manifest_path),
            },
            "table_roles": {"path": str(table_roles_path), "sha256": sha256_file(table_roles_path)},
            "sectors": {"path": str(sectors_path), "sha256": sha256_file(sectors_path)},
            "routing_catalog": {
                "path": str(routing_catalog_path),
                "sha256": sha256_file(routing_catalog_path),
            },
            "routing_manifest": {
                "path": str(routing_manifest_path),
                "sha256": sha256_file(routing_manifest_path),
            },
            "document_metadata": {
                "path": str(document_metadata_path),
                "sha256": sha256_file(document_metadata_path),
            },
            "structured_tables": {
                "path": str(structured_tables_path),
                "sha256": sha256_file(structured_tables_path),
            },
            "structure_manifest": {
                "path": str(structure_manifest_path),
                "sha256": sha256_file(structure_manifest_path),
            },
        },
        "outputs": {
            "packets": {"path": str(output), "sha256": sha256_file(output)},
            "examples": {"path": str(examples_path), "sha256": sha256_file(examples_path)},
        },
        "source_contract": _source_contract(),
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
