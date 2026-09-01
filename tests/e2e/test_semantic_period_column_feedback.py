from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path


def _builder_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/e2e/build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location("competition_submission_builder_period_feedback", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table(*, uid: str, ticker: str, year: int, headers, rows, scope: str = "separate"):
    return {
        "internal_table_uid": uid,
        "ticker": ticker,
        "report_year": year,
        "scope": scope,
        "headers": deepcopy(headers),
        "rows": deepcopy(rows),
        "row_paths": [""] * len(rows),
        "header_row_indices": [0],
        "table_function": {"kind": "financial_note", "label": "financial note"},
        "context_trace": {"source_title": "financial note"},
        "unit_hint": None,
        "document_id": f"{ticker}_financial_statements_{year}_{scope}",
    }


def _item(question: str, *, ticker: str = "AAA", year: int = 2021, metric: str = "giá trị"):
    return {
        "question": question,
        "question_plan": {
            "family": "direct_lookup",
            "tickers": [ticker],
            "years": [year],
            "scope": "separate",
            "operands": [{"metric": metric}],
        },
    }


def _candidate(uid: str, document_id: str, row_index: int = 1):
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "rank": 1,
        "review_score": 1.0,
        "evidence_window": [{"index": row_index, "row": None}],
    }


def _bind_candidate(item, table, *, row_index: int = 1):
    candidate = _candidate(table["internal_table_uid"], table["document_id"], row_index)
    candidate["evidence_window"][0]["row"] = table["rows"][row_index]
    item["candidates"] = [candidate]
    return item


def _assess(builder, item, table, *, row_index: int, column_index: int, column_context: str):
    row = table["rows"][row_index]
    evidence = [{"index": index, "row": value} for index, value in enumerate(table["rows"])]
    return builder.assess_semantic_cell(
        item,
        table,
        row_label=builder._semantic_row_context(
            table,
            row_index=row_index,
            row=row,
            parser=builder.parse_decimal,
        ),
        row_index=row_index,
        column_index=column_index,
        raw_cell=row[column_index],
        column_context=column_context,
        evidence=evidence,
        source_multiplier=builder.Decimal(1),
        parser=builder.parse_decimal,
        metric=item["question_plan"]["operands"][0]["metric"],
    )


def test_exact_year_end_amount_column_wins_over_prior_year_and_percentage():
    builder = _builder_module()
    table = _table(
        uid="aaa-balance",
        ticker="AAA",
        year=2021,
        headers=[
            "Nhãn dòng",
            "Số cuối năm 2021 · VND",
            "Tỷ lệ % 2021",
            "Số cuối năm 2020 · VND",
            "Tỷ lệ % 2020",
        ],
        rows=[
            ["Nhãn dòng", "Số cuối năm 2021", "Tỷ lệ % 2021", "Số cuối năm 2020", "Tỷ lệ % 2020"],
            ["Tài sản", "1000", "2,5", "900", "2,4"],
        ],
    )
    item = _bind_candidate(
        _item(
            "Giá trị tài sản của AAA đến ngày 31/12/2021 là bao nhiêu triệu đồng?",
            metric="Giá trị tài sản AAA",
        ),
        table,
    )

    ranked = builder.rank_semantic_cells(item, tables_by_uid={table["internal_table_uid"]: table}, allow_uncertain=True)

    assert ranked
    assert ranked[0]["column_index"] == 1
    assert ranked[0]["raw_value"] == builder.Decimal("1000")
    assert not ranked[0]["semantic_guard_reason_codes"]


def test_unlabelled_amount_is_not_evicted_by_an_exact_year_percentage_column():
    builder = _builder_module()
    table = _table(
        uid="aaa-unlabelled-amount",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "Giá trị VND", "Tỷ lệ % 2021"],
        rows=[
            ["Nhãn dòng", "Giá trị VND", "Tỷ lệ % 2021"],
            ["Tài sản", "1000", "2,5"],
        ],
    )
    item = _bind_candidate(
        _item(
            "Giá trị tài sản của AAA trong năm 2021 là bao nhiêu triệu đồng?",
            metric="Giá trị tài sản AAA",
        ),
        table,
    )

    ranked = builder.rank_semantic_cells(item, tables_by_uid={table["internal_table_uid"]: table}, allow_uncertain=True)

    assert ranked
    assert ranked[0]["column_index"] == 1
    assert ranked[0]["raw_value"] == builder.Decimal("1000")
    assert "PERCENTAGE_CELL_FOR_AMOUNT_QUERY" not in ranked[0]["semantic_guard_reason_codes"]


def test_exact_year_mismatch_is_reported_for_both_row_and_column_context():
    builder = _builder_module()
    table = _table(
        uid="aaa-wrong-year",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "Giá trị 2021 · VND", "Giá trị 2020 · VND"],
        rows=[["Tài sản năm 2020", "1000", "900"]],
    )
    item = _item(
        "Giá trị tài sản của AAA trong năm 2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    guard = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=2,
        column_context="Giá trị 2020 · VND",
    )

    assert guard["accepted"] is False
    assert "ROW_PERIOD_YEAR_MISMATCH" in guard["reason_codes"]
    assert "COLUMN_PERIOD_YEAR_MISMATCH" in guard["reason_codes"]


def test_start_end_mismatch_is_a_real_guard_not_a_recall_fallback():
    builder = _builder_module()
    table = _table(
        uid="aaa-opening-closing",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "Số đầu năm 2021 · VND", "Số cuối năm 2021 · VND"],
        rows=[["Số dư", "100", "140"]],
    )
    item = _item(
        "Giá trị tài sản của AAA đến ngày 31/12/2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    guard = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=1,
        column_context="Số đầu năm 2021 · VND",
    )

    assert guard["accepted"] is False
    assert "COLUMN_PERIOD_START_SELECTED_FOR_END_QUERY" in guard["reason_codes"]


def test_row_period_start_end_mismatch_is_not_hidden_by_a_plain_vnd_column():
    builder = _builder_module()
    table = _table(
        uid="aaa-opening-row",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "VND"],
        rows=[["Số đầu năm", "100"]],
    )
    end_item = _item(
        "Giá trị tài sản của AAA đến ngày 31/12/2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )
    start_table = _table(
        uid="aaa-closing-row",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "VND"],
        rows=[["Số cuối năm", "140"]],
    )
    start_item = _item(
        "Giá trị tài sản của AAA tại ngày 01/01/2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    end_guard = _assess(
        builder,
        end_item,
        table,
        row_index=0,
        column_index=1,
        column_context="VND",
    )
    start_guard = _assess(
        builder,
        start_item,
        start_table,
        row_index=0,
        column_index=1,
        column_context="VND",
    )

    assert end_guard["accepted"] is False
    assert "ROW_PERIOD_START_SELECTED_FOR_END_QUERY" in end_guard["reason_codes"]
    assert start_guard["accepted"] is False
    assert "ROW_PERIOD_END_SELECTED_FOR_START_QUERY" in start_guard["reason_codes"]


def test_start_query_accepts_start_column_and_rejects_closing_column():
    builder = _builder_module()
    table = _table(
        uid="aaa-opening-closing-start",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "Số đầu năm 2021 · VND", "Số cuối năm 2021 · VND"],
        rows=[["Số dư", "100", "140"]],
    )
    item = _item(
        "Giá trị tài sản của AAA tại ngày 01/01/2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    accepted = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=1,
        column_context="Số đầu năm 2021 · VND",
    )
    rejected = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=2,
        column_context="Số cuối năm 2021 · VND",
    )

    assert accepted["accepted"] is True
    assert rejected["accepted"] is False
    assert "COLUMN_PERIOD_END_SELECTED_FOR_START_QUERY" in rejected["reason_codes"]


def test_leading_zero_start_date_selects_the_opening_column_when_available():
    builder = _builder_module()
    table = _table(
        uid="aaa-opening-selection",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "Số đầu năm 2021 · VND", "Số cuối năm 2021 · VND"],
        rows=[["Số dư", "100", "140"]],
    )
    item = _bind_candidate(
        _item(
            "Giá trị tài sản của AAA tại ngày 01/01/2021 là bao nhiêu triệu đồng?",
            metric="Giá trị tài sản AAA",
        ),
        table,
        row_index=0,
    )

    ranked = builder.rank_semantic_cells(item, tables_by_uid={table["internal_table_uid"]: table}, allow_uncertain=True)

    assert ranked
    assert ranked[0]["column_index"] == 1
    assert not ranked[0]["semantic_guard_reason_codes"]


def test_arbitrary_point_date_does_not_claim_a_period_end_intent():
    builder = _builder_module()

    assert builder._semantic_period_intent(
        "Giá trị tài sản của AAA tại ngày 15/05/2021 là bao nhiêu?"
    ) is None


def test_textual_start_and_end_dates_keep_their_period_intent():
    builder = _builder_module()

    assert builder._semantic_period_intent(
        "Giá trị tài sản của AAA tại ngày 01 tháng 01 năm 2021 là bao nhiêu?"
    ) == "start"
    assert builder._semantic_period_intent(
        "Giá trị tài sản của AAA đến ngày 31 tháng 12 năm 2021 là bao nhiêu?"
    ) == "end"


def test_prior_period_row_is_rejected_when_requested_year_is_current():
    builder = _builder_module()
    table = _table(
        uid="aaa-prior-row",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "VND"],
        rows=[["Số dư năm trước", "900"]],
    )
    item = _item(
        "Giá trị tài sản của AAA trong năm 2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    guard = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=1,
        column_context="VND",
    )

    assert guard["accepted"] is False
    assert "ROW_PRIOR_PERIOD_SELECTED_FOR_REQUESTED_YEAR" in guard["reason_codes"]


def test_percentage_cell_is_rejected_for_amount_query_but_amount_column_survives():
    builder = _builder_module()
    table = _table(
        uid="aaa-percent",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "Giá trị VND 2021", "Tỷ lệ % 2021"],
        rows=[["Tài sản", "1000", "2,5"]],
    )
    item = _item(
        "Giá trị tài sản của AAA trong năm 2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    amount_guard = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=1,
        column_context="Giá trị VND 2021",
    )
    percentage_guard = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=2,
        column_context="Tỷ lệ % 2021",
    )

    assert amount_guard["accepted"] is True
    assert percentage_guard["accepted"] is False
    assert "PERCENTAGE_CELL_FOR_AMOUNT_QUERY" in percentage_guard["reason_codes"]


def test_literal_percent_header_is_preserved_as_a_percentage_rejection_signal():
    builder = _builder_module()
    table = _table(
        uid="aaa-percent-literal",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "2021 · VND", "2021 · %"],
        rows=[["Tài sản", "1000", "2,5"]],
    )
    item = _item(
        "Giá trị tài sản của AAA trong năm 2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    guard = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=2,
        # Simulate a caller that passed only normalized context.  The raw
        # header is still available to the guard and must retain ``%``.
        column_context="2021",
    )

    assert guard["accepted"] is False
    assert "PERCENTAGE_CELL_FOR_AMOUNT_QUERY" in guard["reason_codes"]


def test_percentage_semantic_marker_selects_percentage_column_without_percent_word():
    builder = _builder_module()
    table = _table(
        uid="aaa-voting-rights",
        ticker="AAA",
        year=2021,
        headers=["Nhãn dòng", "Giá trị VND 2021", "Quyền biểu quyết % 2021"],
        rows=[
            ["Nhãn dòng", "Giá trị VND 2021", "Quyền biểu quyết % 2021"],
            ["Cổ đông", "1000", "51,2"],
        ],
    )
    item = _bind_candidate(
        _item(
            "Quyền biểu quyết của AAA năm 2021 là bao nhiêu?",
            metric="Quyền biểu quyết AAA",
        ),
        table,
    )

    ranked = builder.rank_semantic_cells(item, tables_by_uid={table["internal_table_uid"]: table}, allow_uncertain=True)

    assert ranked
    assert ranked[0]["column_index"] == 2
    assert ranked[0]["raw_value"] == builder.Decimal("51.2")
    assert not ranked[0]["semantic_guard_reason_codes"]


def test_report_navigation_row_is_rejected_even_when_it_has_a_page_number():
    builder = _builder_module()
    table = _table(
        uid="aaa-navigation",
        ticker="AAA",
        year=2021,
        headers=["Mục lục", "Trang"],
        rows=[["Báo cáo kết quả hoạt động kinh doanh riêng", "7"]],
    )
    item = _item(
        "Giá trị tài sản của AAA trong năm 2021 là bao nhiêu triệu đồng?",
        metric="Giá trị tài sản AAA",
    )

    guard = _assess(
        builder,
        item,
        table,
        row_index=0,
        column_index=1,
        column_context="Trang",
    )

    assert guard["accepted"] is False
    assert "REPORT_NAVIGATION_ROW" in guard["reason_codes"]
