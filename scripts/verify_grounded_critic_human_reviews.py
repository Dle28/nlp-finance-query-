#!/usr/bin/env python3
"""Verify a completed blind human-review batch without changing any label.

The command emits only a hash-bound review manifest.  It refuses blank,
partial, Qwen-visible, non-source-bound, or non-human-provenance label files;
it does not materialize evidence, answers, provenance promotion, training, or
submission eligibility.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.grounded_critic_protocol import source_contract


PROTOCOL = "grounded_critic_independent_human_review_v1"
ASSIGNMENT_PROTOCOL = "grounded_critic_independent_review_assignment_v1"
LABEL_PROTOCOL = "grounded_critic_independent_label_v1"
GPU_AUDIT_PROTOCOL = "grounded_critic_gpu_run_audit_v2"
ALLOWED_STATUS = frozenset({"accept", "reject", "abstain"})
ALLOWED_FIELDS = frozenset(
    {
        "schema_version",
        "protocol",
        "assignment_id",
        "question_id",
        "immutable_packet_sha256",
        "status",
        "provenance",
        "reviewer_id",
        "reviewed_at",
        "source_coordinates_checked",
        "source_coordinate_agree",
        "unit_period_agree",
        "deterministic_replay_agree",
        "unsupported_evidence",
        "notes",
        "is_blank_template",
        "source_contract",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_review_time(value: object, question_id: int) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"Q{question_id}: reviewed_at must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"Q{question_id}: reviewed_at is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Q{question_id}: reviewed_at lacks UTC timezone")
    return value


def verify(
    *, assignment: Path, assignment_manifest: Path, completed_labels: Path,
    reviewer_slot: str, reviewer_id: str, output: Path,
) -> dict[str, Any]:
    """Verify one complete human label set for a pre-existing blind assignment."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite human-review manifest: {output}")
    if reviewer_slot not in {"reviewer_a", "reviewer_b"} or not reviewer_id.strip():
        raise ValueError("A valid reviewer slot and non-empty reviewer ID are required")
    manifest = load_json(assignment_manifest)
    if (
        manifest.get("protocol") != ASSIGNMENT_PROTOCOL
        or manifest.get("blind_to_qwen_decision") is not True
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("source_contract") != source_contract()
    ):
        raise ValueError("Assignment manifest is not a blank Qwen-blind non-promotable review")
    assignment_output = (manifest.get("outputs") or {}).get(reviewer_slot) or {}
    assignment_sha = require_hash(assignment, assignment_output.get("assignment_sha256"), "reviewer assignment")
    audit_record = ((manifest.get("inputs") or {}).get("gpu_run_audit") or {})
    audit_path = Path(str(audit_record.get("path") or ""))
    if not audit_path.is_file():
        raise ValueError("Assignment lacks an accessible GPU audit")
    audit_sha = require_hash(audit_path, audit_record.get("sha256"), "assignment GPU audit")
    audit = load_json(audit_path)
    if (
        audit.get("protocol") != GPU_AUDIT_PROTOCOL
        or audit.get("audit_passed") is not True
        or audit.get("source_contract") != source_contract()
    ):
        raise ValueError("Assignment GPU audit is not a passed non-promotable audit")

    assignments = {int(row["question_id"]): row for row in load_jsonl(assignment)}
    if not assignments or len(assignments) != manifest.get("assignment_count_per_reviewer"):
        raise ValueError("Reviewer assignment coverage is incomplete or duplicate")
    for question_id, row in assignments.items():
        if (
            row.get("protocol") != ASSIGNMENT_PROTOCOL
            or row.get("reviewer_slot") != reviewer_slot
            or row.get("assignment_id") != f"critic-v2-{reviewer_slot}-q{question_id}"
            or row.get("source_contract") != source_contract()
            or "critic_result" in row
            or "status" in (row.get("packet") or {})
        ):
            raise ValueError(f"Q{question_id}: assignment is malformed or exposes a Qwen decision")

    labels = load_jsonl(completed_labels)
    labels_by_id = {row.get("question_id"): row for row in labels}
    if len(labels_by_id) != len(labels) or set(labels_by_id) != set(assignments):
        raise ValueError("Completed human labels must cover exactly the blind assignment")
    for question_id, label in labels_by_id.items():
        if type(question_id) is not int or set(label).difference(ALLOWED_FIELDS):
            raise ValueError(f"Q{question_id}: completed human label has unsupported fields")
        assignment_row = assignments[question_id]
        if (
            label.get("schema_version") != 1
            or label.get("protocol") != LABEL_PROTOCOL
            or label.get("assignment_id") != assignment_row.get("assignment_id")
            or label.get("immutable_packet_sha256") != assignment_row.get("immutable_packet_sha256")
            or label.get("status") not in ALLOWED_STATUS
            or label.get("provenance") != "human_verified"
            or label.get("reviewer_id") != reviewer_id
            or label.get("source_coordinates_checked") is not True
            or label.get("is_blank_template") is not False
            or label.get("source_contract") != source_contract()
        ):
            raise ValueError(f"Q{question_id}: completed human label does not bind the blind assignment")
        require_review_time(label.get("reviewed_at"), question_id)
        if not isinstance(label.get("notes"), str) or not label["notes"].strip():
            raise ValueError(f"Q{question_id}: completed human label needs review notes")
        for key in ("source_coordinate_agree", "unit_period_agree", "deterministic_replay_agree", "unsupported_evidence"):
            if type(label.get(key)) is not bool:
                raise ValueError(f"Q{question_id}: completed human label {key} must be boolean")
        if label["status"] == "accept" and (
            label["source_coordinate_agree"] is not True
            or label["unit_period_agree"] is not True
            or label["deterministic_replay_agree"] is not True
            or label["unsupported_evidence"] is not False
        ):
            raise ValueError(f"Q{question_id}: human accept is inconsistent with source checks")

    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "blind_to_qwen_decision": True,
        "reviewer_slot": reviewer_slot,
        "reviewer_id": reviewer_id,
        "inputs": {
            "assignment": {"path": str(assignment), "sha256": assignment_sha},
            "assignment_manifest": {"path": str(assignment_manifest), "sha256": sha256_file(assignment_manifest)},
            "gpu_run_audit": {"path": str(audit_path), "sha256": audit_sha},
            "completed_labels": {"path": str(completed_labels), "sha256": sha256_file(completed_labels)},
        },
        "outputs": {"labels": {"path": str(completed_labels), "sha256": sha256_file(completed_labels)}},
        "counts": {"label_count": len(labels), "status_counts": dict(sorted(Counter(row["status"] for row in labels).items()))},
        "source_contract": source_contract(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--assignment-manifest", type=Path, required=True)
    parser.add_argument("--completed-labels", type=Path, required=True)
    parser.add_argument("--reviewer-slot", choices=("reviewer_a", "reviewer_b"), required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(verify(**vars(args))["protocol"])


if __name__ == "__main__":
    main()
