"""Corrected, lineage-preserving metadata adjudication queue producer.

Despite the module name, this producer emits the V3 successor queue.  It keeps
V1 and the under-specified V2 queue immutable so prior independent-review
artifacts remain auditable.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "grounding_adjudication_queues_v3"
SCHEMA_VERSION = 3
METADATA_CAUSES = {"ENTITY", "SECTOR", "YEAR"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "eligible_for_materialization": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def decision_contract() -> dict[str, Any]:
    return {
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": None,
        "reviewed_at": None,
        "source_coordinates_checked": False,
        "proposed_patch": None,
        "eligible_for_materialization": False,
    }


def _require_hash(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def _expected_context(audit: Mapping[str, Any]) -> tuple[set[str], set[int], str | None]:
    nearby = list(audit.get("nearby_exact_concept_candidates") or [])
    entities = {
        str(value)
        for item in nearby
        for value in ((item.get("gate_vector") or {}).get("entity") or {}).get("expected") or []
        if str(value)
    }
    years = {
        int(value)
        for item in nearby
        for value in ((item.get("gate_vector") or {}).get("year") or {}).get("expected") or []
        if isinstance(value, int) and not isinstance(value, bool)
    }
    scopes = {
        str(scope)
        for item in nearby
        for scope in [((item.get("gate_vector") or {}).get("scope") or {}).get("expected")]
        if scope is not None and str(scope)
    }
    if len(scopes) > 1:
        raise ValueError(f"Q{audit.get('question_id')}: conflicting requested scopes in frozen audit")
    return entities, years, next(iter(scopes), None)


def target_document_candidates(audit: Mapping[str, Any], documents: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return only documents matching frozen expected company, year, and scope."""
    entities, years, scope = _expected_context(audit)
    candidates = [
        {
            key: document.get(key)
            for key in ("document_id", "company", "report_year", "report_scope", "available_period_years")
        }
        for document in documents
        if (not entities or str(document.get("company") or "") in entities)
        and (not years or document.get("report_year") in years)
        and (scope is None or document.get("report_scope") == scope)
    ]
    return sorted(candidates, key=lambda row: str(row.get("document_id") or ""))


def compact_nearby_references(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Retain replayable exact-source identity without copying source values."""
    compact: list[dict[str, Any]] = []
    for item in audit.get("nearby_exact_concept_candidates") or []:
        uid = item.get("internal_table_uid")
        row_index = item.get("row_index")
        document_id = item.get("document_id")
        if not isinstance(uid, str) or not uid or not isinstance(row_index, int) or not isinstance(document_id, str) or not document_id:
            raise ValueError(f"Q{audit.get('question_id')}: frozen nearby candidate lacks exact source identity")
        compact.append(
            {
                "internal_table_uid": uid,
                "row_index": row_index,
                "document_id": document_id,
                "gate_vector": item.get("gate_vector") or {},
                "failure_codes": list(item.get("failure_codes") or []),
                "routing_snapshot": item.get("routing_snapshot") or {},
            }
        )
    if not compact:
        raise ValueError(f"Q{audit.get('question_id')}: metadata audit lacks replayable exact-source references")
    return compact


def entity_diagnosis(audit: Mapping[str, Any], documents: list[Mapping[str, Any]], taxonomy_rows: list[Mapping[str, Any]]) -> str:
    target_ids = {row["document_id"] for row in target_document_candidates(audit, documents)}
    if not target_ids:
        return "TARGET_DOCUMENT_ABSENT"
    exact = [
        row
        for row in taxonomy_rows
        if row.get("document_id") in target_ids
        and row.get("match_status") == "exact_unique"
        and (row.get("concept_candidates") or [{}])[0].get("concept_id") == audit.get("concept_id")
    ]
    return "TARGET_ROW_UNMAPPED" if exact else "EXACT_CONCEPT_ABSENT_FOR_ENTITY"


def immutable_source_identity(audit: Mapping[str, Any], audit_sha: str) -> dict[str, Any]:
    return {
        "audit_sha256": audit_sha,
        "audit_item_sha256": canonical_sha(audit),
        "question_id": audit["question_id"],
        "stage_id": audit["stage_id"],
        "role": audit["role"],
        "concept_id": audit["concept_id"],
    }


def build(*, audit_path: Path, period_manifest: Path, document_metadata: Path, taxonomy_candidates: Path, output: Path) -> dict[str, Any]:
    period = json.loads(period_manifest.read_text(encoding="utf-8"))
    outputs = period.get("outputs") or {}
    inputs = period.get("inputs") or {}
    _require_hash(audit_path, ((outputs.get("no_candidate_audit") or {}).get("sha256")), "no-candidate audit")
    _require_hash(document_metadata, ((inputs.get("document_metadata") or {}).get("sha256")), "document metadata")
    _require_hash(taxonomy_candidates, ((inputs.get("taxonomy_candidates") or {}).get("sha256")), "taxonomy candidates")

    audits = rows(audit_path)
    documents = rows(document_metadata)
    taxonomy = rows(taxonomy_candidates)
    audit_sha = sha(audit_path)
    metadata: list[dict[str, Any]] = []
    for audit in audits:
        cause = str(audit.get("exclusive_primary_cause") or "")
        if cause not in METADATA_CAUSES:
            continue
        nearby = compact_nearby_references(audit)
        targets = target_document_candidates(audit, documents)
        diagnosis = (
            "CANDIDATE_SECTOR_UNKNOWN"
            if cause == "SECTOR"
            else entity_diagnosis(audit, documents, taxonomy)
            if cause == "ENTITY"
            else "TARGET_YEAR_PERIOD_REVIEW"
        )
        metadata.append(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol": PROTOCOL,
                "question_id": audit["question_id"],
                "stage_id": audit["stage_id"],
                "role": audit["role"],
                "concept_id": audit["concept_id"],
                "exclusive_primary_cause": cause,
                "diagnosis": diagnosis,
                "immutable_source_identity": immutable_source_identity(audit, audit_sha),
                "target_document_candidates": targets,
                "nearby_exact_concept_candidates": nearby,
                "minimal_blocker_sets": list(audit.get("minimal_blocker_sets") or []),
                "decision_contract": decision_contract(),
                "source_contract": source_contract(),
            }
        )
    metadata.sort(key=lambda row: (str(row["exclusive_primary_cause"]), int(row["question_id"]), str(row["stage_id"]), str(row["role"])))
    if len(metadata) != 54:
        raise ValueError("Corrected metadata queue coverage mismatch")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in metadata),
        encoding="utf-8",
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "inputs": {
            "audit": {"path": str(audit_path), "sha256": audit_sha},
            "period_manifest": {"path": str(period_manifest), "sha256": sha(period_manifest)},
            "document_metadata": {"path": str(document_metadata), "sha256": sha(document_metadata)},
            "taxonomy_candidates": {"path": str(taxonomy_candidates), "sha256": sha(taxonomy_candidates)},
        },
        "outputs": {"metadata_queue": {"path": str(output), "sha256": sha(output)}},
        "counts": {
            "metadata_records": len(metadata),
            "diagnosis_counts": dict(sorted(Counter(row["diagnosis"] for row in metadata).items())),
        },
        "repairs_materialized": False,
        "source_contract": source_contract(),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
