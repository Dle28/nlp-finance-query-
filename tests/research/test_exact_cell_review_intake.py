from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.exact_cell_review_intake import (
    validate_exact_cell_review_intake,
    write_exact_cell_review_intake,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _bundle() -> dict[str, object]:
    source = {
        "locatorSha256": "locator-1",
        "sourceSha256": "source-1",
        "tableSha256": "table-1",
    }
    candidate = {
        "tableUid": "table-1",
        "rowCandidates": [{"rowIndex": 4}],
        "columnHeaders": [{"columnIndex": 2}],
        "source": source,
    }
    return {
        "protocol": "vifinqa_exact_cell_human_review_ui_v2",
        "sourceReviewSampleSha256": "sample-sha",
        "typedOperandPlansSha256": "plans-sha",
        "sourceStructuredTablesSha256": "tables-sha",
        "sourceContract": {"numericFinancialValuesExposed": False, "mayAuthorizeAnswer": False},
        "items": [
            {"sampleId": "sample-1", "reviewPacketId": "packet-1", "routeId": "route-1", "questionId": 1, "operandId": "x0", "candidates": [candidate]},
            {"sampleId": "sample-2", "reviewPacketId": "packet-2", "routeId": "route-2", "questionId": 2, "operandId": "x1", "candidates": [candidate]},
        ],
    }


def _decision(*, sample: str, packet: str, route: str, question: int, operand: str, verdict: str) -> dict[str, object]:
    approved = verdict == "APPROVE"
    return {
        "protocol": "vifinqa_exact_cell_human_review_forms_v1",
        "source_review_sample_sha256": "sample-sha",
        "typed_operand_plans_sha256": "plans-sha",
        "sample_id": sample,
        "review_packet_id": packet,
        "route_id": route,
        "question_id": question,
        "operand_id": operand,
        "review_state": "REVIEWED",
        "overall_decision": verdict,
        "selected_internal_table_uid": "table-1" if approved else None,
        "selected_row_index": 4 if approved else None,
        "selected_column_index": 2 if approved else None,
        "selected_exact_table_locator_sha256": "locator-1" if approved else None,
        "selected_source_sha256": "source-1" if approved else None,
        "selected_table_sha256": "table-1" if approved else None,
        "review_confirmations": {"table_subject": True, "scope": True, "period": True, "unit": True} if approved else None,
        "reviewer_id": "reviewer-1",
        "reviewed_at": "2026-08-26T06:00:00Z",
        "reviewer_notes": "" if approved else "Cần thêm nguồn.",
        "human_verified": False,
        "may_authorize_evidence": False,
        "may_authorize_answer": False,
        "training_eligible": False,
        "submission_eligible": False,
    }


def test_accepts_complete_hash_bound_review_without_promotion(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_bundle()), encoding="utf-8")
    decisions_path = tmp_path / "decisions.jsonl"
    _write_jsonl(
        decisions_path,
        [
            _decision(sample="sample-1", packet="packet-1", route="route-1", question=1, operand="x0", verdict="APPROVE"),
            _decision(sample="sample-2", packet="packet-2", route="route-2", question=2, operand="x1", verdict="UNCERTAIN"),
        ],
    )
    receipt = validate_exact_cell_review_intake(decision_path=decisions_path, ui_bundle_path=bundle_path)
    assert receipt["status"] == "ACCEPTED_NON_PROMOTING"
    assert receipt["decision_counts"] == {"APPROVE": 1, "REJECT": 0, "UNCERTAIN": 1}
    assert receipt["authorization"]["human_verified_count"] == 0
    output = tmp_path / "intake"
    write_exact_cell_review_intake(decision_path=decisions_path, ui_bundle_path=bundle_path, output_dir=output)
    assert (output / "review_intake_receipt_v1.json").is_file()
    assert (output / "manifest.json").is_file()


def test_rejects_decision_that_claims_human_verified(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_bundle()), encoding="utf-8")
    decisions_path = tmp_path / "decisions.jsonl"
    decision = _decision(sample="sample-1", packet="packet-1", route="route-1", question=1, operand="x0", verdict="APPROVE")
    decision["human_verified"] = True
    _write_jsonl(
        decisions_path,
        [
            decision,
            _decision(sample="sample-2", packet="packet-2", route="route-2", question=2, operand="x1", verdict="UNCERTAIN"),
        ],
    )
    with pytest.raises(ValueError, match="human_verified"):
        validate_exact_cell_review_intake(decision_path=decisions_path, ui_bundle_path=bundle_path)
