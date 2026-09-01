from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.multi_operand_review_intake import (
    validate_multi_operand_review_intake,
    write_multi_operand_review_intake,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _bundle() -> dict[str, object]:
    candidate = {
        "tableUid": "table-1", "rowCandidates": [{"rowIndex": 5}, {"rowIndex": 6}], "columnHeaders": [{"columnIndex": 2}, {"columnIndex": 3}],
        "selectableCells": [{"rowIndex": 5, "columnIndex": 2}, {"rowIndex": 5, "columnIndex": 3}, {"rowIndex": 6, "columnIndex": 2}],
        "source": {"locatorSha256": "locator-1", "sourceSha256": "source-1", "tableSha256": "table-1"},
    }
    operand = {"operandId": "x0", "routeId": "route-1", "candidates": [candidate]}
    return {
        "protocol": "vifinqa_multi_operand_review_ui_v2", "sourceQueueSha256": "queue-sha", "requiredOperandCount": 2,
        "sourceContract": {
            "numericFinancialValuesExposed": False, "maySelectValue": False, "mayExecuteFormula": False,
            "mayAuthorizeEvidence": False, "mayAuthorizeAnswer": False, "trainingEligible": False, "submissionEligible": False,
        },
        "items": [
            {"packetId": "packet-1", "questionId": 1, "operands": [operand, {**operand, "operandId": "x1", "routeId": "route-2"}]},
            {"packetId": "packet-2", "questionId": 2, "operands": [operand, {**operand, "operandId": "x1", "routeId": "route-2"}]},
        ],
    }


def _decision(packet: str, question: int, verdict: str) -> dict[str, object]:
    approved = verdict == "APPROVE"
    selected = [
        {
            "operand_id": operand, "route_id": route, "selected_internal_table_uid": "table-1",
            "cell_combination": "CELL_SET",
            "selected_cells": [
                {"row_index": 5, "column_index": 2},
                {"row_index": 5, "column_index": 3},
            ],
            "selected_exact_table_locator_sha256": "locator-1", "selected_source_sha256": "source-1", "selected_table_sha256": "table-1",
            "review_confirmations": {"table_subject": True, "scope": True, "period": True, "unit": True},
        }
        for operand, route in (("x0", "route-1"), ("x1", "route-2"))
    ] if approved else []
    return {
        "protocol": "vifinqa_multi_operand_review_forms_v2", "source_multi_operand_queue_sha256": "queue-sha",
        "packet_id": packet, "question_id": question, "review_state": "REVIEWED", "overall_decision": verdict,
        "operand_decisions": selected, "reviewer_id": "reviewer-1", "reviewed_at": "2026-08-26T07:00:00Z",
        "reviewer_notes": "" if approved else "Thiếu nguồn.", "human_verified": False,
        "may_authorize_evidence": False, "may_authorize_answer": False, "training_eligible": False, "submission_eligible": False,
    }


def test_accepts_only_complete_operand_approval_without_promotion(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_bundle()), encoding="utf-8")
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(decisions, [_decision("packet-1", 1, "APPROVE"), _decision("packet-2", 2, "UNCERTAIN")])
    receipt = validate_multi_operand_review_intake(decision_path=decisions, ui_bundle_path=bundle)
    assert receipt["status"] == "ACCEPTED_NON_PROMOTING"
    assert receipt["decision_counts"] == {"APPROVE": 1, "REJECT": 0, "UNCERTAIN": 1}
    assert len(receipt["decisions"][0]["selected_operands"]) == 2
    assert receipt["decisions"][0]["selected_operands"][0]["cell_combination"] == "CELL_SET"
    assert receipt["decisions"][0]["selected_operands"][0]["selected_cells"][1] == {"row_index": 5, "column_index": 3}
    output = tmp_path / "intake"
    write_multi_operand_review_intake(decision_path=decisions, ui_bundle_path=bundle, output_dir=output)
    assert (output / "review_intake_receipt_v1.json").is_file()


def test_rejects_partial_approve(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_bundle()), encoding="utf-8")
    decisions = tmp_path / "decisions.jsonl"
    partial = _decision("packet-1", 1, "APPROVE")
    partial["operand_decisions"] = partial["operand_decisions"][:1]  # type: ignore[index]
    _write_jsonl(decisions, [partial, _decision("packet-2", 2, "UNCERTAIN")])
    with pytest.raises(ValueError, match="every required operand"):
        validate_multi_operand_review_intake(decision_path=decisions, ui_bundle_path=bundle)


def test_rejects_duplicate_or_non_candidate_multi_row_selection(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_bundle()), encoding="utf-8")
    decisions = tmp_path / "decisions.jsonl"
    invalid = _decision("packet-1", 1, "APPROVE")
    invalid["operand_decisions"][0]["selected_cells"][1]["row_index"] = 5  # type: ignore[index]
    invalid["operand_decisions"][0]["selected_cells"][1]["column_index"] = 2  # type: ignore[index]
    _write_jsonl(decisions, [invalid, _decision("packet-2", 2, "UNCERTAIN")])
    with pytest.raises(ValueError, match="repeats a selected cell"):
        validate_multi_operand_review_intake(decision_path=decisions, ui_bundle_path=bundle)
