from __future__ import annotations

from finance_query.research.staged_formula_metric_phrase_probe import (
    _combined_status,
    _collapse_verified_page_continuations,
    _explicit_year_end_header_candidate,
    _header_role_matches,
    _matches_rule,
    _role_header_candidate,
    _row_has_required_account_code,
    _semantic_rows,
    _self_describing_role_cell_candidate,
)


def test_metric_phrase_match_compacts_ocr_spacing_but_honours_exclusions() -> None:
    cfo_rule = {
        "required": ("luuchuyentienthuantuhoatdongkinhdoanh",),
        "forbidden": (),
    }
    revenue_rule = {
        "required": ("doanhthuthuan",),
        "forbidden": ("doanhthukhac",),
    }
    assert _matches_rule("Lưu chuyển tiền thuần từhoạt động kinh doanh", cfo_rule)
    assert _matches_rule("Doanh thu thuần từ bán hàng ra bên ngoài", revenue_rule)
    assert not _matches_rule("Doanh thu khác", revenue_rule)
    assert not _matches_rule(
        "Quỹ khác thuộc vốn chủ sở hữu",
        {"required": ("vonchusohuu",), "forbidden": ("quykhacthuoc",)},
    )


def test_primary_statement_rule_excludes_a_segment_schedule_with_the_same_row_label() -> None:
    candidates = [
        {"question_id": 1, "operand_id": "equity", "ticker": "AAA", "report_year": 2019, "internal_table_uid": "main"},
        {"question_id": 1, "operand_id": "equity", "ticker": "AAA", "report_year": 2019, "internal_table_uid": "segment"},
    ]
    assets = {
        "main": {
            "internal_table_uid": "main",
            "table_function": {"kind": "balance_sheet"},
            "table_purpose": {"kind": "period_comparison"},
            "rows": [["Vốn chủ sở hữu", "123"]],
        },
        "segment": {
            "internal_table_uid": "segment",
            "table_function": {"kind": "balance_sheet"},
            "table_purpose": {"kind": "quantitative_detail"},
            "rows": [["Vốn chủ sở hữu", "456"]],
        },
    }
    rows = _semantic_rows(
        table_candidates=candidates,
        assets=assets,
        rule={"required": ("vonchusohuu",), "forbidden": (), "primary_statement_only": True},
    )
    assert [row["internal_table_uid"] for row in rows] == ["main"]


def test_phrase_rows_preserve_scope_from_a_table_candidate_packet() -> None:
    rows = _semantic_rows(
        table_candidates=[{"question_id": 1, "operand_id": "equity", "ticker": "AAA", "report_year": 2019, "scope": "consolidated", "internal_table_uid": "main"}],
        assets={
            "main": {
                "internal_table_uid": "main",
                "table_function": {"kind": "balance_sheet"},
                "table_purpose": {"kind": "period_comparison"},
                "rows": [["Vốn chủ sở hữu", "123"]],
            }
        },
        rule={"required": ("vonchusohuu",), "forbidden": (), "primary_statement_only": True},
    )
    assert rows[0]["observed_scope"] == "consolidated"


def test_account_code_rule_distinguishes_total_equity_from_its_component() -> None:
    rule = {"required_account_codes": ("400",)}
    assert _row_has_required_account_code(["VỐN CHỦ SỞ HỮU", "400", "123"], rule)
    assert not _row_has_required_account_code(["Vốn chủ sở hữu", "410", "123"], rule)


def test_explicit_year_end_header_uses_the_header_unit_and_keeps_provenance_gates() -> None:
    source = {"source_path": "x.txt", "source_sha256": "source", "table_sha256": "table", "char_start": 0}
    table = {
        "internal_table_uid": "uid", "document_id": "PLX_2019", "local_ordinal": 1, "source_provenance": source,
        "rows": [["Nhãn", "31/12/2019 VND"], ["Vốn chủ sở hữu", "123"]],
        "cell_provenance": [[{}, {}], [{}, {}]],
    }
    context = {
        "internal_table_uid": "uid", "document_id": "PLX_2019", "source_provenance": source,
        "grid": {"rectangular": True, "provenance_complete": True}, "quality": {"status": "review_ready"},
        "table_function": {"kind": "balance_sheet"},
        "canonical_headers": {"columns": [{"column_index": 1, "source_label": "31/12/2019 VND", "header_source_cells": [{"row_index": 0, "column_index": 1}]}]},
        "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
    }
    candidate, checks = _explicit_year_end_header_candidate(
        row={"question_id": 1, "row_index": 1, "row_token_jaccard": 1.0, "report_year": 2019, "ticker": "PLX"},
        operand={"ticker": "PLX", "years": [2019], "allowed_table_functions": ["balance_sheet"], "scope": None},
        table=table, context=context, minimum_row_jaccard=0.8,
    )
    assert candidate is not None
    assert all(checks.values())


def test_explicit_year_end_header_allows_only_an_explicit_financial_note_contract() -> None:
    source = {"source_path": "x.txt", "source_sha256": "source", "table_sha256": "table", "char_start": 0}
    table = {
        "internal_table_uid": "uid", "document_id": "PC1_2021", "local_ordinal": 1, "source_provenance": source,
        "rows": [["Nhãn", "31/12/2021 VND"], ["Các khoản phải thu khách hàng khác", "123"]],
        "cell_provenance": [[{}, {}], [{}, {}]],
    }
    context = {
        "internal_table_uid": "uid", "document_id": "PC1_2021", "source_provenance": source,
        "grid": {"rectangular": True, "provenance_complete": True}, "quality": {"status": "review_ready"},
        "table_function": {"kind": "financial_note"},
        "canonical_headers": {"columns": [{"column_index": 1, "source_label": "31/12/2021 VND", "header_source_cells": [{"row_index": 0, "column_index": 1}]}]},
        "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
    }
    row = {
        "question_id": 1,
        "row_index": 1,
        "row_token_jaccard": 1.0,
        "report_year": 2021,
        "ticker": "PC1",
        "observed_scope": "separate",
    }
    note_operand = {"ticker": "PC1", "years": [2021], "allowed_table_functions": ["financial_note_detail"], "scope": "separate"}
    candidate, checks = _explicit_year_end_header_candidate(row=row, operand=note_operand, table=table, context=context, minimum_row_jaccard=0.8)
    assert candidate is not None and all(checks.values())
    rejected, rejected_checks = _explicit_year_end_header_candidate(row=row, operand={**note_operand, "allowed_table_functions": []}, table=table, context=context, minimum_row_jaccard=0.8)
    assert rejected is None and rejected_checks["year_end_table_semantics_compatible"] is False


def test_role_header_requires_the_full_opening_or_closing_meaning() -> None:
    assert _header_role_matches(
        source_label="Số phải nộp đầu năm VND",
        requested_year=2021,
        period_role="opening",
        required_compact_fragments=("sophainop",),
    )
    assert _header_role_matches(
        source_label="31/12/2021 Số phải nộp VND",
        requested_year=2021,
        period_role="closing",
        required_compact_fragments=("sophainop",),
    )
    assert not _header_role_matches(
        source_label="31/12/2021 Số phải nộp VND",
        requested_year=2021,
        period_role="opening",
        required_compact_fragments=("sophainop",),
    )
    assert not _header_role_matches(
        source_label="Số phải thu cuối năm VND",
        requested_year=2021,
        period_role="closing",
        required_compact_fragments=("sophainop",),
    )
    assert not _header_role_matches(
        source_label="1/1/2021 Số phải nộp trong năm VND",
        requested_year=2021,
        period_role="opening",
        required_compact_fragments=("sophainop",),
    )


def test_role_header_keeps_the_header_and_metric_cells_distinct() -> None:
    source = {"source_path": "x.txt", "source_sha256": "source", "table_sha256": "table", "char_start": 0}
    table = {
        "internal_table_uid": "uid", "document_id": "PC1_2021", "local_ordinal": 1, "source_provenance": source,
        "rows": [["Nhãn", "Số phải nộp đầu năm VND"], ["Thuế thu nhập doanh nghiệp", "123"]],
        "cell_provenance": [[{}, {}], [{}, {}]],
    }
    context = {
        "internal_table_uid": "uid", "document_id": "PC1_2021", "source_provenance": source,
        "grid": {"rectangular": True, "provenance_complete": True}, "quality": {"status": "review_ready"},
        "table_function": {"kind": "financial_note"},
        "canonical_headers": {"columns": [{"column_index": 1, "source_label": "Số phải nộp đầu năm VND", "header_source_cells": [{"row_index": 0, "column_index": 1}]}]},
        "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
    }
    candidate, checks = _role_header_candidate(
        row={"question_id": 1, "row_index": 1, "row_token_jaccard": 1.0, "report_year": 2021, "ticker": "PC1", "observed_scope": "consolidated"},
        operand={"ticker": "PC1", "years": [2021], "allowed_table_functions": ["financial_note_detail"], "scope": None},
        table=table,
        context=context,
        period_role="opening",
        required_header_compact_fragments=("sophainop",),
        minimum_row_jaccard=0.8,
    )
    assert candidate is not None and all(checks.values())


def test_self_describing_role_cell_requires_role_and_number_in_the_same_source_cell() -> None:
    source = {"source_path": "x.txt", "source_sha256": "source", "table_sha256": "table", "char_start": 0}
    table = {
        "internal_table_uid": "uid", "document_id": "PC1_2025", "local_ordinal": 1, "source_provenance": source,
        "rows": [["Thuế giá trị gia tăng", "Số phải nộp cuối năm VND 123"]],
        "cell_provenance": [[{}, {}]],
    }
    context = {
        "internal_table_uid": "uid", "document_id": "PC1_2025", "source_provenance": source,
        "grid": {"rectangular": True, "provenance_complete": True}, "quality": {"status": "review_ready"},
        "table_function": {"kind": "financial_note"},
        "canonical_headers": {"columns": []},
        "row_profiles": [{"row_index": 0, "numeric_columns": [], "unreliable_numeric_columns": []}],
    }
    candidate, checks = _self_describing_role_cell_candidate(
        row={"question_id": 1, "row_index": 0, "row_token_jaccard": 1.0, "report_year": 2025, "ticker": "PC1", "observed_scope": "consolidated"},
        operand={"ticker": "PC1", "years": [2025], "allowed_table_functions": ["financial_note_detail"], "scope": None},
        table=table,
        context=context,
        period_role="closing",
        required_header_compact_fragments=("sophainop",),
        minimum_row_jaccard=0.8,
    )
    assert candidate is not None and all(checks.values())


def test_only_adjacent_identical_page_continuations_are_collapsed() -> None:
    rows = [
        {"internal_table_uid": "page_1", "observed_scope": "consolidated", "row_label": "Lợi nhuận sau thuế (chuyển sang trang sau)", "row_index": 0, "numeric_cell_indices": [1]},
        {"internal_table_uid": "page_2", "observed_scope": "consolidated", "row_label": "Lợi nhuận sau thuế (mang sang từ trang trước)", "row_index": 0, "numeric_cell_indices": [1]},
    ]
    tables = {
        "page_1": {"document_id": "AAA_2019", "local_ordinal": 5, "rows": [["Lợi nhuận sau thuế", "123"]]},
        "page_2": {"document_id": "AAA_2019", "local_ordinal": 6, "rows": [["Lợi nhuận sau thuế", "123"]]},
    }
    contexts = {key: {"table_function": {"kind": "income_statement"}} for key in tables}
    collapsed, count = _collapse_verified_page_continuations(source_rows=rows, tables=tables, contexts=contexts)
    assert count == 1
    assert [row["internal_table_uid"] for row in collapsed] == ["page_1"]


def test_combined_status_never_turns_missing_sources_into_an_authorized_answer() -> None:
    assert _combined_status(
        {"status": "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"},
        {"operand_status": "CURRENT_PERIOD_RECHECK_INCOMPLETE"},
    ) == "UNIQUE_SEMANTIC_NAVIGATION_CANDIDATE"
    assert _combined_status(
        {"status": "STRICT_SOURCE_GATES_INCOMPLETE"},
        {"operand_status": "CURRENT_PERIOD_RECHECK_INCOMPLETE"},
    ) == "SEMANTIC_SOURCE_GATES_INCOMPLETE"
