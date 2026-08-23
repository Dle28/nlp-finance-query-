from finance_query.hierarchical_retrieval import (
    hierarchy_candidate_score,
    rank_hierarchy_candidates,
)


def test_hierarchy_score_prefers_financial_structure_without_filtering() -> None:
    balance_sheet = {
        "table_function": {"kind": "balance_sheet", "label": "Bảng cân đối kế toán"},
        "table_section": {"kind": "asset", "label": "Tài sản"},
        "table_purpose": {"kind": "financial_statement"},
        "headers": ["Chỉ tiêu", "Năm nay"],
        "rows": [["Tài sản ngắn hạn", "100"]],
    }
    unrelated = {
        "table_function": {"kind": "notes", "label": "Thuyết minh"},
        "table_section": {"kind": "employee"},
        "rows": [["Số lượng nhân viên", "100"]],
    }
    question = "Tài sản ngắn hạn trên bảng cân đối kế toán là bao nhiêu?"
    assert hierarchy_candidate_score(question, balance_sheet) > hierarchy_candidate_score(
        question, unrelated
    )
    ranked = rank_hierarchy_candidates(
        question,
        {"unrelated": unrelated, "balance": balance_sheet},
    )
    assert ranked[0][0] == "balance"

