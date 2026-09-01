from __future__ import annotations

from finance_query.research.staged_formula_hybrid_source_probe import _source_rows


def test_hybrid_source_rows_preserve_a_metric_label_after_a_code_column() -> None:
    rows = _source_rows(
        table_candidates=[
            {
                "question_id": 1,
                "operand_id": "x0",
                "ticker": "AAA",
                "report_year": 2024,
                "observed_scope": "consolidated",
                "internal_table_uid": "u1",
            }
        ],
        assets={
            "u1": {
                "rows": [["01", "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "100"]]
            }
        },
        query="lưu chuyển tiền thuần từ hoạt động kinh doanh",
        maximum_row_candidates=1,
    )
    assert len(rows) == 1
    assert rows[0]["row_label_column_index"] == 1
    assert rows[0]["row_token_jaccard"] == 1.0
