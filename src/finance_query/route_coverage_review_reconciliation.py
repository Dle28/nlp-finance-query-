"""Fail-closed reconciliation of independent reviews for abstained routes.

The reconciler verifies assignment lineage, reviewer independence and proposal
shape. It only records consensus/disagreement; it never changes a route,
creates evidence, selects a value, executes a formula, or promotes provenance.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .route_coverage_adjudication import canonical_sha256, sha256_file, source_contract


ASSIGNMENT_PROTOCOL = "route_coverage_independent_review_assignment_v1"
LABEL_PROTOCOL = "route_coverage_independent_review_label_v1"
PROTOCOL = "route_coverage_independent_review_reconciliation_v1"
HUMAN_REVIEW_PROTOCOL = "route_coverage_independent_human_review_v1"
SLOTS = ("reviewer_a", "reviewer_b")
DECISIONS = frozenset({"accept", "reject", "abstain"})
INDEPENDENT_PROVENANCE = frozenset({"human_verified", "independent_ai_source_review"})
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
        "source_cell", "evidence", "formula",
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


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite route-review reconciliation output: {path}")
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _require_contract(value: object, label: str) -> dict[str, bool]:
    if value != source_contract():
        raise ValueError(f"{label} source contract mismatch")
    return source_contract()


def _require_review_time(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} lacks UTC timezone")
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


def _require_coordinates(value: object, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must provide non-empty source coordinate checks")
    output: list[dict[str, Any]] = []
    allowed = frozenset({"source_locator", "document_id", "page_no", "section", "notes"})
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item).difference(allowed):
            raise ValueError(f"{label}[{index}] has unsupported source-coordinate fields")
        if not isinstance(item.get("source_locator"), str) or not item["source_locator"].strip():
            raise ValueError(f"{label}[{index}] lacks a source locator")
        if "page_no" in item and (type(item["page_no"]) is not int or item["page_no"] < 1):
            raise ValueError(f"{label}[{index}] has invalid page_no")
        output.append(dict(item))
    return output


def _required_proposals(review_tracks: object) -> set[str]:
    if not isinstance(review_tracks, list) or not review_tracks:
        raise ValueError("Route-review assignment has no review tracks")
    mapping = {
        "question_plan_context": "proposed_question_plan",
        "literal_concept_taxonomy": "proposed_taxonomy_alias",
        "typed_direct_operation": "proposed_operation_contract",
        "typed_composed_operation": "proposed_operation_contract",
        "route_contract": "proposed_operation_contract",
    }
    tracks = {str(track) for track in review_tracks}
    if tracks.difference(mapping):
        raise ValueError("Route-review assignment has unsupported review track")
    return {mapping[track] for track in tracks}


def _assignments_by_id(
    *, assignment_path: Path, assignment_manifest: Mapping[str, Any], slot: str
) -> tuple[dict[int, dict[str, Any]], str]:
    output = (assignment_manifest.get("outputs") or {}).get(slot) or {}
    actual_sha = _require_hash(assignment_path, output.get("assignment_sha256"), f"{slot} assignment")
    rows = _load_jsonl(assignment_path)
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = row.get("question_id")
        if type(question_id) is not int or question_id in by_id:
            raise ValueError(f"{slot} assignment has missing or duplicate question IDs")
        if row.get("protocol") != ASSIGNMENT_PROTOCOL or row.get("reviewer_slot") != slot:
            raise ValueError(f"Q{question_id}: invalid {slot} assignment protocol or slot")
        if row.get("assignment_id") != f"route-coverage-v1-{slot}-q{question_id}":
            raise ValueError(f"Q{question_id}: invalid {slot} assignment ID")
        queue_payload = row.get("queue_payload")
        if not isinstance(queue_payload, dict):
            raise ValueError(f"Q{question_id}: {slot} assignment lacks queue payload")
        if queue_payload.get("question_id") != question_id or queue_payload.get("route_status") != "abstain":
            raise ValueError(f"Q{question_id}: {slot} assignment is not bound to an abstained route")
        _require_contract(row.get("source_contract"), f"Q{question_id} {slot} assignment")
        _require_contract(queue_payload.get("source_contract"), f"Q{question_id} {slot} queue payload")
        immutable_assignment = _require_sha(row.get("immutable_assignment_payload_sha256"), f"Q{question_id} immutable assignment payload")
        if immutable_assignment != canonical_sha256(queue_payload):
            raise ValueError(f"Q{question_id}: immutable assignment payload hash mismatch")
        immutable_queue = _require_sha(row.get("immutable_queue_payload_sha256"), f"Q{question_id} immutable queue payload")
        if immutable_queue != queue_payload.get("immutable_queue_payload_sha256"):
            raise ValueError(f"Q{question_id}: assignment/queue payload hash mismatch")
        _required_proposals(queue_payload.get("review_tracks"))
        by_id[question_id] = row
    expected = assignment_manifest.get("assignment_count_per_reviewer")
    if type(expected) is not int or len(by_id) != expected or not by_id:
        raise ValueError(f"{slot} assignment coverage does not match assignment manifest")
    return by_id, actual_sha


def _labels_by_id(
    *, labels_path: Path, assignments: Mapping[int, Mapping[str, Any]], slot: str
) -> tuple[dict[int, dict[str, Any]], str, set[str]]:
    rows = _load_jsonl(labels_path)
    by_id: dict[int, dict[str, Any]] = {}
    reviewer_ids: set[str] = set()
    for row in rows:
        question_id = row.get("question_id")
        if type(question_id) is not int or question_id in by_id:
            raise ValueError(f"{slot} completed labels have missing or duplicate question IDs")
        if set(row).difference(LABEL_FIELDS):
            raise ValueError(f"Q{question_id}: {slot} label has unsupported fields")
        assignment = assignments.get(question_id)
        if assignment is None:
            raise ValueError(f"Q{question_id}: {slot} label is not in the immutable assignment")
        if row.get("schema_version") != 1 or row.get("protocol") != LABEL_PROTOCOL:
            raise ValueError(f"Q{question_id}: {slot} label protocol mismatch")
        for key in ("assignment_id", "immutable_queue_payload_sha256", "immutable_assignment_payload_sha256"):
            if row.get(key) != assignment.get(key):
                raise ValueError(f"Q{question_id}: {slot} label does not bind immutable assignment {key}")
        decision = row.get("decision")
        if decision not in DECISIONS:
            raise ValueError(f"Q{question_id}: {slot} label has unsupported decision")
        if row.get("decision_provenance") != "human_verified":
            raise ValueError(f"Q{question_id}: {slot} label must be human_verified")
        reviewer_id = row.get("reviewer_id")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError(f"Q{question_id}: {slot} label lacks reviewer ID")
        reviewer_ids.add(reviewer_id.strip())
        _require_review_time(row.get("reviewed_at"), f"Q{question_id} {slot} reviewed_at")
        _require_contract(row.get("source_contract"), f"Q{question_id} {slot} label")
        if row.get("is_blank_template") is not False:
            raise ValueError(f"Q{question_id}: {slot} label is still a blank template")
        required_proposals = _required_proposals((assignment.get("queue_payload") or {}).get("review_tracks"))
        proposals = {key: row.get(key) for key in PROPOSAL_FIELDS}
        if decision == "accept":
            _require_coordinates(row.get("source_coordinates_checked"), label=f"Q{question_id} {slot} source coordinates")
            if any(proposals[key] is None for key in required_proposals):
                raise ValueError(f"Q{question_id}: {slot} accept label lacks proposal for its review tracks")
            for value in proposals.values():
                if value is not None:
                    if not isinstance(value, dict) or not value:
                        raise ValueError(f"Q{question_id}: {slot} proposal must be a non-empty object")
                    _walk_proposal(value)
        elif any(value is not None for value in proposals.values()):
            raise ValueError(f"Q{question_id}: {slot} non-accept label must not include a proposal")
        if not isinstance(row.get("notes"), str):
            raise ValueError(f"Q{question_id}: {slot} notes must be text")
        by_id[question_id] = dict(row)
    if set(by_id) != set(assignments):
        raise ValueError(f"{slot} completed label coverage does not match immutable assignment")
    return by_id, sha256_file(labels_path), reviewer_ids


def _require_human_review_manifest(
    *, review_manifest_path: Path, labels_path: Path, assignment_path: Path,
    assignment_manifest_path: Path, slot: str,
) -> dict[str, str]:
    """Require the completed label file to have passed the human-review gate."""
    manifest = _load_json(review_manifest_path)
    if (
        manifest.get("protocol") != HUMAN_REVIEW_PROTOCOL
        or manifest.get("reviewer_slot") != slot
        or manifest.get("blind_to_other_review") is not True
        or manifest.get("materialization_allowed") is not False
    ):
        raise ValueError(f"{slot} completed review manifest is invalid")
    _require_contract(manifest.get("source_contract"), f"{slot} completed review manifest")
    inputs = manifest.get("inputs") or {}
    _require_hash(labels_path, ((inputs.get("completed_labels") or {}).get("sha256")), f"{slot} completed labels")
    _require_hash(assignment_path, ((inputs.get("assignment") or {}).get("sha256")), f"{slot} completed review assignment")
    _require_hash(
        assignment_manifest_path,
        ((inputs.get("assignment_manifest") or {}).get("sha256")),
        f"{slot} completed review assignment manifest",
    )
    _require_hash(labels_path, ((manifest.get("outputs") or {}).get("labels") or {}).get("sha256"), f"{slot} completed review labels output")
    reviewer_id = manifest.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError(f"{slot} completed review manifest lacks reviewer ID")
    return {"path": str(review_manifest_path), "sha256": sha256_file(review_manifest_path), "reviewer_id": reviewer_id.strip()}


def _proposal_payload(label: Mapping[str, Any]) -> dict[str, Any]:
    return {key: label.get(key) for key in PROPOSAL_FIELDS}


def _source_coordinate_payload(label: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the already-validated reviewer source references for consensus.

    Coordinates are not evidence or selected values.  They remain necessary
    lineage for a later, separate source-bound validator to reopen the same
    material.  An accept has already passed ``_require_coordinates`` above.
    """
    value = label.get("source_coordinates_checked")
    if not isinstance(value, list) or not value:
        raise ValueError("Accepted route-review label lacks validated source coordinates")
    return [dict(item) for item in value]


def reconcile_reviews(
    *, assignment_manifest_path: Path, reviewer_a_assignment_path: Path,
    reviewer_b_assignment_path: Path, reviewer_a_labels_path: Path,
    reviewer_b_labels_path: Path, reviewer_a_review_manifest_path: Path,
    reviewer_b_review_manifest_path: Path, output_dir: Path,
) -> dict[str, Any]:
    """Reconcile two complete independent label sets without materializing routes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_output = output_dir / "route_coverage_independent_review_reconciliation_v1.manifest.json"
    if manifest_output.exists():
        raise FileExistsError(f"Refusing to overwrite route-review reconciliation manifest: {manifest_output}")
    assignment_manifest = _load_json(assignment_manifest_path)
    if assignment_manifest.get("protocol") != ASSIGNMENT_PROTOCOL:
        raise ValueError("Unexpected route-review assignment manifest protocol")
    if assignment_manifest.get("reviewer_slots") != list(SLOTS):
        raise ValueError("Route-review assignment manifest has unexpected reviewer slots")
    if assignment_manifest.get("blind_to_other_review") is not True or assignment_manifest.get("labels_prepopulated") is not False:
        raise ValueError("Route-review assignment must be blind and blank")
    if assignment_manifest.get("materialization_allowed") is not False:
        raise ValueError("Route-review assignment must not permit materialization")
    _require_contract(assignment_manifest.get("source_contract"), "route-review assignment manifest")
    completed_a = _require_human_review_manifest(
        review_manifest_path=reviewer_a_review_manifest_path,
        labels_path=reviewer_a_labels_path,
        assignment_path=reviewer_a_assignment_path,
        assignment_manifest_path=assignment_manifest_path,
        slot="reviewer_a",
    )
    completed_b = _require_human_review_manifest(
        review_manifest_path=reviewer_b_review_manifest_path,
        labels_path=reviewer_b_labels_path,
        assignment_path=reviewer_b_assignment_path,
        assignment_manifest_path=assignment_manifest_path,
        slot="reviewer_b",
    )
    assignments_a, assignment_a_sha = _assignments_by_id(assignment_path=reviewer_a_assignment_path, assignment_manifest=assignment_manifest, slot="reviewer_a")
    assignments_b, assignment_b_sha = _assignments_by_id(assignment_path=reviewer_b_assignment_path, assignment_manifest=assignment_manifest, slot="reviewer_b")
    if set(assignments_a) != set(assignments_b):
        raise ValueError("Independent reviewer assignment ID coverage mismatch")
    labels_a, labels_a_sha, reviewer_a_ids = _labels_by_id(labels_path=reviewer_a_labels_path, assignments=assignments_a, slot="reviewer_a")
    labels_b, labels_b_sha, reviewer_b_ids = _labels_by_id(labels_path=reviewer_b_labels_path, assignments=assignments_b, slot="reviewer_b")
    if reviewer_a_ids != {completed_a["reviewer_id"]} or reviewer_b_ids != {completed_b["reviewer_id"]}:
        raise ValueError("Completed label reviewer IDs differ from verified review manifests")
    if reviewer_a_ids.intersection(reviewer_b_ids):
        raise ValueError("Independent reviewer label sets reuse a reviewer ID across slots")

    reconciled: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for question_id in sorted(assignments_a):
        label_a, label_b = labels_a[question_id], labels_b[question_id]
        decision_a, decision_b = label_a["decision"], label_b["decision"]
        proposal_a, proposal_b = _proposal_payload(label_a), _proposal_payload(label_b)
        proposal_sha_a, proposal_sha_b = canonical_sha256(proposal_a), canonical_sha256(proposal_b)
        source_coordinates_a = (
            _source_coordinate_payload(label_a) if decision_a == "accept" else None
        )
        source_coordinates_b = (
            _source_coordinate_payload(label_b) if decision_b == "accept" else None
        )
        source_sha_a = (
            canonical_sha256({"source_coordinates_checked": source_coordinates_a})
            if source_coordinates_a is not None
            else None
        )
        source_sha_b = (
            canonical_sha256({"source_coordinates_checked": source_coordinates_b})
            if source_coordinates_b is not None
            else None
        )
        if decision_a == decision_b == "accept":
            if proposal_sha_a != proposal_sha_b:
                state, proposal, source_coordinates = "needs_human_proposal_disagreement", None, None
            elif source_sha_a != source_sha_b:
                state, proposal, source_coordinates = "needs_human_source_coordinate_disagreement", None, None
            else:
                state, proposal, source_coordinates = (
                    "agreed_accept_non_materializable",
                    proposal_a,
                    source_coordinates_a,
                )
        elif decision_a == decision_b:
            state, proposal, source_coordinates = "agreed_nonaccept_route_remains_abstain", None, None
        else:
            state, proposal, source_coordinates = "needs_human_decision_disagreement", None, None
        row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "immutable_queue_payload_sha256": assignments_a[question_id]["immutable_queue_payload_sha256"],
            "review_tracks": list((assignments_a[question_id].get("queue_payload") or {}).get("review_tracks") or []),
            "reviewer_decisions": {"reviewer_a": decision_a, "reviewer_b": decision_b},
            "reviewer_proposal_sha256": {"reviewer_a": proposal_sha_a, "reviewer_b": proposal_sha_b},
            "reviewer_source_coordinates_sha256": {"reviewer_a": source_sha_a, "reviewer_b": source_sha_b},
            "reconciliation_state": state,
            "proposed_route_change": proposal,
            "consensus_source_coordinates_checked": source_coordinates,
            "route_status_after_reconciliation": "abstain",
            "materialization_allowed": False,
            "source_contract": source_contract(),
        }
        reconciled.append(row)
        if state.startswith("needs_human_"):
            conflicts.append({
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "immutable_queue_payload_sha256": row["immutable_queue_payload_sha256"],
                "decision_pair": row["reviewer_decisions"],
                "reason": (
                    "INDEPENDENT_ROUTE_REVIEW_PROPOSAL_DISAGREEMENT"
                    if state == "needs_human_proposal_disagreement"
                    else "INDEPENDENT_ROUTE_REVIEW_SOURCE_COORDINATE_DISAGREEMENT"
                    if state == "needs_human_source_coordinate_disagreement"
                    else "INDEPENDENT_ROUTE_REVIEW_DECISION_DISAGREEMENT"
                ),
                "route_status": "abstain",
                "materialization_allowed": False,
                "source_contract": source_contract(),
            })
    reconciled_path = output_dir / "route_coverage_reconciled_reviews_v1.jsonl"
    conflicts_path = output_dir / "route_coverage_review_conflicts_v1.jsonl"
    _write_jsonl(reconciled_path, reconciled)
    _write_jsonl(conflicts_path, conflicts)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "reconciliation_complete": True,
        "materialization_allowed": False,
        "inputs": {
            "assignment_manifest": {"path": str(assignment_manifest_path), "sha256": sha256_file(assignment_manifest_path)},
            "reviewer_a_assignment": {"path": str(reviewer_a_assignment_path), "sha256": assignment_a_sha},
            "reviewer_b_assignment": {"path": str(reviewer_b_assignment_path), "sha256": assignment_b_sha},
            "reviewer_a_labels": {"path": str(reviewer_a_labels_path), "sha256": labels_a_sha},
            "reviewer_b_labels": {"path": str(reviewer_b_labels_path), "sha256": labels_b_sha},
            "reviewer_a_review_manifest": completed_a,
            "reviewer_b_review_manifest": completed_b,
        },
        "outputs": {
            "reconciled": {"path": str(reconciled_path), "sha256": sha256_file(reconciled_path)},
            "conflicts": {"path": str(conflicts_path), "sha256": sha256_file(conflicts_path)},
        },
        "counts": {
            "review_count": len(reconciled),
            "state_counts": dict(sorted(Counter(row["reconciliation_state"] for row in reconciled).items())),
            "conflict_count": len(conflicts),
            "agreed_accept_non_materializable_count": sum(row["reconciliation_state"] == "agreed_accept_non_materializable" for row in reconciled),
        },
        "source_contract": source_contract(),
    }
    manifest_output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_output)}
