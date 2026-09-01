from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.multi_operand_review import (
    _recover_misclassified_header_rows,
    build_multi_operand_review,
    validate_multi_operand_review,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _paths(tmp_path: Path) -> dict[str, Path]:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "protocol": "vifinqa_multi_operand_review_v1",
                "question_ids": [11],
                "review": {"max_tables_per_operand": 1, "max_rows_per_table": 3},
            }
        ),
        encoding="utf-8",
    )
    plans = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans,
        [
            {
                "question_id": 11,
                "question": "Hiệu số chỉ tiêu AAA và BBB năm 2024 là bao nhiêu?",
                "requested_unit": "million_vnd",
                "operation_ast": {"op": "subtract", "args": ["x0", "x1"]},
                "operands": [
                    {
                        "operand_id": "x0", "ticker": "AAA", "years": [2024], "scope": "separate",
                        "required": True, "role": "comparison_value", "metric_hints": ["Chi phí kiểm thử"],
                        "unit_contract": {"requested_unit": "million_vnd"},
                    },
                    {
                        "operand_id": "x1", "ticker": "BBB", "years": [2024], "scope": "separate",
                        "required": True, "role": "comparison_value", "metric_hints": ["Chi phí kiểm thử"],
                        "unit_contract": {"requested_unit": "million_vnd"},
                    },
                ],
            }
        ],
    )
    assets = tmp_path / "assets.jsonl"
    asset_rows: list[dict[str, object]] = []
    routes: list[dict[str, object]] = []
    for operand, ticker in (("x0", "AAA"), ("x1", "BBB")):
        source_hash = f"source-{ticker}"
        table_hash = f"table-{ticker}"
        uid = f"uid-{ticker}"
        asset_rows.append(
            {
                "internal_table_uid": uid,
                "document_id": f"{ticker}_financial_statements_2024_separate",
                "source_path": str(tmp_path / "data" / f"{ticker}.txt"),
                "source_sha256": source_hash,
                "table_sha256": table_hash,
                "local_ordinal": 3,
                "char_start": 200,
                "page_no": 7,
                "scope": "separate",
                "report_year": 2024,
                "unit_hint": "triệu đồng",
                "table_function": {"label": "Bảng kết quả"},
                "headers": ["Chỉ tiêu", "Năm 2024 (triệu đồng)"],
                "header_row_indices": [0],
                "rows": [["Chỉ tiêu", "Năm 2024"], ["Chi phí kiểm thử", "123.456"]],
            }
        )
        routes.append(
            {
                "question_id": 11,
                "operand_id": operand,
                "route_id": f"route-{operand}",
                "ticker": ticker,
                "report_year": 2024,
                "requested_scope": "separate",
                "metric_core_query": "Chi phí kiểm thử",
                "candidates": [
                    {
                        "internal_table_uid": uid,
                        "retrieval_support": "lexical_and_dense",
                        "exact_table_locator_sha256": f"locator-{ticker}",
                        "exact_table_locator": {
                            "source_sha256": source_hash,
                            "table_sha256": table_hash,
                        },
                    }
                ],
            }
        )
    _write_jsonl(assets, asset_rows)
    hybrid = tmp_path / "hybrid.jsonl"
    _write_jsonl(hybrid, routes)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "status": "ACCEPTED_NON_PROMOTING",
                "authorization": {"human_verified_count": 0},
                "decisions": [{"question_id": 11, "decision": "UNCERTAIN"}],
            }
        ),
        encoding="utf-8",
    )
    return {
        "config": config, "plans": plans, "assets": assets, "hybrid": hybrid,
        "receipt": receipt, "output": tmp_path / "output",
    }


def test_groups_all_required_operands_and_redacts_source_values(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    coverage = build_multi_operand_review(
        config_path=paths["config"], plans_path=paths["plans"],
        hybrid_review_queue_path=paths["hybrid"], assets_path=paths["assets"],
        prior_review_receipt_path=paths["receipt"], output_dir=paths["output"],
        repo_root=tmp_path,
    )
    queue = [json.loads(line) for line in (paths["output"] / "multi_operand_review_queue_v1.jsonl").read_text().splitlines()]
    assert coverage["required_operand_count"] == 2
    assert len(queue) == 1 and [row["operand_id"] for row in queue[0]["operands"]] == ["x0", "x1"]
    assert {tuple(cell.values()) for cell in queue[0]["operands"][0]["candidates"][0]["selectable_cells"]} == {(1, 1)}
    visible = json.dumps(queue, ensure_ascii=False)
    assert "123.456" not in visible
    assert "[SỐ ĐÃ ẨN]" in visible
    assert validate_multi_operand_review(paths["output"], expected_question_ids=[11])["status"] == "PASS"


def test_refuses_to_continue_a_question_not_unresolved(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths["receipt"].write_text(
        json.dumps(
            {
                "status": "ACCEPTED_NON_PROMOTING",
                "authorization": {"human_verified_count": 0},
                "decisions": [{"question_id": 11, "decision": "APPROVE"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unresolved"):
        build_multi_operand_review(
            config_path=paths["config"], plans_path=paths["plans"],
            hybrid_review_queue_path=paths["hybrid"], assets_path=paths["assets"],
            prior_review_receipt_path=paths["receipt"], output_dir=paths["output"],
            repo_root=tmp_path,
        )


def test_can_prepend_a_feedback_targeted_candidate_without_promoting_it(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    config = json.loads(paths["config"].read_text(encoding="utf-8"))
    config["candidate_overrides"] = {"11": {"x1": {"prepend_table_uids": ["uid-BBB"]}}}
    paths["config"].write_text(json.dumps(config), encoding="utf-8")
    build_multi_operand_review(
        config_path=paths["config"], plans_path=paths["plans"],
        hybrid_review_queue_path=paths["hybrid"], assets_path=paths["assets"],
        prior_review_receipt_path=paths["receipt"], output_dir=paths["output"],
        repo_root=tmp_path,
    )
    queue = [json.loads(line) for line in (paths["output"] / "multi_operand_review_queue_v1.jsonl").read_text().splitlines()]
    candidate = queue[0]["operands"][1]["candidates"][0]
    assert candidate["table_uid"] == "uid-BBB"
    assert queue[0]["may_authorize_answer"] is False


def test_recovers_metric_rows_misclassified_as_ocr_headers_without_values() -> None:
    rows = _recover_misclassified_header_rows(
        {
            "header_row_indices": [0, 1, 2],
            "rows": [
                ["", "Năm 2024"],
                ["Trích lập dự phòng cho vay khách hàng", "123.456"],
                ["Trích lập dự phòng khác", "22.000"],
            ],
        },
        query="trích lập dự phòng cho vay khách hàng",
        maximum=3,
    )
    assert rows[0]["row_index"] == 1
    assert rows[0]["row_candidate_origin"] == "OCR_HEADER_ROW_RECOVERY"
    assert "123.456" not in json.dumps(rows, ensure_ascii=False)
