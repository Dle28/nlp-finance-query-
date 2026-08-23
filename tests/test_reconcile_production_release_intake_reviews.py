from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "verify_production_release_intake_reviews",
    ROOT / "scripts" / "verify_production_release_intake_reviews.py",
)
review = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(review)
reconcile_spec = importlib.util.spec_from_file_location(
    "reconcile_production_release_intake_reviews",
    ROOT / "scripts" / "reconcile_production_release_intake_reviews.py",
)
mod = importlib.util.module_from_spec(reconcile_spec)
assert reconcile_spec.loader is not None
reconcile_spec.loader.exec_module(mod)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": review.sha256_file(path)}


def _fixture(tmp_path: Path) -> dict[str, Path]:
    gate = tmp_path / "gate.json"
    _write_json(gate, {"protocol": "production_release_gate_v1", "release_status": "blocked", "production_eligible": False, "submission_compilation_allowed": False, "answer_materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT})
    remediation = tmp_path / "remediation.jsonl"
    _write_jsonl(remediation, [{"question_id": 7}])
    remediation_manifest = tmp_path / "remediation.manifest.json"
    _write_json(remediation_manifest, {"protocol": "production_release_remediation_queue_v1", "queue_status": "non_materializable", "source_contract": review.SOURCE_CONTRACT, "inputs": {"release_gate": _record(gate)}, "outputs": {"queue": _record(remediation)}})
    ancillary = []
    for name in ("bundle.jsonl", "typed.jsonl", "typed.manifest.json"):
        path = tmp_path / name
        path.write_text("{}\n", encoding="utf-8")
        ancillary.append(path)
    context = {"question": "Question 7"}
    intake_row = {
        "schema_version": 1,
        "protocol": "typed_plan_abstain_review_queue_v1",
        "question_id": 7,
        "immutable_review_context_sha256": review.canonical_sha256(context),
        "review_context": context,
        "review_decision_contract": {"decision": None, "decision_provenance": None, "reviewer_id": None, "reviewed_at": None, "source_coordinates_checked": None, "proposed_typed_plan": None, "materialization_allowed": False},
        "materialization_allowed": False,
        "source_contract": review.SOURCE_CONTRACT,
    }
    intake = tmp_path / "intake.jsonl"
    _write_jsonl(intake, [intake_row])
    intake_manifest = tmp_path / "intake.manifest.json"
    _write_json(intake_manifest, {"protocol": "typed_plan_abstain_review_queue_v1", "question_count": 1, "labels_prepopulated": False, "materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT, "inputs": {"bundle_review_items": _record(ancillary[0]), "typed_plans": _record(ancillary[1]), "typed_plans_manifest": _record(ancillary[2]), "release_gate": _record(gate), "remediation_queue": _record(remediation), "remediation_manifest": _record(remediation_manifest)}, "outputs": {"queue": _record(intake)}})
    assignments = review.build_assignments(intake=intake, intake_manifest=intake_manifest, output_dir=tmp_path / "assignments")
    assignment_manifest = Path(assignments["manifest_path"])
    result: dict[str, Path] = {"assignment_manifest": assignment_manifest}
    for slot, reviewer_id in (("reviewer_a", "human-a"), ("reviewer_b", "human-b")):
        assignment = Path(assignments["outputs"][slot]["assignment"]["path"])
        assignment_row = json.loads(assignment.read_text().splitlines()[0])
        coordinates = [{"source_locator": "report.pdf#page=1", "page_no": 1}]
        label = review.blank_label_template(assignment_row=assignment_row)
        label.update({"decision": "accept", "decision_provenance": "human_verified", "reviewer_id": reviewer_id, "reviewed_at": "2026-08-13T00:00:00Z", "source_coordinates_checked": coordinates, "source_coordinates_sha256": review.canonical_sha256({"source_coordinates_checked": coordinates}), "proposed_typed_plan": {"scope": "consolidated", "operation": "direct_lookup"}, "notes": "Independently reopened report context.", "is_blank_template": False})
        labels = tmp_path / f"{slot}-labels.jsonl"
        _write_jsonl(labels, [label])
        review_manifest = tmp_path / f"{slot}-review.json"
        review.verify(assignment=assignment, assignment_manifest=assignment_manifest, completed_labels=labels, reviewer_slot=slot, reviewer_id=reviewer_id, output=review_manifest)
        result[f"{slot}_assignment"] = assignment
        result[f"{slot}_labels"] = labels
        result[f"{slot}_review_manifest"] = review_manifest
    return result


def _kwargs(paths: dict[str, Path]) -> dict[str, Path]:
    return {
        "assignment_manifest": paths["assignment_manifest"],
        "reviewer_a_assignment": paths["reviewer_a_assignment"],
        "reviewer_b_assignment": paths["reviewer_b_assignment"],
        "reviewer_a_labels": paths["reviewer_a_labels"],
        "reviewer_b_labels": paths["reviewer_b_labels"],
        "reviewer_a_review_manifest": paths["reviewer_a_review_manifest"],
        "reviewer_b_review_manifest": paths["reviewer_b_review_manifest"],
    }


def test_reconciliation_preserves_agreement_as_nonmaterializable_handoff(tmp_path: Path) -> None:
    result = mod.reconcile(**_kwargs(_fixture(tmp_path)), output_dir=tmp_path / "out")
    assert result["counts"] == {"review_count": 1, "state_counts": {"agreed_accept_non_materializable": 1}, "conflict_count": 0, "agreed_accept_non_materializable_count": 1}
    row = json.loads((tmp_path / "out" / "production_release_intake_reconciled_reviews_v1.jsonl").read_text())
    assert row["validation_state"] == "requires_lane_specific_source_validation"
    assert row["materialization_allowed"] is False
    assert row["consensus_proposal"] == {"scope": "consolidated", "operation": "direct_lookup"}
    assert row["consensus_proposal_sha256"] == review.canonical_sha256(row["consensus_proposal"])
    assert row["consensus_source_coordinates_sha256"] == review.canonical_sha256(
        {"source_coordinates_checked": row["consensus_source_coordinates_checked"]}
    )


def test_reconciliation_rejects_reused_reviewer_or_different_coordinates(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels = [json.loads(line) for line in paths["reviewer_b_labels"].read_text().splitlines()]
    labels[0]["reviewer_id"] = "human-a"
    _write_jsonl(paths["reviewer_b_labels"], labels)
    with pytest.raises(ValueError, match="does not bind completed_labels|malformed"):
        mod.reconcile(**_kwargs(paths), output_dir=tmp_path / "reused")
    paths = _fixture(tmp_path / "coordinates")
    labels = [json.loads(line) for line in paths["reviewer_b_labels"].read_text().splitlines()]
    coordinates = [{"source_locator": "report.pdf#page=2", "page_no": 2}]
    labels[0]["source_coordinates_checked"] = coordinates
    labels[0]["source_coordinates_sha256"] = review.canonical_sha256({"source_coordinates_checked": coordinates})
    _write_jsonl(paths["reviewer_b_labels"], labels)
    review.verify(assignment=paths["reviewer_b_assignment"], assignment_manifest=paths["assignment_manifest"], completed_labels=paths["reviewer_b_labels"], reviewer_slot="reviewer_b", reviewer_id="human-b", output=tmp_path / "coordinates" / "new-review.json")
    paths["reviewer_b_review_manifest"] = tmp_path / "coordinates" / "new-review.json"
    result = mod.reconcile(**_kwargs(paths), output_dir=tmp_path / "coordinates" / "out")
    assert result["counts"]["state_counts"] == {"needs_human_source_coordinate_disagreement": 1}
