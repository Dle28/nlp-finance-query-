from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finance_query.route_coverage_adjudication import canonical_sha256, sha256_file, source_contract
from finance_query.route_coverage_review_reconciliation import reconcile_reviews


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _assignment(question_id: int, slot: str, tracks: list[str]) -> dict:
    queue_payload = {
        "question_id": question_id,
        "question": f"Question {question_id}",
        "normalized_question": f"question {question_id}",
        "route_status": "abstain",
        "route_reason_codes": ["MISSING_SCOPE_CONTEXT"],
        "question_context": {"scope": None},
        "route_stages": [],
        "review_tracks": tracks,
        "missing_context": ["scope"],
        "immutable_route_sha256": "a" * 64,
        "immutable_queue_payload_sha256": "b" * 64,
        "source_contract": source_contract(),
    }
    return {
        "schema_version": 1,
        "protocol": "route_coverage_independent_review_assignment_v1",
        "assignment_id": f"route-coverage-v1-{slot}-q{question_id}",
        "reviewer_slot": slot,
        "assignment_position": 1,
        "question_id": question_id,
        "immutable_queue_payload_sha256": "b" * 64,
        "immutable_assignment_payload_sha256": canonical_sha256(queue_payload),
        "queue_payload": queue_payload,
        "review_instructions": [],
        "source_contract": source_contract(),
    }


def _label(assignment: dict, *, reviewer: str, decision: str, proposal: dict | None) -> dict:
    return {
        "schema_version": 1,
        "protocol": "route_coverage_independent_review_label_v1",
        "assignment_id": assignment["assignment_id"],
        "question_id": assignment["question_id"],
        "immutable_queue_payload_sha256": assignment["immutable_queue_payload_sha256"],
        "immutable_assignment_payload_sha256": assignment["immutable_assignment_payload_sha256"],
        "decision": decision,
        "decision_provenance": "human_verified",
        "reviewer_id": reviewer,
        "reviewed_at": "2026-08-13T00:00:00Z",
        "source_coordinates_checked": [{"source_locator": "report.pdf#page=1", "page_no": 1}],
        "proposed_question_plan": proposal,
        "proposed_taxonomy_alias": None,
        "proposed_operation_contract": None,
        "notes": "reviewed",
        "is_blank_template": False,
        "source_contract": source_contract(),
    }


def _review_manifest(
    *, path: Path, slot: str, reviewer_id: str, labels: Path, assignment: Path, assignment_manifest: Path
) -> None:
    path.write_text(
        json.dumps(
            {
                "protocol": "route_coverage_independent_human_review_v1",
                "reviewer_slot": slot,
                "reviewer_id": reviewer_id,
                "blind_to_other_review": True,
                "materialization_allowed": False,
                "source_contract": source_contract(),
                "inputs": {
                    "assignment": {"sha256": sha256_file(assignment)},
                    "assignment_manifest": {"sha256": sha256_file(assignment_manifest)},
                    "completed_labels": {"sha256": sha256_file(labels)},
                },
                "outputs": {"labels": {"sha256": sha256_file(labels)}},
            }
        ),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> dict[str, Path]:
    assignments_a = [_assignment(2, "reviewer_a", ["question_plan_context"]), _assignment(5, "reviewer_a", ["question_plan_context"])]
    assignments_b = [_assignment(2, "reviewer_b", ["question_plan_context"]), _assignment(5, "reviewer_b", ["question_plan_context"])]
    a_path, b_path = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write_jsonl(a_path, assignments_a)
    _write_jsonl(b_path, assignments_b)
    manifest = tmp_path / "assignment.manifest.json"
    manifest.write_text(json.dumps({
        "protocol": "route_coverage_independent_review_assignment_v1",
        "reviewer_slots": ["reviewer_a", "reviewer_b"],
        "assignment_count_per_reviewer": 2,
        "blind_to_other_review": True,
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "outputs": {"reviewer_a": {"assignment_sha256": sha256_file(a_path)}, "reviewer_b": {"assignment_sha256": sha256_file(b_path)}},
        "source_contract": source_contract(),
    }), encoding="utf-8")
    labels_a, labels_b = tmp_path / "labels-a.jsonl", tmp_path / "labels-b.jsonl"
    proposal = {"scope": "consolidated", "source_note": "reviewer recorded source plan"}
    _write_jsonl(labels_a, [_label(assignments_a[0], reviewer="human-a", decision="accept", proposal=proposal), _label(assignments_a[1], reviewer="human-a", decision="abstain", proposal=None)])
    _write_jsonl(labels_b, [_label(assignments_b[0], reviewer="human-b", decision="accept", proposal=proposal), _label(assignments_b[1], reviewer="human-b", decision="reject", proposal=None)])
    reviewer_a_manifest, reviewer_b_manifest = tmp_path / "reviewer-a.manifest.json", tmp_path / "reviewer-b.manifest.json"
    _review_manifest(path=reviewer_a_manifest, slot="reviewer_a", reviewer_id="human-a", labels=labels_a, assignment=a_path, assignment_manifest=manifest)
    _review_manifest(path=reviewer_b_manifest, slot="reviewer_b", reviewer_id="human-b", labels=labels_b, assignment=b_path, assignment_manifest=manifest)
    return {"assignment_manifest_path": manifest, "reviewer_a_assignment_path": a_path, "reviewer_b_assignment_path": b_path, "reviewer_a_labels_path": labels_a, "reviewer_b_labels_path": labels_b, "reviewer_a_review_manifest_path": reviewer_a_manifest, "reviewer_b_review_manifest_path": reviewer_b_manifest}


def test_reconciliation_requires_complete_independent_hash_bound_labels(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = reconcile_reviews(**paths, output_dir=tmp_path / "out")
    assert result["counts"] == {"review_count": 2, "state_counts": {"agreed_accept_non_materializable": 1, "needs_human_decision_disagreement": 1}, "conflict_count": 1, "agreed_accept_non_materializable_count": 1}
    rows = [json.loads(line) for line in (tmp_path / "out" / "route_coverage_reconciled_reviews_v1.jsonl").read_text().splitlines()]
    assert rows[0]["route_status_after_reconciliation"] == "abstain"
    assert rows[0]["materialization_allowed"] is False
    assert rows[0]["proposed_route_change"] == {"proposed_question_plan": {"scope": "consolidated", "source_note": "reviewer recorded source plan"}, "proposed_taxonomy_alias": None, "proposed_operation_contract": None}
    assert rows[1]["reconciliation_state"] == "needs_human_decision_disagreement"


def test_reconciliation_rejects_reused_reviewer_or_unsafe_proposal(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels = [json.loads(line) for line in paths["reviewer_b_labels_path"].read_text().splitlines()]
    for label in labels:
        label["reviewer_id"] = "human-a"
    _write_jsonl(paths["reviewer_b_labels_path"], labels)
    _review_manifest(path=paths["reviewer_b_review_manifest_path"], slot="reviewer_b", reviewer_id="human-a", labels=paths["reviewer_b_labels_path"], assignment=paths["reviewer_b_assignment_path"], assignment_manifest=paths["assignment_manifest_path"])
    with pytest.raises(ValueError, match="reuse a reviewer ID"):
        reconcile_reviews(**paths, output_dir=tmp_path / "reused")
    paths = _fixture(tmp_path / "unsafe")
    labels = [json.loads(line) for line in paths["reviewer_a_labels_path"].read_text().splitlines()]
    labels[0]["proposed_question_plan"] = {"table_uid": "must-not-materialize"}
    _write_jsonl(paths["reviewer_a_labels_path"], labels)
    _review_manifest(path=paths["reviewer_a_review_manifest_path"], slot="reviewer_a", reviewer_id="human-a", labels=paths["reviewer_a_labels_path"], assignment=paths["reviewer_a_assignment_path"], assignment_manifest=paths["assignment_manifest_path"])
    with pytest.raises(ValueError, match="forbidden key"):
        reconcile_reviews(**paths, output_dir=tmp_path / "unsafe-out")


def test_reconciliation_rejects_partial_label_set(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    lines = paths["reviewer_a_labels_path"].read_text().splitlines()
    paths["reviewer_a_labels_path"].write_text(lines[0] + "\n", encoding="utf-8")
    _review_manifest(path=paths["reviewer_a_review_manifest_path"], slot="reviewer_a", reviewer_id="human-a", labels=paths["reviewer_a_labels_path"], assignment=paths["reviewer_a_assignment_path"], assignment_manifest=paths["assignment_manifest_path"])
    with pytest.raises(ValueError, match="coverage"):
        reconcile_reviews(**paths, output_dir=tmp_path / "partial")


def test_reconciliation_treats_distinct_accept_proposals_as_conflict(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels = [json.loads(line) for line in paths["reviewer_b_labels_path"].read_text().splitlines()]
    labels[0]["proposed_question_plan"] = {"scope": "separate", "source_note": "different source plan"}
    _write_jsonl(paths["reviewer_b_labels_path"], labels)
    _review_manifest(path=paths["reviewer_b_review_manifest_path"], slot="reviewer_b", reviewer_id="human-b", labels=paths["reviewer_b_labels_path"], assignment=paths["reviewer_b_assignment_path"], assignment_manifest=paths["assignment_manifest_path"])
    result = reconcile_reviews(**paths, output_dir=tmp_path / "different-proposals")
    assert result["counts"]["state_counts"]["needs_human_proposal_disagreement"] == 1
    conflict = json.loads((tmp_path / "different-proposals" / "route_coverage_review_conflicts_v1.jsonl").read_text().splitlines()[0])
    assert conflict["reason"] == "INDEPENDENT_ROUTE_REVIEW_PROPOSAL_DISAGREEMENT"


def test_reconciliation_treats_distinct_accept_source_coordinates_as_conflict(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels = [json.loads(line) for line in paths["reviewer_b_labels_path"].read_text().splitlines()]
    labels[0]["source_coordinates_checked"] = [{"source_locator": "other-report.pdf#page=2", "page_no": 2}]
    _write_jsonl(paths["reviewer_b_labels_path"], labels)
    _review_manifest(path=paths["reviewer_b_review_manifest_path"], slot="reviewer_b", reviewer_id="human-b", labels=paths["reviewer_b_labels_path"], assignment=paths["reviewer_b_assignment_path"], assignment_manifest=paths["assignment_manifest_path"])
    result = reconcile_reviews(**paths, output_dir=tmp_path / "different-sources")
    assert result["counts"]["state_counts"]["needs_human_source_coordinate_disagreement"] == 1
    conflict = json.loads((tmp_path / "different-sources" / "route_coverage_review_conflicts_v1.jsonl").read_text().splitlines()[0])
    assert conflict["reason"] == "INDEPENDENT_ROUTE_REVIEW_SOURCE_COORDINATE_DISAGREEMENT"


def test_reconciliation_rejects_stale_completed_review_manifest(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels = [json.loads(line) for line in paths["reviewer_a_labels_path"].read_text().splitlines()]
    labels[0]["notes"] = "tampered after review verification"
    _write_jsonl(paths["reviewer_a_labels_path"], labels)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        reconcile_reviews(**paths, output_dir=tmp_path / "stale")


def test_real_route_assignments_are_compatible_with_reconciliation_contract() -> None:
    source_root = ROOT / "artifacts" / "research" / "route_coverage_adjudication_v1"
    root = source_root / "independent_review_assignments_v1"
    manifest = json.loads((root / "route_coverage_independent_review_assignments_v1.manifest.json").read_text())
    queue_ids = {
        json.loads(line)["question_id"]
        for line in (source_root / "route_coverage_adjudication_queue_v1.jsonl").read_text().splitlines()
    }
    assert manifest["assignment_count_per_reviewer"] == 902
    assert len(queue_ids) == 902
    assert manifest["materialization_allowed"] is False
    for slot in ("reviewer_a", "reviewer_b"):
        rows = [json.loads(line) for line in (root / f"route_coverage_{slot}_assignment_v1.jsonl").read_text().splitlines()]
        assert len(rows) == 902
        assert {row["question_id"] for row in rows} == queue_ids
        for row in rows:
            assert row["protocol"] == "route_coverage_independent_review_assignment_v1"
            assert row["immutable_assignment_payload_sha256"] == canonical_sha256(row["queue_payload"])
            assert row["queue_payload"]["route_status"] == "abstain"
            assert row["source_contract"] == source_contract()
