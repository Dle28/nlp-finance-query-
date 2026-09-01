from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.multi_operand_machine_diagnostic import (
    build_multi_operand_machine_diagnostic,
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _asset(uid: str, rows: list[list[str]]) -> dict[str, object]:
    return {
        "internal_table_uid": uid,
        "source_path": f"/source/{uid}.txt",
        "source_sha256": f"source-{uid}",
        "table_sha256": f"table-{uid}",
        "local_ordinal": 1,
        "char_start": 100,
        "page_no": 1,
        "scope": "separate",
        "report_year": 2024,
        "unit_hint": "triệu đồng",
        "headers": ["Chỉ tiêu", "Năm 2024 (triệu đồng)"],
        "header_row_indices": [0],
        "rows": rows,
    }


def test_machine_diagnostic_emits_a_baseline_and_non_authorizing_composition_variant(
    tmp_path: Path,
) -> None:
    assets = [
        _asset("a", [["Chỉ tiêu", "Năm 2024 (triệu đồng)"], ["Chi phí A", "10.000"]]),
        _asset("b", [["Chỉ tiêu", "Năm 2024 (triệu đồng)"], ["Chi phí B", "20.000"]]),
        _asset(
            "c",
            [
                ["Chỉ tiêu", "Năm 2024 (triệu đồng)"],
                ["Dự phòng chung cho vay khách hàng", "2.000"],
                ["Dự phòng cụ thể cho vay khách hàng", "6.000"],
            ],
        ),
    ]
    assets_path = tmp_path / "assets.jsonl"
    _write_jsonl(assets_path, assets)
    plans_path = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans_path,
        [
            {
                "question_id": 1,
                "requested_unit": "billion_vnd",
                "operation_ast": {"op": "mean", "args": ["x0", "x1", "x2"]},
                "operands": [
                    {
                        "operand_id": operand_id,
                        "required": True,
                        "years": [2024],
                        "scope": "separate",
                        "unit_contract": {"requested_unit": "billion_vnd"},
                    }
                    for operand_id in ("x0", "x1", "x2")
                ],
            }
        ],
    )
    proposals = [
        {
            "question_id": 1,
            "operand_id": "x0",
            "internal_table_uid": "a",
            "proposed_selected_cells": [{"row_index": 1, "column_index": 1}],
        },
        {
            "question_id": 1,
            "operand_id": "x1",
            "internal_table_uid": "b",
            "proposed_selected_cells": [{"row_index": 1, "column_index": 1}],
        },
        {
            "question_id": 1,
            "operand_id": "x2",
            "internal_table_uid": "c",
            "proposed_selected_cells": [{"row_index": 2, "column_index": 1}],
            "composition_hypothesis": {
                "operator_candidate": "sum",
                "sibling_row_index": 1,
                "sibling_column_index": 1,
            },
        },
    ]
    review_dir = tmp_path / "machine-review"
    review_dir.mkdir()
    review_path = review_dir / "machine_research_review_v1.json"
    review_path.write_text(
        json.dumps(
            {
                "status": "MACHINE_RESEARCH_REVIEW_COMPLETE_NON_PROMOTING",
                "authorization": {"reviewer_type": "machine_research", "submission_eligible": False},
                "question_summary": [
                    {"question_id": 1, "machine_research_status": "NEEDS_COMPOSITION_HYPOTHESIS"}
                ],
            }
        ),
        encoding="utf-8",
    )
    proposal_path = review_dir / "machine_adjustment_proposals_v1.jsonl"
    _write_jsonl(proposal_path, proposals)
    (review_dir / "manifest.json").write_text(
        json.dumps(
            {
                "protocol": "vifinqa_multi_operand_machine_review_v1",
                "outputs": {
                    "machine_research_review_v1.json": {"sha256": _sha_file(review_path)},
                    "machine_adjustment_proposals_v1.jsonl": {"sha256": _sha_file(proposal_path)},
                },
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "diagnostic"
    report = build_multi_operand_machine_diagnostic(
        machine_review_dir=review_dir,
        plans_path=plans_path,
        assets_path=assets_path,
        output_dir=output,
    )

    assert report["status"] == "MACHINE_DIAGNOSTIC_CANDIDATES_COMPLETE_NON_PROMOTING"
    assert report["candidate_count"] == 2
    by_variant = {item["variant"]: item for item in report["candidates"]}
    assert by_variant["machine_adjusted_selection"]["diagnostic_result"] == "12"
    assert by_variant["composition_hypothesis_common_plus_specific"]["diagnostic_result"].startswith(
        "12.666666666666666666666666666666666667"
    )
    assert all(item["submission_eligible"] is False for item in report["candidates"])
    assert all(
        "RESEARCH_CONVERSION_OUTSIDE_PLAN_CONTRACT" in item["assumption_codes"]
        for item in report["candidates"]
    )
    recommendation = report["evaluation_recommendations"][0]
    assert recommendation["primary_variant"] == "composition_hypothesis_common_plus_specific"
    assert recommendation["evaluation_priority"] == "COMPARE_PRIMARY_WITH_BASELINE"
    assert recommendation["conditions"] == ["RESEARCH_CONVERSION_OUTSIDE_PLAN_CONTRACT"]
    rendered = (output / "machine_diagnostic_candidates_v1.json").read_text(encoding="utf-8")
    assert "10.000" not in rendered and "20.000" not in rendered and "6.000" not in rendered
