from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.multi_operand_machine_review import (
    build_multi_operand_machine_review,
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _asset(uid: str, rows: list[list[str]]) -> dict[str, object]:
    return {
        "internal_table_uid": uid, "source_path": f"/source/{uid}.txt", "source_sha256": f"source-{uid}",
        "table_sha256": f"table-{uid}", "local_ordinal": 3, "char_start": 200, "page_no": 7,
        "scope": "separate", "report_year": 2024, "unit_hint": "triệu đồng",
        "headers": ["Chỉ tiêu", "Năm 2024 (triệu đồng)"], "header_row_indices": [0],
        "rows": rows,
    }


def _decision(question_id: int, operand_id: str, asset: dict[str, object], cells: list[dict[str, int]]) -> dict[str, object]:
    locator = {key: asset[key] for key in ("source_path", "source_sha256", "table_sha256", "local_ordinal", "char_start", "page_no")}
    return {
        "question_id": question_id, "overall_decision": "APPROVE", "operand_decisions": [{
            "operand_id": operand_id, "route_id": f"route-{operand_id}", "selected_internal_table_uid": asset["internal_table_uid"],
            "selected_cells": cells, "selected_source_sha256": asset["source_sha256"], "selected_table_sha256": asset["table_sha256"],
            "selected_exact_table_locator_sha256": _canonical_sha(locator),
        }],
    }


def test_machine_review_reduces_a_unique_exact_label_and_flags_component_hypothesis(tmp_path: Path) -> None:
    exact_asset = _asset("table-exact", [
        ["Chỉ tiêu", "Năm 2024"], ["Các khoản phải thu bên ngoài", "100.000"], ["Các khoản phải thu bên ngoài khác", "200.000"],
    ])
    component_asset = _asset("table-component", [
        ["Chỉ tiêu", "Năm 2024"], ["Trích lập dự phòng chung cho vay khách hàng", "10.000"], ["Trích lập dự phòng cụ thể cho vay khách hàng", "20.000"],
    ])
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(decisions, [
        _decision(1, "x0", exact_asset, [{"row_index": 1, "column_index": 1}, {"row_index": 2, "column_index": 1}]),
        _decision(2, "x0", component_asset, [{"row_index": 2, "column_index": 1}]),
    ])
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "manifest.json").write_text(json.dumps({"protocol": "vifinqa_multi_operand_review_intake_v1", "inputs": {"decisions": {"sha256": _sha_file(decisions)}}}), encoding="utf-8")
    (intake / "review_intake_receipt_v1.json").write_text(json.dumps({"status": "ACCEPTED_NON_PROMOTING", "authorization": {"may_authorize_answer": False}}), encoding="utf-8")
    plans = tmp_path / "plans.jsonl"
    _write_jsonl(plans, [
        {"question_id": 1, "question": "Hiệu số khoản phải thu bên ngoài", "operands": [{"operand_id": "x0", "metric_hints": ["khoản phải thu bên ngoài"], "years": [2024], "scope": "separate"}]},
        {"question_id": 2, "question": "Chi phí trích lập dự phòng rủi ro tín dụng cho vay khách hàng", "operands": [{"operand_id": "x0", "metric_hints": ["trích lập dự phòng rủi ro tín dụng cho vay khách hàng"], "years": [2024], "scope": "separate"}]},
    ])
    assets = tmp_path / "assets.jsonl"
    _write_jsonl(assets, [exact_asset, component_asset])
    output = tmp_path / "output"
    report = build_multi_operand_machine_review(
        intake_dir=intake, decision_path=decisions, plans_path=plans, assets_path=assets, output_dir=output
    )
    assert report["status_counts"] == {"AUTO_REVISED_EXACT_LABEL": 1, "NEEDS_COMPOSITION_HYPOTHESIS": 1}
    exact = next(value for value in report["operand_reviews"] if value["question_id"] == 1)
    assert exact["recommended_cells"] == [{"row_index": 1, "column_index": 1}]
    component = next(value for value in report["operand_reviews"] if value["question_id"] == 2)
    assert component["composition_hypothesis"]["operator_candidate"] == "sum"
    rendered = (output / "machine_research_review_v1.json").read_text(encoding="utf-8")
    assert "100.000" not in rendered and "200.000" not in rendered and "20.000" not in rendered
    proposals = [json.loads(line) for line in (output / "machine_adjustment_proposals_v1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert proposals[0]["proposed_selected_cells"] == [{"column_index": 1, "row_index": 1}]
    assert all(proposal["submission_eligible"] is False for proposal in proposals)
    assert report["authorization"]["submission_eligible"] is False
