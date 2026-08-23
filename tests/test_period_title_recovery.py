from __future__ import annotations

import hashlib

from finance_query.period_title_recovery import RECOVERY_METHOD, _recover_candidate


def _fixture(*, question: str, title: str, period_type: str = "duration") -> tuple[dict, dict, dict, dict, dict]:
    rows = [["Chỉ tiêu", "Mã số", "Năm nay", "Năm trước"], ["Doanh thu", "10", "123", "100"]]
    provenance = [[
        {"source_row": row_index, "source_cell": column_index}
        for column_index in range(len(row))
    ] for row_index, row in enumerate(rows)]
    source = {"source_sha256": "a" * 64, "table_sha256": "b" * 64}
    table = {
        "internal_table_uid": "table-1",
        "document_id": "SSH_2024_separate",
        "rows": rows,
        "cell_provenance": provenance,
        "source_provenance": source,
    }
    context = {
        "internal_table_uid": "table-1",
        "document_id": "SSH_2024_separate",
        "source_provenance": source,
        "grid": {"rectangular": True, "provenance_complete": True},
        "quality": {"status": "review_ready"},
        "table_function": {"kind": "income_statement"},
        "context_trace": {"source_title": title},
        "canonical_headers": {"columns": [
            {"column_index": 0, "source_label": "Chỉ tiêu", "header_source_cells": [{"row_index": 0, "column_index": 0}], "role": "value_or_text"},
            {"column_index": 1, "source_label": "Mã số", "header_source_cells": [{"row_index": 0, "column_index": 1}], "role": "reference"},
            {"column_index": 2, "source_label": "Năm nay", "header_source_cells": [{"row_index": 0, "column_index": 2}], "period_labels": ["Năm nay"], "unit_labels": [], "role": "value_or_text"},
            {"column_index": 3, "source_label": "Năm trước", "header_source_cells": [{"row_index": 0, "column_index": 3}], "period_labels": ["Năm trước"], "unit_labels": [], "role": "value_or_text"},
        ]},
        "row_profiles": [
            {"row_index": 0, "numeric_columns": [], "unreliable_numeric_columns": []},
            {"row_index": 1, "numeric_columns": [1, 2, 3], "unreliable_numeric_columns": []},
        ],
    }
    packet = {
        "question": question,
        "question_context": {"entities": ["SSH"], "years": [2024]},
    }
    base_operand = {"column_status": "no_period_column"}
    route_operand = {
        "period_type": period_type,
        "navigation_candidates": [{"internal_table_uid": "table-1", "row_index": 1}],
    }
    return packet, base_operand, route_operand, table, context


def test_duration_current_header_requires_exact_title_date_and_entity_corroboration() -> None:
    values = _fixture(
        question="Doanh thu của CTCP Phát triển Sunshine Homes trong năm 2024 là bao nhiêu?",
        title="Công ty Cổ phần Phát triển Sunshine Homes - Báo cáo riêng cho năm tài chính kết thúc ngày 31 tháng 12 năm 2024. Đơn vị: VND",
    )
    candidate, reason = _recover_candidate(
        packet=values[0], base_operand=values[1], route_operand=values[2],
        table=values[3], context=values[4],
    )
    assert reason == "RECOVERED_EXACT_SOURCE_TITLE_CURRENT_HEADER"
    assert candidate is not None
    assert candidate["column_index"] == 2
    assert candidate["period_labels"] == ["2024"]
    assert candidate["period_source_date"] == "2024-12-31"
    assert candidate["period_resolution_method"] == RECOVERY_METHOD
    assert candidate["period_source_title_sha256"] == hashlib.sha256(
        values[4]["context_trace"]["source_title"].encode("utf-8")
    ).hexdigest()


def test_inferred_alias_without_question_title_overlap_remains_blocked() -> None:
    values = _fixture(
        question="Doanh thu của CTCP Bluemarq Group trong năm 2024 là bao nhiêu?",
        title="Công ty Cổ phần Tập đoàn Đất Xanh - Báo cáo riêng cho năm tài chính kết thúc ngày 31 tháng 12 năm 2024. VND",
    )
    values[0]["question_context"]["entities"] = ["DXG"]
    candidate, reason = _recover_candidate(
        packet=values[0], base_operand=values[1], route_operand=values[2],
        table=values[3], context=values[4],
    )
    assert candidate is None
    assert reason == "QUESTION_SOURCE_ENTITY_NOT_CORROBORATED"


def test_non_year_end_balance_date_must_be_explicit_and_exact_in_question() -> None:
    values = _fixture(
        question="Hàng tồn kho của CTCP Phát triển Sunshine Homes tại ngày 30/09/2024 là bao nhiêu?",
        title="Công ty Cổ phần Phát triển Sunshine Homes - Bảng cân đối kế toán riêng vào ngày 30 tháng 9 năm 2024. VND",
        period_type="instant",
    )
    values[4]["table_function"] = {"kind": "balance_sheet"}
    values[3]["rows"][0][2] = "Số cuối năm"
    values[4]["canonical_headers"]["columns"][2]["source_label"] = "Số cuối năm"
    values[4]["canonical_headers"]["columns"][2]["period_labels"] = ["Số cuối năm"]
    candidate, reason = _recover_candidate(
        packet=values[0], base_operand=values[1], route_operand=values[2],
        table=values[3], context=values[4],
    )
    assert reason == "RECOVERED_EXACT_SOURCE_TITLE_CURRENT_HEADER"
    assert candidate is not None
    assert candidate["period_source_date"] == "2024-09-30"

    values[0]["question"] = "Hàng tồn kho của CTCP Phát triển Sunshine Homes trong năm 2024 là bao nhiêu?"
    candidate, reason = _recover_candidate(
        packet=values[0], base_operand=values[1], route_operand=values[2],
        table=values[3], context=values[4],
    )
    assert candidate is None
    assert reason == "NON_YEAR_END_DATE_NOT_EXPLICIT_IN_QUESTION"

