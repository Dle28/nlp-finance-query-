"""Fail-closed handoff audit between V1 review sidecars and corrected V2 queues."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PROTOCOL = "review_sidecar_handoff_audit_v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _contract() -> dict[str, bool]:
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


def _require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def build_review_sidecar_handoff_audit(
    *, review_manifest_path: Path, metadata_reviews_path: Path, period_reviews_path: Path,
    v2_metadata_queue_path: Path, output_dir: Path,
) -> dict[str, Any]:
    """Record review staleness; never carry a V1 decision into V2 automatically."""
    manifest = _read_json(review_manifest_path)
    expected = manifest.get("outputs") or {}
    metadata_sha = _require_hash(metadata_reviews_path, (expected.get("metadata_reviews") or {}).get("sha256"), "metadata review sidecar")
    period_sha = _require_hash(period_reviews_path, (expected.get("period_reviews") or {}).get("sha256"), "period review sidecar")
    v2_sha = sha256_file(v2_metadata_queue_path)
    v2_rows = _read_jsonl(v2_metadata_queue_path)
    v2_by_key = {(int(row.get("question_id") or 0), str(row.get("stage_id") or ""), str(row.get("role") or "")): row for row in v2_rows}
    if len(v2_by_key) != len(v2_rows):
        raise ValueError("V2 metadata queue has duplicate identity")
    records: list[dict[str, Any]] = []
    for review in _read_jsonl(metadata_reviews_path):
        key = (int(review.get("question_id") or 0), str(review.get("stage_id") or ""), str(review.get("role") or ""))
        successor = v2_by_key.get(key)
        changed = successor is not None and successor.get("diagnosis") != review.get("source_diagnosis")
        records.append({
            "schema_version": 1,
            "protocol": PROTOCOL,
            "source_queue": "metadata",
            "source_review_sha256": canonical_sha256(review),
            "source_review_file_sha256": metadata_sha,
            "question_id": key[0], "stage_id": key[1], "role": key[2],
            "review_decision": review.get("decision"),
            "v2_queue_sha256": v2_sha,
            "v2_queue_item_sha256": canonical_sha256(successor) if successor else None,
            "v2_diagnosis": successor.get("diagnosis") if successor else None,
            "handoff_status": "SEMANTIC_DIAGNOSIS_CHANGED_REVIEW_REQUIRED" if changed else "V2_REVIEW_REQUIRED",
            "eligible_for_materialization": False,
            "source_contract": _contract(),
        })
    for review in _read_jsonl(period_reviews_path):
        records.append({
            "schema_version": 1,
            "protocol": PROTOCOL,
            "source_queue": "period",
            "source_review_sha256": canonical_sha256(review),
            "source_review_file_sha256": period_sha,
            "question_id": review.get("question_id"), "stage_id": None, "role": None,
            "review_decision": review.get("decision"),
            "v2_queue_sha256": None, "v2_queue_item_sha256": None, "v2_diagnosis": None,
            "handoff_status": "NO_V2_PERIOD_QUEUE_REVIEW_REQUIRED",
            "eligible_for_materialization": False,
            "source_contract": _contract(),
        })
    review_counts = manifest.get("counts") or {}
    expected_record_count = int(review_counts.get("metadata_reviews", len(_read_jsonl(metadata_reviews_path)))) + int(
        review_counts.get("period_reviews", len(_read_jsonl(period_reviews_path)))
    )
    if len(records) != expected_record_count or any(record["eligible_for_materialization"] for record in records):
        raise ValueError("Unexpected review-handoff coverage or eligibility")
    records.sort(key=lambda row: (row["source_queue"], int(row["question_id"] or 0), str(row["stage_id"] or ""), str(row["role"] or "")))
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "person1_review_handoff_audit_v1.jsonl"
    manifest_path = output_dir / "person1_review_handoff_audit_v1.manifest.json"
    _write_jsonl(audit_path, records)
    result = {
        "schema_version": 1, "protocol": PROTOCOL,
        "inputs": {
            "review_manifest": {"path": str(review_manifest_path), "sha256": sha256_file(review_manifest_path)},
            "metadata_reviews": {"path": str(metadata_reviews_path), "sha256": metadata_sha},
            "period_reviews": {"path": str(period_reviews_path), "sha256": period_sha},
            "v2_metadata_queue": {"path": str(v2_metadata_queue_path), "sha256": v2_sha},
        },
        "outputs": {"handoff_audit": {"path": str(audit_path), "sha256": sha256_file(audit_path)}},
        "counts": {"records": len(records), "handoff_status_counts": dict(sorted(Counter(row["handoff_status"] for row in records).items()))},
        "automatic_carry_forward": False, "repairs_materialized": False, "source_contract": _contract(),
    }
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
