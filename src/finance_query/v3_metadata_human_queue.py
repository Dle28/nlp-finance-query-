"""Build a non-promotable human approval queue for V3 metadata proposals."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "v3_metadata_human_approval_queue_v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, values: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values), encoding="utf-8")


def build_human_approval_queue(*, review_manifest_path: Path, metadata_reviews_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = _read_json(review_manifest_path)
    expected = ((manifest.get("outputs") or {}).get("metadata_reviews") or {}).get("sha256")
    review_sha = sha256_file(metadata_reviews_path)
    if not isinstance(expected, str) or expected != review_sha:
        raise ValueError("SHA-256 mismatch for V3 metadata review sidecar")
    queue: list[dict[str, Any]] = []
    for review in _read_jsonl(metadata_reviews_path):
        if review.get("decision") != "accept_repair":
            continue
        patch = review.get("proposed_patch")
        if not isinstance(patch, dict) or patch.get("scope") != "metadata":
            raise ValueError("Accepted V3 metadata review has no metadata patch")
        queue.append({
            "schema_version": 1, "protocol": PROTOCOL,
            "source_review_sha256": hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "source_review_file_sha256": review_sha,
            "question_id": review.get("question_id"), "stage_id": review.get("stage_id"), "role": review.get("role"),
            "proposed_patch": patch, "authoritative_sources": review.get("authoritative_sources") or [],
            "reason_codes": review.get("reason_codes") or [],
            "reviewer_action": "approve | reject | uncertain",
            "reviewer_decision": None, "human_verified": False,
            "eligible_for_materialization": False,
            "source_contract": {"evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False},
        })
    if len(queue) != 27:
        raise ValueError(f"Expected exactly 27 V3 metadata proposals, found {len(queue)}")
    queue.sort(key=lambda row: (int(row["question_id"]), str(row["stage_id"])))
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "person1_v3_metadata_human_approval_queue_v1.jsonl"
    manifest_path = output_dir / "person1_v3_metadata_human_approval_queue_v1.manifest.json"
    _write_jsonl(queue_path, queue)
    result = {"schema_version": 1, "protocol": PROTOCOL, "inputs": {"review_manifest": {"path": str(review_manifest_path), "sha256": sha256_file(review_manifest_path)}, "metadata_reviews": {"path": str(metadata_reviews_path), "sha256": review_sha}}, "outputs": {"queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)}}, "counts": {"needs_human": len(queue)}, "repairs_materialized": False, "source_contract": {"evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False}}
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
