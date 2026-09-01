from __future__ import annotations

from decimal import Decimal

from finance_query.research.finqa_dsl_eval import evaluate_finqa_items, execute_finqa_program


def test_executes_chained_arithmetic_and_percent_tokens() -> None:
    result, operators = execute_finqa_program(
        "subtract(34.8, 1.2), divide(#0, 34.8), multiply(#1, const_100)",
        [],
    )
    assert abs(result - Decimal("96.551724137931034482758620689655172414")) < Decimal("1e-36")
    assert operators == ["subtract", "divide", "multiply"]
    percent, _ = execute_finqa_program("divide(23.6%, 2)", [])
    assert percent == Decimal("0.118")


def test_executes_table_aggregation() -> None:
    result, operators = execute_finqa_program(
        "table_average(revenue, none)",
        [["metric", "2022", "2023"], ["revenue", "$ 100", "120"]],
    )
    assert result == Decimal("110")
    assert operators == ["table_average"]


def test_reports_pass_unsupported_and_shadow_boundary() -> None:
    items = [
        {"id": "pass", "table": [], "qa": {"program": "add(1, 2)", "exe_ans": 3}},
        {"id": "multiply", "table": [], "qa": {"program": "multiply(2, 3)", "exe_ans": 6}},
        {"id": "unsupported", "table": [], "qa": {"program": "greater(2, 1)", "exe_ans": "yes"}},
    ]
    rows, report = evaluate_finqa_items(items, source_split="dev", benchmark_stage="development")
    assert report["status_counts"] == {"PASS": 2, "UNSUPPORTED": 1}
    assert report["shadow_status_counts"] == {"BLOCKED": 2, "ELIGIBLE": 1}
    assert rows[0]["source_contract"]["submission_eligible"] is False
