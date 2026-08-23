"""Audit whether a corrected queue has enough lineage for independent review."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PROTOCOL = "v2_reviewability_audit_v2"
REQUIRED_FIELDS = ("question_id", "stage_id", "role", "concept_id", "immutable_source_identity", "target_document_candidates", "nearby_exact_concept_candidates")
IDENTITY_FIELDS = ("audit_sha256", "audit_item_sha256", "question_id", "stage_id", "role", "concept_id")
DOCUMENT_FIELDS = ("document_id", "company", "report_year", "report_scope", "available_period_years")
NEARBY_FIELDS = ("internal_table_uid", "row_index", "document_id", "gate_vector")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _contract() -> dict[str, bool]:
    return {"candidate_only": True, "evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False, "eligible_for_materialization": False}


def _missing_lineage_fields(row: Mapping[str, Any]) -> list[str]:
    missing = [field for field in REQUIRED_FIELDS if not row.get(field)]
    identity = row.get("immutable_source_identity") or {}
    missing.extend(f"immutable_source_identity.{field}" for field in IDENTITY_FIELDS if not identity.get(field))
    targets = list(row.get("target_document_candidates") or [])
    if targets:
        for index, candidate in enumerate(targets):
            missing.extend(
                f"target_document_candidates[{index}].{field}"
                for field in DOCUMENT_FIELDS
                if field not in candidate
            )
    nearby = list(row.get("nearby_exact_concept_candidates") or [])
    if nearby:
        for index, candidate in enumerate(nearby):
            missing.extend(
                f"nearby_exact_concept_candidates[{index}].{field}"
                for field in NEARBY_FIELDS
                if not candidate.get(field) and candidate.get(field) != 0
            )
    return missing


def build_v2_reviewability_audit(*, queue_path: Path, output_dir: Path) -> dict[str, Any]:
    """Emit a non-materializable blocker record for each under-specified item."""
    queue_sha = sha256_file(queue_path)
    rows = _read_jsonl(queue_path)
    records = []
    for row in rows:
        missing = _missing_lineage_fields(row)
        records.append({
            "schema_version": 1, "protocol": PROTOCOL,
            "question_id": row.get("question_id"), "stage_id": row.get("stage_id"), "role": row.get("role"),
            "source_queue_sha256": queue_sha,
            "source_queue_item_sha256": hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "reviewability_status": "BLOCKED_MISSING_REVIEW_PROVENANCE" if missing else "REVIEWABLE",
            "missing_fields": missing,
            "required_next_action": "REGENERATE_V2_QUEUE_WITH_FROZEN_SOURCE_IDENTITY_AND_TARGET_DOCUMENT_CANDIDATES" if missing else "INDEPENDENT_REVIEW_REQUIRED",
            "eligible_for_materialization": False, "source_contract": _contract(),
        })
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "v2_metadata_reviewability_audit_v2.jsonl"
    manifest_path = output_dir / "v2_metadata_reviewability_audit_v2.manifest.json"
    _write_jsonl(audit_path, records)
    result = {
        "schema_version": 1, "protocol": PROTOCOL,
        "inputs": {"queue": {"path": str(queue_path), "sha256": queue_sha}},
        "outputs": {"audit": {"path": str(audit_path), "sha256": sha256_file(audit_path)}},
        "counts": {"records": len(records), "status_counts": dict(sorted(Counter(row["reviewability_status"] for row in records).items()))},
        "repairs_materialized": False, "source_contract": _contract(),
    }
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
