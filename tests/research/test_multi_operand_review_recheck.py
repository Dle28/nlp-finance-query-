from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.research.multi_operand_review_intake import (
    write_multi_operand_review_intake,
)
from finance_query.research.multi_operand_review_recheck import (
    recheck_multi_operand_review,
    write_multi_operand_review_recheck,
)


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    asset = {
        "internal_table_uid": "table-1",
        "document_id": "AAA_financial_statements_2024_separate",
        "source_path": str(tmp_path / "source" / "AAA.txt"),
        "source_sha256": "source-1",
        "table_sha256": "table-1-sha",
        "local_ordinal": 3,
        "char_start": 200,
        "page_no": 7,
        "scope": "separate",
        "report_year": 2024,
        "unit_hint": "triệu đồng",
        "headers": ["Chỉ tiêu", "Năm 2024 (triệu đồng)"],
        "header_row_indices": [0],
        "rows": [
            ["Chỉ tiêu", "Năm 2024 (triệu đồng)"],
            ["Chỉ tiêu A", "111.000"],
            ["Chỉ tiêu B", "222.000"],
        ],
    }
    locator = {
        "source_path": asset["source_path"],
        "source_sha256": asset["source_sha256"],
        "table_sha256": asset["table_sha256"],
        "local_ordinal": asset["local_ordinal"],
        "char_start": asset["char_start"],
        "page_no": asset["page_no"],
    }
    candidate = {
        "tableUid": asset["internal_table_uid"],
        "selectableCells": [
            {"rowIndex": 1, "columnIndex": 1},
            {"rowIndex": 2, "columnIndex": 1},
        ],
        "source": {
            "locatorSha256": _canonical_sha(locator),
            "sourceSha256": asset["source_sha256"],
            "tableSha256": asset["table_sha256"],
        },
    }
    operands = [
        {"operandId": "x0", "routeId": "route-x0", "ticker": "AAA", "reportYear": 2024, "requestedScope": "separate", "candidates": [candidate]},
        {"operandId": "x1", "routeId": "route-x1", "ticker": "AAA", "reportYear": 2024, "requestedScope": "separate", "candidates": [candidate]},
    ]
    bundle = {
        "protocol": "vifinqa_multi_operand_review_ui_v2",
        "sourceQueueSha256": "queue-sha",
        "requiredOperandCount": 2,
        "sourceContract": {
            "numericFinancialValuesExposed": False,
            "maySelectValue": False,
            "mayExecuteFormula": False,
            "mayAuthorizeEvidence": False,
            "mayAuthorizeAnswer": False,
            "trainingEligible": False,
            "submissionEligible": False,
        },
        "items": [{"packetId": "packet-1", "questionId": 11, "operands": operands}],
    }
    decision_operands = [
        {
            "operand_id": "x0",
            "route_id": "route-x0",
            "selected_internal_table_uid": "table-1",
            "cell_combination": "SINGLE_CELL",
            "selected_cells": [{"row_index": 1, "column_index": 1}],
            "selected_exact_table_locator_sha256": candidate["source"]["locatorSha256"],
            "selected_source_sha256": "source-1",
            "selected_table_sha256": "table-1-sha",
            "review_confirmations": {"table_subject": True, "scope": True, "period": True, "unit": True},
        },
        {
            "operand_id": "x1",
            "route_id": "route-x1",
            "selected_internal_table_uid": "table-1",
            "cell_combination": "CELL_SET",
            "selected_cells": [{"row_index": 1, "column_index": 1}, {"row_index": 2, "column_index": 1}],
            "selected_exact_table_locator_sha256": candidate["source"]["locatorSha256"],
            "selected_source_sha256": "source-1",
            "selected_table_sha256": "table-1-sha",
            "review_confirmations": {"table_subject": True, "scope": True, "period": True, "unit": True},
        },
    ]
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(
        decisions,
        [{
            "protocol": "vifinqa_multi_operand_review_forms_v2",
            "source_multi_operand_queue_sha256": "queue-sha",
            "packet_id": "packet-1",
            "question_id": 11,
            "review_state": "REVIEWED",
            "overall_decision": "APPROVE",
            "operand_decisions": decision_operands,
            "reviewer_id": "reviewer-1",
            "reviewed_at": "2026-08-26T10:00:00Z",
            "reviewer_notes": "",
            "human_verified": False,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
        }],
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    assets_path = tmp_path / "assets.jsonl"
    _write_jsonl(assets_path, [asset])
    plans_path = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans_path,
        [{
            "question_id": 11,
            "operands": [
                {"operand_id": "x0", "ticker": "AAA", "years": [2024], "scope": "separate", "required": True},
                {"operand_id": "x1", "ticker": "AAA", "years": [2024], "scope": "separate", "required": True},
            ],
        }],
    )
    intake_dir = tmp_path / "intake"
    write_multi_operand_review_intake(
        decision_path=decisions, ui_bundle_path=bundle_path, output_dir=intake_dir
    )
    return {
        "intake_dir": intake_dir,
        "decisions": decisions,
        "bundle": bundle_path,
        "assets": assets_path,
        "plans": plans_path,
        "output": tmp_path / "recheck",
    }


def test_rechecks_hash_bound_source_coordinates_without_exposing_values(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    receipt = write_multi_operand_review_recheck(
        intake_dir=paths["intake_dir"], decision_path=paths["decisions"],
        ui_bundle_path=paths["bundle"], assets_path=paths["assets"],
        plans_path=paths["plans"], output_dir=paths["output"],
    )
    assert receipt["status"] == "RECHECKED_NON_PROMOTING"
    assert receipt["review_batch"] == {
        "approved_question_count": 1,
        "approved_operand_count": 2,
        "selected_cell_count": 3,
    }
    assert receipt["recheck_status_counts"] == {"STRUCTURALLY_RECHECKED": 2}
    assert receipt["rechecks"][1]["selected_cells"] == [
        {"row_index": 1, "column_index": 1},
        {"row_index": 2, "column_index": 1},
    ]
    rendered = (paths["output"] / "source_recheck_receipt_v1.json").read_text(encoding="utf-8")
    assert "111.000" not in rendered and "222.000" not in rendered
    assert receipt["authorization"]["may_authorize_answer"] is False


def test_recheck_refuses_decisions_changed_after_intake(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    changed = json.loads(paths["decisions"].read_text(encoding="utf-8"))
    changed["reviewer_id"] = "someone-else"
    _write_jsonl(paths["decisions"], [changed])
    with pytest.raises(ValueError, match="intake decisions hash mismatch"):
        recheck_multi_operand_review(
            intake_dir=paths["intake_dir"], decision_path=paths["decisions"],
            ui_bundle_path=paths["bundle"], assets_path=paths["assets"],
            plans_path=paths["plans"],
        )
