from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from finance_query.e2e.decimal_executor import execute_typed_plan_shadow
from finance_query.e2e.question_compiler import build_typed_operand_plan


def _item(question: str, *, family: str, tickers: list[str], years: list[int]) -> dict[str, Any]:
    return {
        "id": 1,
        "question": question,
        "question_plan": {
            "family": family,
            "tickers": tickers,
            "years": years,
            "scope": "consolidated",
            "requested_unit": "million_vnd",
            "operands": [],
        },
    }


def _grounded_case(
    values: dict[str, tuple[str, int, str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    inputs: dict[str, dict[str, Any]] = {}
    tables: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    for index, (operand_id, (ticker, year, value)) in enumerate(values.items(), 1):
        uid = f"table-{index}"
        source_cell = {"row": 1, "column": 2}
        tables[uid] = {
            "internal_table_uid": uid,
            "ticker": ticker,
            "report_year": year,
            "scope": "consolidated",
            "unit_hint": "million_vnd",
            "rows": [["header"], ["metric", "note", value]],
            "cell_provenance": [[], [None, None, source_cell]],
        }
        contexts[uid] = {
            "internal_table_uid": uid,
            "canonical_headers": {
                "columns": [{"column_index": 2, "source_label": str(year)}]
            },
            "row_profiles": [{"row_index": 1, "role": "data", "numeric_columns": [2]}],
        }
        inputs[operand_id] = {
            "internal_table_uid": uid,
            "ticker": ticker,
            "report_year": year,
            "scope": "consolidated",
            "source_unit": "million_vnd",
            "binding": {
                "status": "cell_bound",
                "row_index": 1,
                "column_index": 2,
                "raw_value": value,
                "parsed_value": value,
                "parse_warnings": [],
                "column_label": str(year),
                "source_cell": source_cell,
            },
        }
    return inputs, tables, contexts


def _execute(plan: dict[str, Any], values: dict[str, tuple[str, int, str]]) -> dict[str, Any]:
    inputs, tables, contexts = _grounded_case(values)
    return execute_typed_plan_shadow(
        plan,
        inputs,
        source_tables=tables,
        source_contexts=contexts,
    )


def test_cross_entity_difference_positive_canary() -> None:
    plan = build_typed_operand_plan(
        _item(
            "Chênh lệch doanh thu thuần giữa HPG và HSG trong năm 2022 là bao nhiêu?",
            family="cross_entity_comparison",
            tickers=["HPG", "HSG"],
            years=[2022],
        )
    )
    assert plan["route"] == "simple_cross_entity_comparison"
    assert plan["decomposition_status"] == "complete"
    result = _execute(plan, {"x0": ("HPG", 2022, "120"), "x1": ("HSG", 2022, "100")})
    assert result["status"] == "shadow_complete"
    assert result["result_value"] == "20"
    assert result["submission_eligible"] is False


def test_cross_entity_difference_rejects_period_drift() -> None:
    plan = build_typed_operand_plan(
        _item(
            "Chênh lệch doanh thu thuần giữa HPG và HSG trong năm 2022 là bao nhiêu?",
            family="cross_entity_comparison",
            tickers=["HPG", "HSG"],
            years=[2022],
        )
    )
    result = _execute(plan, {"x0": ("HPG", 2022, "120"), "x1": ("HSG", 2023, "100")})
    assert result["status"] == "shadow_blocked"
    assert "x1:typed_period_mismatch" in result["reason_codes"]
    assert result["result_value"] is None


def test_temporal_difference_positive_and_order_canary() -> None:
    plan = build_typed_operand_plan(
        _item(
            "Chênh lệch doanh thu thuần của HPG giữa năm 2023 và năm 2022 là bao nhiêu?",
            family="temporal_change",
            tickers=["HPG"],
            years=[2022, 2023],
        )
    )
    assert plan["route"] == "simple_temporal_comparison"
    assert plan["operation_ast"] == {"op": "subtract", "args": ["x1", "x0"]}
    values = {"x0": ("HPG", 2022, "100"), "x1": ("HPG", 2023, "130")}
    assert _execute(plan, values)["result_value"] == "30"


def test_temporal_difference_rejects_ast_order_tampering() -> None:
    plan = build_typed_operand_plan(
        _item(
            "Chênh lệch doanh thu thuần của HPG giữa năm 2023 và năm 2022 là bao nhiêu?",
            family="temporal_change",
            tickers=["HPG"],
            years=[2022, 2023],
        )
    )
    reversed_plan = deepcopy(plan)
    reversed_plan["operation_ast"] = {"op": "subtract", "args": ["x0", "x1"]}
    result = _execute(
        reversed_plan,
        {"x0": ("HPG", 2022, "100"), "x1": ("HPG", 2023, "130")},
    )
    assert result["status"] == "shadow_blocked"
    assert "typed_plan_fingerprint_mismatch" in result["reason_codes"]
    assert result["result_value"] is None


def test_temporal_difference_rejects_entity_drift() -> None:
    plan = build_typed_operand_plan(
        _item(
            "Chênh lệch doanh thu thuần của HPG giữa năm 2023 và năm 2022 là bao nhiêu?",
            family="temporal_change",
            tickers=["HPG"],
            years=[2022, 2023],
        )
    )
    result = _execute(plan, {"x0": ("HPG", 2022, "100"), "x1": ("HSG", 2023, "130")})
    assert result["status"] == "shadow_blocked"
    assert "x1:typed_entity_mismatch" in result["reason_codes"]
    assert result["result_value"] is None


def test_period_extremum_positive_canary() -> None:
    plan = build_typed_operand_plan(
        _item(
            "HPG có doanh thu thuần cao nhất vào năm nào trong các năm 2021, 2022 và 2023?",
            family="multi_entity_or_period_aggregation",
            tickers=["HPG"],
            years=[2021, 2022, 2023],
        )
    )
    result = _execute(
        plan,
        {
            "x0": ("HPG", 2021, "100"),
            "x1": ("HPG", 2022, "150"),
            "x2": ("HPG", 2023, "120"),
        },
    )
    assert plan["decomposition_status"] == "complete"
    assert result["status"] == "shadow_complete"
    assert result["result_period"] == 2022
    assert result["output_unit"] == "year"
    assert result["submission_eligible"] is False


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("tie", "execution_error:ValueError"),
        ("entity", "x1:typed_entity_mismatch"),
        ("period", "x1:typed_period_mismatch"),
        ("unit", "unit_mismatch"),
        ("scope", "x1:typed_scope_mismatch"),
        ("cell", "x1:raw_value_differs_from_v2"),
    ],
)
def test_period_extremum_fail_closed_canary(mutation: str, reason: str) -> None:
    plan = build_typed_operand_plan(
        _item(
            "HPG có doanh thu thuần cao nhất vào năm nào trong các năm 2021, 2022 và 2023?",
            family="multi_entity_or_period_aggregation",
            tickers=["HPG"],
            years=[2021, 2022, 2023],
        )
    )
    inputs, tables, contexts = _grounded_case(
        {
            "x0": ("HPG", 2021, "100"),
            "x1": ("HPG", 2022, "150"),
            "x2": ("HPG", 2023, "120"),
        }
    )
    if mutation == "tie":
        inputs["x2"]["binding"]["raw_value"] = "150"
        inputs["x2"]["binding"]["parsed_value"] = "150"
        tables["table-3"]["rows"][1][2] = "150"
    elif mutation == "entity":
        inputs["x1"]["ticker"] = "HSG"
        tables["table-2"]["ticker"] = "HSG"
    elif mutation == "period":
        inputs["x1"]["report_year"] = 2024
        inputs["x1"]["binding"]["column_label"] = "2024"
        tables["table-2"]["report_year"] = 2024
        contexts["table-2"]["canonical_headers"]["columns"][0]["source_label"] = "2024"
    elif mutation == "unit":
        inputs["x1"]["source_unit"] = "vnd"
        tables["table-2"]["unit_hint"] = "vnd"
    elif mutation == "scope":
        inputs["x1"]["scope"] = "separate"
        tables["table-2"]["scope"] = "separate"
    else:
        tables["table-2"]["rows"][1][2] = "999"
    result = execute_typed_plan_shadow(
        plan,
        inputs,
        source_tables=tables,
        source_contexts=contexts,
    )
    assert result["status"] == "shadow_blocked"
    assert reason in result["reason_codes"]
    assert result["result_period"] is None
    assert result["submission_eligible"] is False
