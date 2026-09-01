from decimal import Decimal

from finance_query.research.source_bound_plan_union import (
    _decimal,
    apply_metric_row_binding_gate,
    answer_diff_counts,
    hydrate_proposal,
)


def _table() -> dict:
    return {
        "internal_table_uid": "u1",
        "document_id": "AAA_financial_statements_2023_separate",
        "source_sha256": "a" * 64,
        "table_sha256": "b" * 64,
        "rows": [["Metric", "2.500"]],
        "headers": ["Nhãn dòng", "2023 triệu VND"],
    }


def _question_plan() -> dict:
    return {
        "family": "direct_lookup",
        "tickers": ["AAA"],
        "years": [2023],
        "scope": "separate",
        "requested_unit": "million_vnd",
        "operands": [
            {
                "operand_id": "x0",
                "metric": "Metric",
                "ticker": "AAA",
                "period": 2023,
                "scope": "separate",
            }
        ],
        "operation_ast": {"op": "lookup", "args": ["x0"]},
    }


def _proposal(answer: str = "2500") -> dict:
    return {
        "question_id": 1,
        "answer_decimal": answer,
        "answer_route": "source_first_exact_row_v1",
        "operation_ast": {"op": "lookup", "args": ["x0"]},
        "claims": {"reporting_scope": "separate", "operands": [_question_plan()["operands"][0]]},
        "evidence": [
            {
                "internal_table_uid": "u1",
                "row_index": 0,
                "column_index": 1,
                "raw_value": "2.500",
                "source_to_vnd_multiplier": "1000000",
                "row_label": "Metric",
            }
        ],
    }


def test_canonical_answer_decimal_precedes_locale_cell_parser():
    assert _decimal("2030418.476") == Decimal("2030418.476")
    assert _decimal("2.030.418.476") == Decimal("2030418476")


def test_hydrate_replays_current_source_bound_lookup():
    candidate = hydrate_proposal(
        _proposal(),
        source_label="candidate",
        question_plan=_question_plan(),
        tables_by_uid={"u1": _table()},
    )
    assert candidate is not None
    assert candidate.replay_status == "PASS"
    assert candidate.source_closure_status == "PASS"
    assert candidate.plan["structural_complete"] is True
    assert candidate.plan["replay_answer_decimal"] == "2500"


def test_answer_diff_uses_submission_numeric_rendering():
    result = answer_diff_counts(
        control_submission=[{"id": 1, "answer": 0.3333333333333333}],
        candidate_rows=[
            {"question_id": 1, "selected_answer_decimal": "0.3333333333333333333333333333"}
        ],
    )
    assert result["changed_answer"] == 0
    assert result["unchanged_answer"] == 1


def test_metric_row_binding_gate_rejects_family_conflict_without_qid_rule():
    question_plan = {
        **_question_plan(),
        "operands": [
            {
                "operand_id": "x0",
                "metric": "Khoản vay Ngân hàng Nhà nước",
                "ticker": "AAA",
                "period": 2023,
                "scope": "separate",
            }
        ],
    }
    proposal = {
        **_proposal(),
        "claims": {"reporting_scope": "separate", "operands": question_plan["operands"]},
        "evidence": [
            {
                "internal_table_uid": "u1",
                "row_index": 0,
                "column_index": 1,
                "raw_value": "2.500",
                "source_to_vnd_multiplier": "1000000",
                "row_label": "Tăng các khoản cho vay khách hàng",
            }
        ],
    }
    table = {**_table(), "rows": [["Tăng các khoản cho vay khách hàng", "2.500"]]}
    candidate = hydrate_proposal(
        proposal,
        source_label="candidate",
        question_plan=question_plan,
        tables_by_uid={"u1": table},
    )
    assert candidate is not None
    gated, rejected = apply_metric_row_binding_gate(
        candidate,
        question_plan=question_plan,
        tables_by_uid={"u1": table},
    )
    assert rejected is True
    assert gated.plan["filter_passed"] is False
    assert "METRIC_ROW_BINDING_GATE_FAILED" in gated.reasons
