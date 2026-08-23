#!/usr/bin/env python3
"""Verify a completed blind human review batch for abstained routes.

This produces only a hash-bound review manifest. It cannot alter routes,
materialize a proposal, select evidence, or grant any production permission.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.route_coverage_adjudication import canonical_sha256, sha256_file, source_contract


PROTOCOL = "route_coverage_independent_human_review_v1"
ASSIGNMENT_PROTOCOL = "route_coverage_independent_review_assignment_v1"
LABEL_PROTOCOL = "route_coverage_independent_review_label_v1"
SLOTS = ("reviewer_a", "reviewer_b")
DECISIONS = frozenset({"accept", "reject", "abstain"})
PROPOSAL_FIELDS = (
    "proposed_question_plan",
    "proposed_taxonomy_alias",
    "proposed_operation_contract",
)
LABEL_FIELDS = frozenset(
    {
        "schema_version",
        "protocol",
        "assignment_id",
        "question_id",
        "immutable_queue_payload_sha256",
        "immutable_assignment_payload_sha256",
        "decision",
        "decision_provenance",
        "reviewer_id",
        "reviewed_at",
        "source_coordinates_checked",
        *PROPOSAL_FIELDS,
        "notes",
        "is_blank_template",
        "source_contract",
    }
)
FORBIDDEN_PROPOSAL_KEYS = frozenset(
    {
        "answer", "value", "numeric_value", "raw_value", "selected",
        "selected_value", "table", "table_uid", "internal_table_uid",
        "row", "row_index", "column", "column_index", "cell",
        "source_cell", "evidence", "formula", "execution", "submission",
        "promotion", "eligible_for_materialization",
    }
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def _require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _require_time(value: object, question_id: int) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"Q{question_id}: reviewed_at must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"Q{question_id}: reviewed_at is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Q{question_id}: reviewed_at lacks UTC timezone")
    return value


def _walk_proposal(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_PROPOSAL_KEYS:
                raise ValueError(f"Route proposal contains forbidden key: {key}")
            _walk_proposal(child)
    elif isinstance(value, list):
        for child in value:
            _walk_proposal(child)


def _require_coordinates(value: object, question_id: int) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Q{question_id}: completed human review needs source coordinate checks")
    allowed = frozenset({"source_locator", "document_id", "page_no", "section", "notes"})
    for index, coordinate in enumerate(value):
        if not isinstance(coordinate, dict) or set(coordinate).difference(allowed):
            raise ValueError(f"Q{question_id}: source coordinate {index} has unsupported fields")
        if not isinstance(coordinate.get("source_locator"), str) or not coordinate["source_locator"].strip():
            raise ValueError(f"Q{question_id}: source coordinate {index} lacks source_locator")
        if "page_no" in coordinate and (type(coordinate["page_no"]) is not int or coordinate["page_no"] < 1):
            raise ValueError(f"Q{question_id}: source coordinate {index} has invalid page_no")


def _required_proposals(tracks: object) -> set[str]:
    mapping = {
        "question_plan_context": "proposed_question_plan",
        "literal_concept_taxonomy": "proposed_taxonomy_alias",
        "typed_direct_operation": "proposed_operation_contract",
        "typed_composed_operation": "proposed_operation_contract",
        "route_contract": "proposed_operation_contract",
    }
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("Route assignment has no review tracks")
    values = {str(track) for track in tracks}
    if values.difference(mapping):
        raise ValueError("Route assignment has unsupported review track")
    return {mapping[value] for value in values}


def verify(
    *, assignment: Path, assignment_manifest: Path, completed_labels: Path,
    reviewer_slot: str, reviewer_id: str, output: Path,
) -> dict[str, Any]:
    """Verify one complete human label set against the immutable assignment."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite route human-review manifest: {output}")
    if reviewer_slot not in SLOTS or not reviewer_id.strip():
        raise ValueError("A valid reviewer slot and non-empty reviewer ID are required")
    manifest = _load_json(assignment_manifest)
    if (
        manifest.get("protocol") != ASSIGNMENT_PROTOCOL
        or manifest.get("reviewer_slots") != list(SLOTS)
        or manifest.get("blind_to_other_review") is not True
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != source_contract()
    ):
        raise ValueError("Assignment manifest is not a blank independent non-materializable review")
    output_record = (manifest.get("outputs") or {}).get(reviewer_slot) or {}
    assignment_sha = _require_hash(assignment, output_record.get("assignment_sha256"), "reviewer assignment")
    assignments = {row.get("question_id"): row for row in _load_jsonl(assignment)}
    expected_count = manifest.get("assignment_count_per_reviewer")
    if (
        type(expected_count) is not int
        or not assignments
        or len(assignments) != expected_count
        or any(type(question_id) is not int for question_id in assignments)
    ):
        raise ValueError("Reviewer assignment has missing, duplicate or incomplete question IDs")
    for question_id, row in assignments.items():
        payload = row.get("queue_payload")
        if (
            row.get("protocol") != ASSIGNMENT_PROTOCOL
            or row.get("reviewer_slot") != reviewer_slot
            or row.get("assignment_id") != f"route-coverage-v1-{reviewer_slot}-q{question_id}"
            or not isinstance(payload, dict)
            or payload.get("question_id") != question_id
            or payload.get("route_status") != "abstain"
            or row.get("source_contract") != source_contract()
            or payload.get("source_contract") != source_contract()
            or _require_sha(row.get("immutable_assignment_payload_sha256"), f"Q{question_id} assignment hash")
            != canonical_sha256(payload)
            or _require_sha(row.get("immutable_queue_payload_sha256"), f"Q{question_id} queue hash")
            != payload.get("immutable_queue_payload_sha256")
        ):
            raise ValueError(f"Q{question_id}: immutable reviewer assignment is malformed")
        _required_proposals(payload.get("review_tracks"))

    labels = _load_jsonl(completed_labels)
    labels_by_id = {row.get("question_id"): row for row in labels}
    if len(labels_by_id) != len(labels) or set(labels_by_id) != set(assignments):
        raise ValueError("Completed human labels must cover exactly the blind assignment")
    for question_id, label in labels_by_id.items():
        assignment_row = assignments[question_id]
        if (
            set(label).difference(LABEL_FIELDS)
            or label.get("schema_version") != 1
            or label.get("protocol") != LABEL_PROTOCOL
            or label.get("assignment_id") != assignment_row.get("assignment_id")
            or label.get("immutable_queue_payload_sha256") != assignment_row.get("immutable_queue_payload_sha256")
            or label.get("immutable_assignment_payload_sha256") != assignment_row.get("immutable_assignment_payload_sha256")
            or label.get("decision") not in DECISIONS
            or label.get("decision_provenance") != "human_verified"
            or label.get("reviewer_id") != reviewer_id
            or label.get("is_blank_template") is not False
            or label.get("source_contract") != source_contract()
            or not isinstance(label.get("notes"), str)
            or not label["notes"].strip()
        ):
            raise ValueError(f"Q{question_id}: completed human label does not bind the blind assignment")
        _require_time(label.get("reviewed_at"), question_id)
        _require_coordinates(label.get("source_coordinates_checked"), question_id)
        required = _required_proposals((assignment_row.get("queue_payload") or {}).get("review_tracks"))
        proposals = {key: label.get(key) for key in PROPOSAL_FIELDS}
        if label["decision"] == "accept":
            if any(proposals[key] is None for key in required):
                raise ValueError(f"Q{question_id}: human accept lacks proposal for a review track")
            for value in proposals.values():
                if value is not None:
                    if not isinstance(value, dict) or not value:
                        raise ValueError(f"Q{question_id}: human proposal must be a non-empty object")
                    _walk_proposal(value)
        elif any(value is not None for value in proposals.values()):
            raise ValueError(f"Q{question_id}: non-accept human label must not include a proposal")

    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "reviewer_slot": reviewer_slot,
        "reviewer_id": reviewer_id,
        "blind_to_other_review": True,
        "inputs": {
            "assignment": {"path": str(assignment), "sha256": assignment_sha},
            "assignment_manifest": {"path": str(assignment_manifest), "sha256": sha256_file(assignment_manifest)},
            "completed_labels": {"path": str(completed_labels), "sha256": sha256_file(completed_labels)},
        },
        "outputs": {"labels": {"path": str(completed_labels), "sha256": sha256_file(completed_labels)}},
        "counts": {"label_count": len(labels), "decision_counts": dict(sorted(Counter(row["decision"] for row in labels).items()))},
        "materialization_allowed": False,
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
    parser.add_argument("--reviewer-slot", choices=SLOTS, required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(verify(**vars(args))["protocol"])


if __name__ == "__main__":
    main()
