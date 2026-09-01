from __future__ import annotations

from finance_query.research.staged_formula_current_period_probe import (
    _current_header_candidate,
    _current_header_row_margin_candidate,
    _current_period_status,
    _safe_navigation_descriptor,
)


def _provenance(rows: list[list[str]]) -> list[list[dict[str, int]]]:
    return [
        [{"source_row": row_index, "source_column": column_index} for column_index in range(len(row))]
        for row_index, row in enumerate(rows)
    ]


def test_source_title_current_header_is_only_a_strict_navigation_candidate() -> None:
    rows = [["Chỉ tiêu", "Năm nay"], ["Lợi nhuận", "100"]]
    source = {
        "source_path": "/immutable/AAA.txt",
        "source_sha256": "a" * 64,
        "table_sha256": "b" * 64,
        "char_start": 10,
    }
    table = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2024_consolidated",
        "local_ordinal": 1,
        "page_no": 2,
        "source_provenance": source,
        "rows": rows,
        "cell_provenance": _provenance(rows),
    }
    context = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2024_consolidated",
        "source_provenance": source,
        "grid": {"rectangular": True, "provenance_complete": True},
        "quality": {"status": "review_ready"},
        "table_function": {"kind": "income_statement"},
        "context_trace": {
            "source_title": "Báo cáo kết quả hoạt động kinh doanh cho năm tài chính kết thúc ngày 31 tháng 12 năm 2024. Đơn vị: VND"
        },
        "canonical_headers": {
            "columns": [
                {"column_index": 0, "source_label": "Chỉ tiêu", "header_source_cells": [{"row_index": 0, "column_index": 0}]},
                {"column_index": 1, "source_label": "Năm nay", "header_source_cells": [{"row_index": 0, "column_index": 1}]},
            ]
        },
        "row_profiles": [
            {"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}
        ],
    }
    operand = {
        "operand_id": "aaa_profit_2024",
        "ticker": "AAA",
        "years": [2024],
        "allowed_table_functions": ["income_statement"],
    }
    source_row = {
        "question_id": 1,
        "internal_table_uid": "u1",
        "report_year": 2024,
        "ticker": "AAA",
        "observed_scope": "consolidated",
        "row_index": 1,
        "row_rank": 1,
        "row_label": "Lợi nhuận",
        "row_token_jaccard": 1.0,
        "numeric_cell_indices": [1],
    }
    candidate, checks = _current_header_candidate(
        row=source_row,
        operand=operand,
        table=table,
        context=context,
        minimum_row_jaccard=0.9,
    )
    assert candidate is not None
    assert all(checks.values())
    final, margin_checks = _current_header_row_margin_candidate(
        candidate=candidate,
        rows=[
            {
                "row_index": 1,
                "row_rank": 1,
                "row_label_token_jaccard": 1.0,
                "numeric_column_indices": [1],
            }
        ],
        table=table,
        context=context,
        minimum_row_jaccard=0.9,
        minimum_row_margin=0.2,
    )
    assert final == ("u1", 1, 1)
    assert all(margin_checks.values())
    descriptor = _safe_navigation_descriptor(
        candidate=candidate,
        source_row=source_row,
        context=context,
        final=final,
    )
    assert descriptor["observed_scope"] == "consolidated"
    assert descriptor["period_resolution"] == "source_title_plus_current_header"
    assert "100" not in str(descriptor)
    assert "row_index" not in descriptor
    status = _current_period_status(
        source_rows=[source_row],
        operand=operand,
        tables={"u1": table},
        contexts={"u1": context},
        minimum_row_jaccard=0.9,
        minimum_row_margin=0.2,
    )
    assert status["operand_status"] == "UNIQUE_CURRENT_PERIOD_RECHECK_CANDIDATE"
    assert "100" not in str(status)
