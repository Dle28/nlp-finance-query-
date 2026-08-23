"""Fail-closed review queues for grounded financial-routing blockers.

The module diagnoses immutable candidate packets.  It never updates document
metadata, taxonomy, routing, period packets, or a provenance state.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


GROUNDING_ADJUDICATION_PROTOCOL = "grounding_adjudication_queues_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _require_hash(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"Missing SHA-256 for {label}")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")


def _unique(rows: Sequence[Mapping[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(field) or "").strip()
        if not value or value in result:
            raise ValueError(f"{label} has missing or duplicate {field}: {value!r}")
        result[value] = dict(row)
    return result


def _source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _decision_contract() -> dict[str, Any]:
    return {
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": None,
        "reviewed_at": None,
        "source_coordinates_checked": False,
        "proposed_patch": None,
        "eligible_for_materialization": False,
    }


def _audit_identity(audit: Mapping[str, Any], audit_sha: str) -> dict[str, Any]:
    return {
        "audit_sha256": audit_sha,
        "audit_item_sha256": canonical_sha256(audit),
        "question_id": audit.get("question_id"),
        "stage_id": audit.get("stage_id"),
        "role": audit.get("role"),
        "concept_id": audit.get("concept_id"),
    }


def _nearby_with_sources(
    audit: Mapping[str, Any], *, tables: Mapping[str, Mapping[str, Any]], routing: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for nearby in audit.get("nearby_exact_concept_candidates") or []:
        uid = str(nearby.get("internal_table_uid") or "")
        row_index = nearby.get("row_index")
        if uid not in tables or isinstance(row_index, bool) or not isinstance(row_index, int):
            raise ValueError("Audit nearby candidate has missing V2 identity")
        rows = tables[uid].get("rows") or []
        if row_index < 0 or row_index >= len(rows):
            raise ValueError("Audit nearby candidate row index is out of bounds")
        values.append(
            {
                "internal_table_uid": uid,
                "document_id": tables[uid].get("document_id"),
                "row_index": row_index,
                "raw_source_row": list(rows[row_index]),
                "gate_vector": nearby.get("gate_vector") or {},
                "failure_codes": list(nearby.get("failure_codes") or []),
                "routing_snapshot": {
                    key: routing.get(uid, {}).get(key)
                    for key in ("table_type", "table_type_status", "routing_eligible", "available_period_years")
                },
            }
        )
    return values


def _sector_diagnosis(audit: Mapping[str, Any], nearby: Sequence[Mapping[str, Any]], documents: Sequence[Mapping[str, Any]]) -> str:
    observed = {
        str((item.get("gate_vector") or {}).get("sector", {}).get("observed") or "")
        for item in nearby
    }
    expected = {
        str(value)
        for item in nearby
        for value in ((item.get("gate_vector") or {}).get("sector", {}).get("expected") or [])
    }
    if observed and observed == {"unknown"}:
        return "CANDIDATE_SECTOR_UNKNOWN"
    target_sectors = {str(row.get("sector") or "unknown") for row in documents}
    if documents and target_sectors == {"unknown"}:
        return "TARGET_DOCUMENT_SECTOR_UNKNOWN"
    non_unknown = {value for value in observed if value and value != "unknown"}
    if len(non_unknown) > 1 or (expected and non_unknown and not non_unknown.intersection(expected)):
        return "CONFLICTING_SECTOR_SOURCES"
    return "GENUINE_SECTOR_MISMATCH"


def _entity_diagnosis(audit: Mapping[str, Any], nearby: Sequence[Mapping[str, Any]], documents: Sequence[Mapping[str, Any]]) -> str:
    entities = {
        str(value)
        for item in nearby
        for value in ((item.get("gate_vector") or {}).get("entity", {}).get("expected") or [])
    }
    if not documents:
        return "TARGET_DOCUMENT_ABSENT"
    observed = {str((item.get("gate_vector") or {}).get("entity", {}).get("observed") or "") for item in nearby}
    if entities and observed and not observed.intersection(entities):
        return "ENTITY_ALIAS_CONFLICT"
    if int(audit.get("exact_concept_row_count") or 0) == 0:
        return "EXACT_CONCEPT_ABSENT_FOR_ENTITY"
    return "TARGET_ROW_UNMAPPED"


def _year_diagnosis(audit: Mapping[str, Any], documents: Sequence[Mapping[str, Any]]) -> str:
    if not documents:
        return "TARGET_YEAR_DOCUMENT_ABSENT"
    available = {
        int(value)
        for row in documents
        for value in row.get("available_period_years", [])
        if isinstance(value, int)
    }
    return "TARGET_YEAR_PERIOD_UNANCHORED" if not available else "COMPARATIVE_OR_HEADER_YEAR_REVIEW"


def build_grounding_adjudication_queues(
    *,
    period_packets_path: Path,
    period_manifest_path: Path,
    no_candidate_audit_path: Path,
    document_metadata_path: Path,
    routing_catalog_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build blank-decision queues after validating all hash-bound inputs."""
    manifest = load_json(period_manifest_path)
    outputs = manifest.get("outputs") or {}
    _require_hash(period_packets_path, (outputs.get("period_packets") or {}).get("sha256"), "period packets")
    _require_hash(no_candidate_audit_path, (outputs.get("no_candidate_audit") or {}).get("sha256"), "no-candidate audit")
    for key, path, label in (
        ("document_metadata", document_metadata_path, "document metadata"),
        ("routing_catalog", routing_catalog_path, "routing catalog"),
        ("structured_tables_v2", structured_tables_path, "structured tables V2"),
        ("evidence_context_v3", evidence_context_path, "evidence context V3"),
    ):
        _require_hash(path, ((manifest.get("inputs") or {}).get(key) or {}).get("sha256"), label)

    packets = load_jsonl(period_packets_path)
    audits = load_jsonl(no_candidate_audit_path)
    tables = _unique(load_jsonl(structured_tables_path), "internal_table_uid", "V2 tables")
    contexts = _unique(load_jsonl(evidence_context_path), "internal_table_uid", "V3 contexts")
    routing = _unique(load_jsonl(routing_catalog_path), "internal_table_uid", "routing catalog")
    documents = _unique(load_jsonl(document_metadata_path), "document_id", "document metadata")
    if set(tables) != set(contexts) or set(tables) != set(routing):
        raise ValueError("V2/V3/routing table UID coverage mismatch")
    packet_ids = [int(row.get("question_id") or 0) for row in packets]
    if len(packet_ids) != len(set(packet_ids)) or set(packet_ids) != set(range(1, 1013)):
        raise ValueError("Period packet question ID coverage mismatch")
    if len(audits) != 91:
        raise ValueError("No-candidate audit coverage mismatch")
    audit_keys = [(item.get("question_id"), item.get("stage_id"), item.get("role")) for item in audits]
    if len(audit_keys) != len(set(audit_keys)):
        raise ValueError("No-candidate audit has duplicate operand identity")

    audit_sha = sha256_file(no_candidate_audit_path)
    metadata: list[dict[str, Any]] = []
    routing_queue: list[dict[str, Any]] = []
    for audit in audits:
        cause = str(audit.get("exclusive_primary_cause") or "")
        nearby = _nearby_with_sources(audit, tables=tables, routing=routing)
        expected_entities = {
            str(value)
            for item in nearby
            for value in ((item.get("gate_vector") or {}).get("entity", {}).get("expected") or [])
        }
        docs_for_target = [
            row for row in documents.values()
            if not expected_entities or str(row.get("company") or "") in expected_entities
        ]
        common = {
            "schema_version": 1,
            "protocol": GROUNDING_ADJUDICATION_PROTOCOL,
            "immutable_source_identity": _audit_identity(audit, audit_sha),
            "exclusive_primary_cause": cause,
            "minimal_blocker_sets": list(audit.get("minimal_blocker_sets") or []),
            "nearby_exact_concept_candidates": nearby,
            "decision_contract": _decision_contract(),
            "source_contract": _source_contract(),
        }
        if cause in {"SECTOR", "ENTITY", "YEAR"}:
            if cause == "SECTOR":
                diagnosis = _sector_diagnosis(audit, nearby, docs_for_target)
                queue_kind = "sector_metadata"
            elif cause == "ENTITY":
                diagnosis = _entity_diagnosis(audit, nearby, docs_for_target)
                queue_kind = "entity_metadata"
            else:
                diagnosis = _year_diagnosis(audit, docs_for_target)
                queue_kind = "year_metadata"
            metadata.append({
                **common,
                "queue_kind": queue_kind,
                "diagnosis": diagnosis,
                "target_document_candidates": [
                    {key: document.get(key) for key in ("document_id", "company", "report_year", "report_scope", "available_period_years")}
                    for document in sorted(docs_for_target, key=lambda value: str(value.get("document_id") or ""))
                ],
                "reason_codes": [cause, diagnosis],
            })
        else:
            routing_kind = (
                "LOANS_TO_CUSTOMERS_ROUTING_REVIEW"
                if audit.get("concept_id") == "loans_to_customers"
                else "ROUTING_OR_NAVIGATION_REVIEW"
            )
            routing_queue.append({
                **common,
                "queue_kind": routing_kind,
                "reason_codes": [cause, *sorted({code for item in nearby for code in item.get("failure_codes") or []})],
                "source_quality_or_role_review_required": any(
                    not bool((item.get("routing_snapshot") or {}).get("routing_eligible"))
                    for item in nearby
                ),
            })

    period_queue: list[dict[str, Any]] = []
    for packet in packets:
        status = str(packet.get("packet_status") or "")
        if status not in {"ambiguous_period_columns", "no_period_column"}:
            continue
        operands: list[dict[str, Any]] = []
        for stage in packet.get("stages") or []:
            for operand in stage.get("required_operands") or []:
                if operand.get("column_status") not in {"ambiguous_period_columns", "no_period_column"}:
                    continue
                candidates = list(operand.get("period_column_candidates") or [])
                for candidate in candidates:
                    uid = str(candidate.get("internal_table_uid") or "")
                    if uid not in contexts:
                        raise ValueError("Period candidate references unknown V3 UID")
                operands.append({
                    "stage_id": stage.get("stage_id"),
                    "role": operand.get("role"),
                    "concept_id": operand.get("concept_id"),
                    "period_type": operand.get("period_type"),
                    "column_status": operand.get("column_status"),
                    "column_candidate_reason_counts": operand.get("column_candidate_reason_counts") or {},
                    "period_column_candidates": candidates,
                })
        if not operands:
            raise ValueError(f"Period packet Q{packet.get('question_id')} lacks matching operand")
        period_queue.append({
            "schema_version": 1,
            "protocol": GROUNDING_ADJUDICATION_PROTOCOL,
            "queue_kind": "period_anchor_review",
            "immutable_source_identity": {
                "period_packets_sha256": sha256_file(period_packets_path),
                "question_id": packet.get("question_id"),
                "packet_item_sha256": canonical_sha256(packet),
            },
            "packet_status": status,
            "question_context": packet.get("question_context") or {},
            "operands": operands,
            "reason_codes": [status],
            "decision_contract": _decision_contract(),
            "source_contract": _source_contract(),
        })

    metadata.sort(key=lambda row: (str(row["queue_kind"]), int(row["immutable_source_identity"]["question_id"]), str(row["immutable_source_identity"]["stage_id"]), str(row["immutable_source_identity"]["role"])))
    routing_queue.sort(key=lambda row: (str(row["queue_kind"]), int(row["immutable_source_identity"]["question_id"]), str(row["immutable_source_identity"]["stage_id"]), str(row["immutable_source_identity"]["role"])))
    period_queue.sort(key=lambda row: int(row["immutable_source_identity"]["question_id"]))
    if len(metadata) != 54 or len(routing_queue) != 37 or len(period_queue) != 16:
        raise ValueError("Adjudication queue coverage does not match frozen V1 blocker counts")

    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "metadata_adjudication_queue_v1.jsonl"
    routing_path = output_dir / "routing_gate_adjudication_queue_v1.jsonl"
    period_path = output_dir / "period_adjudication_queue_v1.jsonl"
    examples_path = output_dir / "grounding_adjudication_v1.examples.jsonl"
    manifest_path = output_dir / "grounding_adjudication_v1.manifest.json"
    _write_jsonl(metadata_path, metadata)
    _write_jsonl(routing_path, routing_queue)
    _write_jsonl(period_path, period_queue)
    examples = (metadata[:20] + routing_queue[:20] + period_queue[:20])
    _write_jsonl(examples_path, examples)
    inputs = {
        "period_packets": {"path": str(period_packets_path), "sha256": sha256_file(period_packets_path)},
        "period_manifest": {"path": str(period_manifest_path), "sha256": sha256_file(period_manifest_path)},
        "no_candidate_audit": {"path": str(no_candidate_audit_path), "sha256": audit_sha},
        "document_metadata": {"path": str(document_metadata_path), "sha256": sha256_file(document_metadata_path)},
        "routing_catalog": {"path": str(routing_catalog_path), "sha256": sha256_file(routing_catalog_path)},
        "structured_tables_v2": {"path": str(structured_tables_path), "sha256": sha256_file(structured_tables_path)},
        "evidence_context_v3": {"path": str(evidence_context_path), "sha256": sha256_file(evidence_context_path)},
    }
    result = {
        "schema_version": 1,
        "protocol": GROUNDING_ADJUDICATION_PROTOCOL,
        "inputs": inputs,
        "outputs": {
            "metadata_queue": {"path": str(metadata_path), "sha256": sha256_file(metadata_path)},
            "routing_queue": {"path": str(routing_path), "sha256": sha256_file(routing_path)},
            "period_queue": {"path": str(period_path), "sha256": sha256_file(period_path)},
            "examples": {"path": str(examples_path), "sha256": sha256_file(examples_path)},
        },
        "counts": {
            "missing_operand_audits": len(audits),
            "metadata_queue": len(metadata),
            "routing_queue": len(routing_queue),
            "period_queue": len(period_queue),
            "metadata_diagnosis_counts": dict(sorted(Counter(row["diagnosis"] for row in metadata).items())),
            "routing_queue_kind_counts": dict(sorted(Counter(row["queue_kind"] for row in routing_queue).items())),
            "period_packet_status_counts": dict(sorted(Counter(row["packet_status"] for row in period_queue).items())),
        },
        "source_contract": _source_contract(),
    }
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
