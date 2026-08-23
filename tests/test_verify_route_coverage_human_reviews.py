from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "verify_route_coverage_human_reviews", ROOT / "scripts" / "verify_route_coverage_human_reviews.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path | str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    sys_path = ROOT / "src"
    import sys
    if str(sys_path) not in sys.path:
        sys.path.insert(0, str(sys_path))
    from finance_query.route_coverage_adjudication import canonical_sha256, sha256_file, source_contract

    queue_payload = {
        "question_id": 7,
        "question": "Question 7",
        "normalized_question": "question 7",
        "route_status": "abstain",
        "route_reason_codes": ["MISSING_SCOPE_CONTEXT"],
        "question_context": {"scope": None},
        "route_stages": [],
        "review_tracks": ["question_plan_context"],
        "missing_context": ["scope"],
        "immutable_route_sha256": "a" * 64,
        "immutable_queue_payload_sha256": "b" * 64,
        "source_contract": source_contract(),
    }
    assignment = tmp_path / "assignment.jsonl"
    _write_jsonl(
        assignment,
        [{
            "schema_version": 1,
            "protocol": "route_coverage_independent_review_assignment_v1",
            "assignment_id": "route-coverage-v1-reviewer_a-q7",
            "reviewer_slot": "reviewer_a",
            "question_id": 7,
            "immutable_queue_payload_sha256": "b" * 64,
            "immutable_assignment_payload_sha256": canonical_sha256(queue_payload),
            "queue_payload": queue_payload,
            "source_contract": source_contract(),
        }],
    )
    assignment_manifest = tmp_path / "assignment.manifest.json"
    assignment_manifest.write_text(
        json.dumps(
            {
                "protocol": "route_coverage_independent_review_assignment_v1",
                "reviewer_slots": ["reviewer_a", "reviewer_b"],
                "assignment_count_per_reviewer": 1,
                "blind_to_other_review": True,
                "labels_prepopulated": False,
                "materialization_allowed": False,
                "source_contract": source_contract(),
                "outputs": {"reviewer_a": {"assignment_sha256": sha256_file(assignment)}},
            }
        ),
        encoding="utf-8",
    )
    labels = tmp_path / "labels.jsonl"
    _write_jsonl(
        labels,
        [{
            "schema_version": 1,
            "protocol": "route_coverage_independent_review_label_v1",
            "assignment_id": "route-coverage-v1-reviewer_a-q7",
            "question_id": 7,
            "immutable_queue_payload_sha256": "b" * 64,
            "immutable_assignment_payload_sha256": canonical_sha256(queue_payload),
            "decision": "accept",
            "decision_provenance": "human_verified",
            "reviewer_id": "human-a",
            "reviewed_at": "2026-08-13T00:00:00Z",
            "source_coordinates_checked": [{"source_locator": "report.pdf#page=1", "page_no": 1}],
            "proposed_question_plan": {"scope": "consolidated", "source_locator": "report.pdf#page=1"},
            "proposed_taxonomy_alias": None,
            "proposed_operation_contract": None,
            "notes": "Reopened the source plan and report context.",
            "is_blank_template": False,
            "source_contract": source_contract(),
        }],
    )
    return {
        "assignment": assignment,
        "assignment_manifest": assignment_manifest,
        "completed_labels": labels,
        "reviewer_slot": "reviewer_a",
        "reviewer_id": "human-a",
    }


def test_route_human_review_verifier_requires_complete_blind_handoff(tmp_path: Path) -> None:
    result = mod.verify(**_fixture(tmp_path), output=tmp_path / "review.manifest.json")
    assert result["protocol"] == "route_coverage_independent_human_review_v1"
    assert result["counts"] == {"label_count": 1, "decision_counts": {"accept": 1}}
    assert result["materialization_allowed"] is False
    assert result["source_contract"]["submission_eligible"] is False


def test_route_human_review_verifier_rejects_blank_or_unsafe_proposal(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels = [json.loads(line) for line in Path(paths["completed_labels"]).read_text().splitlines()]
    labels[0]["is_blank_template"] = True
    _write_jsonl(Path(paths["completed_labels"]), labels)
    with pytest.raises(ValueError, match="does not bind"):
        mod.verify(**paths, output=tmp_path / "blank.manifest.json")
    paths = _fixture(tmp_path / "unsafe")
    labels = [json.loads(line) for line in Path(paths["completed_labels"]).read_text().splitlines()]
    labels[0]["proposed_question_plan"] = {"internal_table_uid": "forbidden"}
    _write_jsonl(Path(paths["completed_labels"]), labels)
    with pytest.raises(ValueError, match="forbidden key"):
        mod.verify(**paths, output=tmp_path / "unsafe.manifest.json")
