from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.multi_operand_source_recheck_intake import (
    validate_multi_operand_source_recheck_intake,
    write_multi_operand_source_recheck_intake,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _bundle() -> dict[str, object]:
    return {
        "protocol": "vifinqa_multi_operand_source_recheck_ui_v1",
        "itemCount": 1,
        "sourceRecheckReceiptSha256": "recheck-sha",
        "sourceMultiOperandBundleSha256": "bundle-sha",
        "priorReviewerIds": ["dungle"],
        "sourceContract": {
            "numericFinancialValuesExposed": False, "maySelectValue": False,
            "mayExecuteFormula": False, "humanVerified": False,
            "mayAuthorizeEvidence": False, "mayAuthorizeAnswer": False,
            "trainingEligible": False, "submissionEligible": False,
        },
        "items": [{
            "recheckId": "recheck-1", "packetId": "packet-1", "questionId": 745,
            "operand": {"operandId": "x0", "routeId": "route-1"},
            "selectedCells": [{"rowIndex": 1, "columnIndex": 1}, {"rowIndex": 12, "columnIndex": 1}],
            "candidate": {"tableUid": "table-1"},
        }],
    }


def _decision(reviewer: str = "reviewer-2") -> dict[str, object]:
    return {
        "protocol": "vifinqa_multi_operand_source_recheck_forms_v1",
        "source_recheck_receipt_sha256": "recheck-sha",
        "source_multi_operand_bundle_sha256": "bundle-sha",
        "recheck_id": "recheck-1", "packet_id": "packet-1", "question_id": 745,
        "operand_id": "x0", "route_id": "route-1", "selected_internal_table_uid": "table-1",
        "selected_cells": [{"row_index": 1, "column_index": 1}, {"row_index": 12, "column_index": 1}],
        "review_state": "REVIEWED", "overall_decision": "APPROVE",
        "review_confirmations": {"table_subject": True, "scope": True, "period": True, "unit": True},
        "reviewer_id": reviewer, "reviewed_at": "2026-08-26T11:00:00Z", "reviewer_notes": "",
        "independent_from_prior_reviewer": True,
        "human_verified": False, "may_authorize_evidence": False, "may_authorize_answer": False,
        "training_eligible": False, "submission_eligible": False,
    }


def test_accepts_independent_frozen_source_recheck_without_promotion(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_bundle()), encoding="utf-8")
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(decisions, [_decision()])
    receipt = validate_multi_operand_source_recheck_intake(
        decision_path=decisions, ui_bundle_path=bundle
    )
    assert receipt["status"] == "ACCEPTED_NON_PROMOTING"
    assert receipt["independent_reviewer_ids"] == ["reviewer-2"]
    assert receipt["decisions"][0]["selected_cells"] == [
        {"row_index": 1, "column_index": 1},
        {"row_index": 12, "column_index": 1},
    ]
    output = tmp_path / "intake"
    write_multi_operand_source_recheck_intake(
        decision_path=decisions, ui_bundle_path=bundle, output_dir=output
    )
    assert (output / "source_recheck_intake_receipt_v1.json").is_file()


def test_rejects_prior_reviewer_or_changed_coordinates(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_bundle()), encoding="utf-8")
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(decisions, [_decision("DUNGLE")])
    with pytest.raises(ValueError, match="not independent"):
        validate_multi_operand_source_recheck_intake(
            decision_path=decisions, ui_bundle_path=bundle
        )
    changed = _decision()
    changed["selected_cells"] = [{"row_index": 2, "column_index": 1}]
    _write_jsonl(decisions, [changed])
    with pytest.raises(ValueError, match="exact frozen cell"):
        validate_multi_operand_source_recheck_intake(
            decision_path=decisions, ui_bundle_path=bundle
        )
