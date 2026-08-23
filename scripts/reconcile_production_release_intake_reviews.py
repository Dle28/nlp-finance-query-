#!/usr/bin/env python3
"""Reconcile two completed remediation-intake reviews without materializing.

This command requires both completed reviewer manifests for one immutable
assignment set. It records agreement or disagreement only; it cannot modify an
intake, typed plan, Formula EvidenceSet, QueryProgram, route, evidence, answer
or production eligibility.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "verify_production_release_intake_reviews",
    ROOT / "scripts" / "verify_production_release_intake_reviews.py",
)
_review = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_review)


PROTOCOL = "production_release_intake_independent_review_reconciliation_v1"
HUMAN_REVIEW_PROTOCOL = "production_release_intake_human_review_v1"


def _load_json(path: Path) -> dict[str, Any]:
    return _review.load_json(path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return _review.load_jsonl(path)


def _validate_completed_review(
    *, assignment: Path, assignment_manifest: Path, completed_labels: Path,
    review_manifest: Path, reviewer_slot: str,
) -> dict[str, Any]:
    """Revalidate a completed-review receipt before reconciling it."""
    assignment_state = _review.validate_assignment(
        assignment=assignment, assignment_manifest=assignment_manifest, reviewer_slot=reviewer_slot
    )
    descriptor = assignment_state["descriptor"]
    manifest = _load_json(review_manifest)
    if (
        manifest.get("protocol") != HUMAN_REVIEW_PROTOCOL
        or manifest.get("reviewer_slot") != reviewer_slot
        or manifest.get("blind_to_other_review") is not True
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or manifest.get("answer_materialization_allowed") is not False
        or manifest.get("source_contract") != _review.SOURCE_CONTRACT
    ):
        raise ValueError(f"{reviewer_slot}: completed review manifest is invalid")
    reviewer_id = manifest.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError(f"{reviewer_slot}: completed review manifest lacks reviewer ID")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError(f"{reviewer_slot}: completed review manifest lacks inputs")
    checks = {
        "assignment": (assignment, assignment_state["assignment_sha256"]),
        "assignment_manifest": (assignment_manifest, _review.sha256_file(assignment_manifest)),
        "completed_labels": (completed_labels, _review.sha256_file(completed_labels)),
    }
    for name, (path, expected) in checks.items():
        if ((inputs.get(name) or {}).get("sha256")) != expected:
            raise ValueError(f"{reviewer_slot}: completed review manifest does not bind {name}")
    if ((manifest.get("outputs") or {}).get("labels") or {}).get("sha256") != _review.sha256_file(completed_labels):
        raise ValueError(f"{reviewer_slot}: completed review manifest does not bind label output")
    labels = _load_jsonl(completed_labels)
    labels_by_id = {row.get("question_id"): row for row in labels}
    assignments = assignment_state["assignments_by_id"]
    if len(labels) != len(labels_by_id) or set(labels_by_id) != set(assignments):
        raise ValueError(f"{reviewer_slot}: completed labels do not cover assignment exactly")
    proposal_field = descriptor["proposal_field"]
    for question_id, label in labels_by_id.items():
        assignment_row = assignments[question_id]
        if (
            type(question_id) is not int
            or set(label).difference(_review.ALLOWED_LABEL_FIELDS)
            or label.get("schema_version") != 1
            or label.get("protocol") != _review.LABEL_PROTOCOL
            or label.get("assignment_id") != assignment_row.get("assignment_id")
            or label.get("immutable_assignment_payload_sha256")
            != assignment_row.get("immutable_assignment_payload_sha256")
            or label.get("reviewer_slot") != reviewer_slot
            or label.get("intake_protocol") != descriptor["protocol"]
            or label.get("intake_kind") != assignment_row.get("intake_kind")
            or label.get("immutable_review_context_sha256")
            != assignment_row.get("immutable_review_context_sha256")
            or label.get("decision") not in _review.ALLOWED_DECISIONS
            or label.get("decision_provenance") != "human_verified"
            or label.get("reviewer_id") != reviewer_id
            or label.get("is_blank_template") is not False
            or label.get("materialization_allowed") is not False
            or label.get("source_contract") != _review.SOURCE_CONTRACT
            or not isinstance(label.get("notes"), str)
            or not label["notes"].strip()
        ):
            raise ValueError(f"Q{question_id}: {reviewer_slot} completed label is malformed")
        _review.require_review_time(label.get("reviewed_at"), question_id)
        coordinates = _review.require_coordinates(label.get("source_coordinates_checked"), question_id)
        if label.get("source_coordinates_sha256") != _review.canonical_sha256(
            {"source_coordinates_checked": coordinates}
        ):
            raise ValueError(f"Q{question_id}: {reviewer_slot} source-coordinate hash mismatch")
        proposals = {field: label.get(field) for field in _review.PROPOSAL_FIELDS}
        if label["decision"] == "accept":
            proposal = proposals.pop(proposal_field)
            if not isinstance(proposal, dict) or not proposal or any(value is not None for value in proposals.values()):
                raise ValueError(f"Q{question_id}: {reviewer_slot} accept proposal contract is invalid")
            _review.walk_proposal(proposal)
        elif any(value is not None for value in proposals.values()):
            raise ValueError(f"Q{question_id}: {reviewer_slot} non-accept has a proposal")
    return {
        "descriptor": descriptor,
        "reviewer_id": reviewer_id,
        "labels_by_id": labels_by_id,
        "assignment_sha256": assignment_state["assignment_sha256"],
    }


def _reconcile_rows(
    *, a: Mapping[str, Any], b: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive the only permitted reconciliation rows from two reviews."""
    descriptor_a, descriptor_b = a["descriptor"], b["descriptor"]
    if (
        descriptor_a["protocol"] != descriptor_b["protocol"]
        or descriptor_a["output_name"] != descriptor_b["output_name"]
        or descriptor_a["intake_sha256"] != descriptor_b["intake_sha256"]
        or set(a["labels_by_id"]) != set(b["labels_by_id"])
    ):
        raise ValueError("Independent reviewer assignments do not bind the same immutable intake")

    proposal_field = descriptor_a["proposal_field"]
    reconciled: list[dict[str, Any]] = []
    conflicts_rows: list[dict[str, Any]] = []
    for question_id in sorted(a["labels_by_id"]):
        label_a, label_b = a["labels_by_id"][question_id], b["labels_by_id"][question_id]
        decision_a, decision_b = label_a["decision"], label_b["decision"]
        proposal_a, proposal_b = label_a.get(proposal_field), label_b.get(proposal_field)
        proposal_sha_a = _review.canonical_sha256(proposal_a) if proposal_a is not None else None
        proposal_sha_b = _review.canonical_sha256(proposal_b) if proposal_b is not None else None
        coordinates_a = label_a["source_coordinates_checked"]
        coordinates_b = label_b["source_coordinates_checked"]
        coordinates_sha_a = label_a["source_coordinates_sha256"]
        coordinates_sha_b = label_b["source_coordinates_sha256"]
        if decision_a == decision_b == "accept":
            if proposal_sha_a != proposal_sha_b:
                state, proposal, coordinates = "needs_human_proposal_disagreement", None, None
            elif coordinates_sha_a != coordinates_sha_b:
                state, proposal, coordinates = "needs_human_source_coordinate_disagreement", None, None
            else:
                state, proposal, coordinates = "agreed_accept_non_materializable", proposal_a, coordinates_a
        elif decision_a == decision_b:
            state, proposal, coordinates = "agreed_nonaccept_remains_blocked", None, None
        else:
            state, proposal, coordinates = "needs_human_decision_disagreement", None, None
        row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "intake_protocol": descriptor_a["protocol"],
            "intake_output_name": descriptor_a["output_name"],
            "intake_kind": label_a.get("intake_kind"),
            "question_id": question_id,
            "immutable_review_context_sha256": label_a["immutable_review_context_sha256"],
            "reviewer_decisions": {"reviewer_a": decision_a, "reviewer_b": decision_b},
            "reviewer_proposal_sha256": {"reviewer_a": proposal_sha_a, "reviewer_b": proposal_sha_b},
            "reviewer_source_coordinates_sha256": {"reviewer_a": coordinates_sha_a, "reviewer_b": coordinates_sha_b},
            # A lane-specific validator must bind the exact agreed content,
            # rather than trusting a reviewer-specific hash by convention.
            # Disagreements deliberately have no consensus hash.
            "consensus_proposal_sha256": proposal_sha_a if proposal is not None else None,
            "consensus_source_coordinates_sha256": coordinates_sha_a if coordinates is not None else None,
            "reconciliation_state": state,
            "consensus_proposal": proposal,
            "consensus_source_coordinates_checked": coordinates,
            "validation_state": "requires_lane_specific_source_validation" if state == "agreed_accept_non_materializable" else "review_conflict_or_nonaccept",
            "materialization_allowed": False,
            "source_contract": _review.SOURCE_CONTRACT,
        }
        reconciled.append(row)
        if state.startswith("needs_human_"):
            conflicts_rows.append(
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "intake_protocol": descriptor_a["protocol"],
                    "intake_output_name": descriptor_a["output_name"],
                    "question_id": question_id,
                    "immutable_review_context_sha256": row["immutable_review_context_sha256"],
                    "decision_pair": row["reviewer_decisions"],
                    "reason": (
                        "INDEPENDENT_INTAKE_REVIEW_PROPOSAL_DISAGREEMENT"
                        if state == "needs_human_proposal_disagreement"
                        else "INDEPENDENT_INTAKE_REVIEW_SOURCE_COORDINATE_DISAGREEMENT"
                        if state == "needs_human_source_coordinate_disagreement"
                        else "INDEPENDENT_INTAKE_REVIEW_DECISION_DISAGREEMENT"
                    ),
                    "materialization_allowed": False,
                    "source_contract": _review.SOURCE_CONTRACT,
                }
            )
    return reconciled, conflicts_rows


def reconcile(
    *, assignment_manifest: Path, reviewer_a_assignment: Path, reviewer_b_assignment: Path,
    reviewer_a_labels: Path, reviewer_b_labels: Path,
    reviewer_a_review_manifest: Path, reviewer_b_review_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Reconcile two full independent reviews against one assignment manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "production_release_intake_reconciled_reviews_v1.jsonl"
    manifest_path = output_dir / "production_release_intake_independent_review_reconciliation_v1.manifest.json"
    conflicts = output_dir / "production_release_intake_review_conflicts_v1.jsonl"
    if output.exists() or manifest_path.exists() or conflicts.exists():
        raise FileExistsError(f"Refusing to overwrite intake-review reconciliation: {output_dir}")
    a = _validate_completed_review(
        assignment=reviewer_a_assignment, assignment_manifest=assignment_manifest,
        completed_labels=reviewer_a_labels, review_manifest=reviewer_a_review_manifest,
        reviewer_slot="reviewer_a",
    )
    b = _validate_completed_review(
        assignment=reviewer_b_assignment, assignment_manifest=assignment_manifest,
        completed_labels=reviewer_b_labels, review_manifest=reviewer_b_review_manifest,
        reviewer_slot="reviewer_b",
    )
    if a["reviewer_id"] == b["reviewer_id"]:
        raise ValueError("Independent reviewer slots must use distinct reviewer IDs")
    reconciled, conflicts_rows = _reconcile_rows(a=a, b=b)
    descriptor_a = a["descriptor"]
    _review.write_jsonl(output, reconciled)
    _review.write_jsonl(conflicts, conflicts_rows)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "reconciliation_complete": True,
        "intake_protocol": descriptor_a["protocol"],
        "intake_output_name": descriptor_a["output_name"],
        "materialization_allowed": False,
        "inputs": {
            "assignment_manifest": {"path": str(assignment_manifest), "sha256": _review.sha256_file(assignment_manifest)},
            "reviewer_a_assignment": {"path": str(reviewer_a_assignment), "sha256": a["assignment_sha256"]},
            "reviewer_b_assignment": {"path": str(reviewer_b_assignment), "sha256": b["assignment_sha256"]},
            "reviewer_a_labels": {"path": str(reviewer_a_labels), "sha256": _review.sha256_file(reviewer_a_labels)},
            "reviewer_b_labels": {"path": str(reviewer_b_labels), "sha256": _review.sha256_file(reviewer_b_labels)},
            "reviewer_a_review_manifest": {"path": str(reviewer_a_review_manifest), "sha256": _review.sha256_file(reviewer_a_review_manifest)},
            "reviewer_b_review_manifest": {"path": str(reviewer_b_review_manifest), "sha256": _review.sha256_file(reviewer_b_review_manifest)},
        },
        "outputs": {
            "reconciled": {"path": str(output), "sha256": _review.sha256_file(output)},
            "conflicts": {"path": str(conflicts), "sha256": _review.sha256_file(conflicts)},
        },
        "counts": {
            "review_count": len(reconciled),
            "state_counts": dict(sorted(Counter(row["reconciliation_state"] for row in reconciled).items())),
            "conflict_count": len(conflicts_rows),
            "agreed_accept_non_materializable_count": sum(
                row["reconciliation_state"] == "agreed_accept_non_materializable" for row in reconciled
            ),
        },
        "source_contract": _review.SOURCE_CONTRACT,
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def validate_reconciliation_receipt(
    *, assignment_manifest: Path, reviewer_a_assignment: Path, reviewer_b_assignment: Path,
    reviewer_a_labels: Path, reviewer_b_labels: Path,
    reviewer_a_review_manifest: Path, reviewer_b_review_manifest: Path,
    reconciliation_manifest: Path,
) -> dict[str, Any]:
    """Recompute and validate one immutable reconciliation receipt.

    Consumers must call this rather than merely trusting a reconciliation JSONL.
    It revalidates the two completed reviews, derives the only allowed rows,
    and compares them byte-for-byte as decoded JSON objects.
    """
    manifest = _load_json(reconciliation_manifest)
    if (
        manifest.get("protocol") != PROTOCOL
        or manifest.get("reconciliation_complete") is not True
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != _review.SOURCE_CONTRACT
    ):
        raise ValueError("Reconciliation manifest is not non-materializable")
    a = _validate_completed_review(
        assignment=reviewer_a_assignment, assignment_manifest=assignment_manifest,
        completed_labels=reviewer_a_labels, review_manifest=reviewer_a_review_manifest,
        reviewer_slot="reviewer_a",
    )
    b = _validate_completed_review(
        assignment=reviewer_b_assignment, assignment_manifest=assignment_manifest,
        completed_labels=reviewer_b_labels, review_manifest=reviewer_b_review_manifest,
        reviewer_slot="reviewer_b",
    )
    if a["reviewer_id"] == b["reviewer_id"]:
        raise ValueError("Independent reviewer slots must use distinct reviewer IDs")
    expected_rows, expected_conflicts = _reconcile_rows(a=a, b=b)
    descriptor = a["descriptor"]
    if (
        manifest.get("intake_protocol") != descriptor["protocol"]
        or manifest.get("intake_output_name") != descriptor["output_name"]
    ):
        raise ValueError("Reconciliation manifest does not bind the reviewed intake")

    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("Reconciliation manifest lacks inputs")
    expected_inputs = {
        "assignment_manifest": assignment_manifest,
        "reviewer_a_assignment": reviewer_a_assignment,
        "reviewer_b_assignment": reviewer_b_assignment,
        "reviewer_a_labels": reviewer_a_labels,
        "reviewer_b_labels": reviewer_b_labels,
        "reviewer_a_review_manifest": reviewer_a_review_manifest,
        "reviewer_b_review_manifest": reviewer_b_review_manifest,
    }
    for name, path in expected_inputs.items():
        record = inputs.get(name)
        if not isinstance(record, Mapping) or record.get("sha256") != _review.sha256_file(path):
            raise ValueError(f"Reconciliation manifest does not bind {name}")

    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("Reconciliation manifest lacks outputs")
    output_records = {
        name: outputs.get(name) for name in ("reconciled", "conflicts")
    }
    resolved_outputs: dict[str, Path] = {}
    for name, record in output_records.items():
        if not isinstance(record, Mapping):
            raise ValueError(f"Reconciliation manifest lacks {name} output")
        path = _review.resolve_record_path(record.get("path"), manifest_path=reconciliation_manifest)
        if record.get("sha256") != _review.sha256_file(path):
            raise ValueError(f"Reconciliation manifest does not bind {name} output")
        resolved_outputs[name] = path
    actual_rows = _load_jsonl(resolved_outputs["reconciled"])
    actual_conflicts = _load_jsonl(resolved_outputs["conflicts"])
    if actual_rows != expected_rows or actual_conflicts != expected_conflicts:
        raise ValueError("Reconciliation outputs do not match independently recomputed reviews")
    expected_counts = {
        "review_count": len(expected_rows),
        "state_counts": dict(sorted(Counter(row["reconciliation_state"] for row in expected_rows).items())),
        "conflict_count": len(expected_conflicts),
        "agreed_accept_non_materializable_count": sum(
            row["reconciliation_state"] == "agreed_accept_non_materializable" for row in expected_rows
        ),
    }
    if manifest.get("counts") != expected_counts:
        raise ValueError("Reconciliation manifest counts do not match outputs")
    return {
        "descriptor": descriptor,
        "rows_by_id": a["descriptor"]["rows_by_id"],
        "lineage": a["descriptor"]["lineage"],
        "reconciled_rows": actual_rows,
        "reconciliation_sha256": _review.sha256_file(resolved_outputs["reconciled"]),
        "reconciliation_manifest_sha256": _review.sha256_file(reconciliation_manifest),
        "reconciliation_manifest": manifest,
        "reconciled_path": resolved_outputs["reconciled"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment-manifest", type=Path, required=True)
    parser.add_argument("--reviewer-a-assignment", type=Path, required=True)
    parser.add_argument("--reviewer-b-assignment", type=Path, required=True)
    parser.add_argument("--reviewer-a-labels", type=Path, required=True)
    parser.add_argument("--reviewer-b-labels", type=Path, required=True)
    parser.add_argument("--reviewer-a-review-manifest", type=Path, required=True)
    parser.add_argument("--reviewer-b-review-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(reconcile(**vars(args))["manifest_path"])


if __name__ == "__main__":
    main()
