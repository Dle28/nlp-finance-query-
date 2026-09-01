from finance_query.research.semantic_binding_feedback import (
    audit_metric_row_binding,
    audit_selected_plan,
)
from finance_query.research import semantic_binding_feedback as semantic_feedback


def test_metric_row_binding_accepts_domain_abbreviation_and_entity_suffix():
    result = audit_metric_row_binding(
        "Chi phí thuế thu nhập doanh nghiệp theo thuế suất hiện hành "
        "Ngân hàng TMCP Phương Đông năm",
        "Chi phí thuế TNDN theo thuế suất hiện hành",
    )
    assert result["status"] == "PASS"
    assert result["reason_codes"] == ["METRIC_ROW_BOUND"]


def test_metric_row_binding_rejects_conflicting_loan_qualifier():
    result = audit_metric_row_binding(
        "31/12/2018, khoản vay Ngân hàng Nhà nước Ngân hàng TMCP "
        "Phát triển Thành phố Hồ Chí Minh",
        "Tăng các khoản cho vay khách hàng",
    )
    assert result["status"] == "FAIL"
    assert "METRIC_ROW_QUALIFIER_CONFLICT" in result["reason_codes"]


def test_metric_row_binding_is_unknown_when_a_label_is_missing():
    result = audit_metric_row_binding("Lãi tiền gửi", None)
    assert result["status"] == "UNKNOWN"
    assert "METRIC_ROW_LABEL_MISSING" in result["reason_codes"]


def test_row_label_lookup_handles_code_column_before_label_column():
    question_plan = {
        "family": "direct_lookup",
        "requested_unit": "million_vnd",
        "operands": [{"metric": "Lợi nhuận sau thuế", "operand_id": "x0"}],
        "operation_ast": {"op": "lookup", "args": ["x0"]},
    }
    selected_plan = {
        "operands": [
            {
                "operand_id": "x0",
                "source": {
                    "source_uid": "u1",
                    "row_index": 0,
                    "column_index": 2,
                },
            }
        ],
        "selection_evidence": [
            {
                "internal_table_uid": "u1",
                "row_index": 0,
                "column_index": 2,
                "row_label": "Lợi nhuận sau thuế",
                "source_to_vnd_multiplier": "1000000",
            }
        ],
    }
    result = audit_selected_plan(
        question_plan,
        selected_plan,
        {
            "u1": {
                "headers": ["Mã số", "Nhãn dòng", "2023 Triệu VND"],
                "rows": [["60", "Lợi nhuận sau thuế", "2.500"]],
            }
        },
    )
    assert result["status"] == "PASS"
    assert "SOURCE_ROW_LABEL_STALE" not in result["reason_codes"]


def test_unit_inference_does_not_treat_ty_inside_ty_le_as_billion():
    table = {
        "headers": ["Vốn điều lệ VND", "Tỷ lệ %"],
        "unit_hint": "vnd",
        "context_trace": {"unit_labels": ["VND", "%"]},
    }
    assert semantic_feedback._table_unit_multiplier(table) == semantic_feedback.Decimal("1")
