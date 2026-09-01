from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_tax_payable_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("tax_payable_variant_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _item(
    question: str,
    ticker: str,
    year: int,
    *,
    scope: str | None = None,
) -> dict:
    plan = {
        "family": "direct_lookup",
        "tickers": [ticker],
        "years": [year],
        "operands": [{"metric": question}],
    }
    if scope is not None:
        plan["scope"] = scope
    return {
        "id": 121,
        "question": question,
        "effective_metric": question,
        "question_plan": plan,
    }


def _table(
    uid: str,
    document_id: str,
    rows: list[list[str]],
    *,
    scope: str,
    kind: str = "financial_note",
    headers: list[str] | None = None,
    source_title: str = "Thuế và các khoản phải nộp Nhà nước; đơn vị tính: VND",
) -> dict:
    headers = headers or [
        "Nhãn dòng",
        "Số đầu năm · VND",
        "Số phải nộp trong năm · VND",
        "Số đã nộp trong năm · VND",
        "Số cuối năm · VND",
    ]
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "scope": scope,
        "report_year": int(document_id.split("_")[3]),
        "headers": headers,
        "column_labels": headers,
        "rows": rows,
        "table_function": {"kind": kind},
        "context_trace": {
            "source_title": source_title,
            "unit_labels": ["VND"],
        },
    }


def _beginning_question() -> str:
    return "Thuế thu nhập doanh nghiệp của công ty mẹ GEX đầu năm 2017 là bao nhiêu triệu đồng?"


def _ending_question() -> str:
    return "Đến ngày 31/12/2022, số tiền thuế thu nhập doanh nghiệp phải nộp của công ty mẹ PDR là bao nhiêu tỷ đồng?"


def test_family_spec_distinguishes_beginning_balance() -> None:
    spec = MODULE._family_spec(_item(_beginning_question(), "GEX", 2017, scope="separate"))
    assert spec is not None
    assert spec["intent"] == "beginning"
    assert spec["output_divisor"] == Decimal("1000000")


def test_expense_question_is_not_taken_over() -> None:
    item = _item(
        "Chi phí thuế thu nhập doanh nghiệp hiện hành của GEX năm 2017 là bao nhiêu triệu đồng?",
        "GEX",
        2017,
    )
    assert MODULE._family_spec(item) is None


def test_beginning_row_replays_source_vnd_to_million_vnd() -> None:
    item = _item(_beginning_question(), "GEX", 2017, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "gex-tax",
        "GEX_financial_statements_2017_separate",
        [
            ["", "Số đầu năm", "Số phải nộp trong năm", "Số đã nộp trong năm", "Số cuối năm"],
            ["Thuế thu nhập doanh nghiệp", "6.918.948.141", "-", "(6.918.948.141)", "-"],
        ],
        scope="separate",
    )
    result = MODULE._resolve_tax_payable(item, spec, tables_by_uid={"tax": table})
    assert result is not None
    assert result[0] == Decimal("6918.948141")
    assert result[1][0]["column_index"] == 1


def test_ending_payable_subsection_replays_current_balance() -> None:
    item = _item(_ending_question(), "PDR", 2022, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "pdr-tax",
        "PDR_financial_statements_2022_separate",
        [
            ["", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
            ["Phải nộp", "", "", "", ""],
            ["Thuế thu nhập doanh nghiệp (*)", "265.658.879.411", "306.563.962.999", "(267.942.964.910)", "304.279.877.500"],
            ["Phải thu", "", "", "", ""],
            ["Thuế thu nhập doanh nghiệp (*)", "11.932.171.942", "18.245.643.500", "(24.827.815.442)", "5.350.000.000"],
        ],
        scope="separate",
    )
    result = MODULE._resolve_tax_payable(item, spec, tables_by_uid={"tax": table})
    assert result is not None
    assert result[0] == Decimal("304.2798775")
    assert result[1][0]["row_index"] == 2
    assert result[1][0]["column_index"] == 4


def test_date_only_opening_and_closing_headers_are_a_balance_shape() -> None:
    item = _item(
        "Số dư thuế thu nhập doanh nghiệp phải trả của VIB cuối năm 2020 là bao nhiêu triệu đồng?",
        "VIB",
        2020,
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "date-only-tax",
        "VIB_financial_statements_2020_consolidated",
        [
            ["", "1/1/2020", "Số phát sinh trong năm", "Số đã nộp trong năm", "31/12/2020"],
            ["", "Triệu VND", "Triệu VND", "Triệu VND", "Triệu VND"],
            ["Phải trả Ngân sách Nhà nước", "", "", "", ""],
            ["Thuế thu nhập doanh nghiệp", "222.811", "1.160.511", "(1.041.884)", "341.438"],
        ],
        scope="consolidated",
        headers=[
            "Thuế giá trị gia tăng",
            "1/1/2020 · Triệu VND · 7.632",
            "Số phát sinh trong năm · Triệu VND · 146.570",
            "Số đã nộp trong năm · Triệu VND · (147.422)",
            "31/12/2020 · Triệu VND · 6.780",
        ],
    )
    result = MODULE._resolve_tax_payable(item, spec, tables_by_uid={"tax": table})
    assert result is not None
    assert result[0] == Decimal("341438")
    assert result[1][0]["column_index"] == 4


def test_explicit_ending_row_wins_over_explicit_beginning_row() -> None:
    item = _item(
        "Thuế TNDN phải nộp cuối năm 2024 của BAB là bao nhiêu triệu đồng?",
        "BAB",
        2024,
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "bab-tax",
        "BAB_financial_statements_2024_separate",
        [
            ["Nhãn dòng", "Năm 2024", "Năm 2023"],
            ["Triệu VND", "Triệu VND", "Triệu VND"],
            ["Tổng chi phí thuế TNDN hiện hành", "245.981", "203.631"],
            ["- Thuế TNDN phải nộp đầu năm", "97.836", "63.255"],
            ["- Thuế TNDN đã nộp trong năm", "(258.831)", "(169.050)"],
            ["Thuế TNDN còn phải nộp cuối năm", "84.986", "97.836"],
        ],
        scope="separate",
        headers=["Nhãn dòng", "Năm 2024 · Triệu VND", "Năm 2023 · Triệu VND"],
        source_title="32. Thuế thu nhập doanh nghiệp hiện hành; đơn vị tính: Triệu VND",
    )
    result = MODULE._resolve_tax_payable(item, spec, tables_by_uid={"tax": table})
    assert result is not None
    assert result[0] == Decimal("84986")
    assert result[1][0]["row_index"] == 5


def test_mixed_receivable_payable_table_binds_the_payable_column() -> None:
    item = _item(
        "Số thuế thu nhập doanh nghiệp phải nộp đầu năm 2021 của PC1 là bao nhiêu triệu đồng?",
        "PC1",
        2021,
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "pc1-mixed-tax",
        "PC1_financial_statements_2021_consolidated",
        [
            ["", "1/1/2021", "", "31/12/2021", ""],
            ["", "Số phải thu VND", "Số phải nộp VND", "Số phải thu VND", "Số phải nộp VND"],
            ["Thuế thu nhập doanh nghiệp", "122.678.842", "91.633.391.147", "1.043.518.596", "22.636.567.695"],
        ],
        scope="consolidated",
        headers=[
            "Nhãn dòng",
            "1/1/2021 · Số phải thu VND",
            "1/1/2021 · Số phải nộp VND",
            "31/12/2021 · Số phải thu VND",
            "31/12/2021 · Số phải nộp VND",
        ],
        source_title="23. Thuế và các khoản phải thu và phải nộp Nhà nước; đơn vị tính: VND",
    )
    result = MODULE._resolve_tax_payable(item, spec, tables_by_uid={"tax": table})
    assert result is not None
    assert result[0] == Decimal("91633.391147")
    assert result[1][0]["column_index"] == 2


def test_receivable_subsection_is_rejected() -> None:
    item = _item(_ending_question(), "PDR", 2022, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "wrong-subsection",
        "PDR_financial_statements_2022_separate",
        [
            ["", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
            ["Phải thu", "", "", "", ""],
            ["Thuế thu nhập doanh nghiệp (*)", "11.932.171.942", "18.245.643.500", "(24.827.815.442)", "5.350.000.000"],
        ],
        scope="separate",
    )
    assert MODULE._resolve_tax_payable(item, spec, tables_by_uid={"tax": table}) is None


def test_conflicting_unscoped_duplicates_are_rejected() -> None:
    item = _item(_ending_question(), "PDR", 2022)
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table(
        "separate",
        "PDR_financial_statements_2022_separate",
        [
            ["", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
            ["Phải nộp", "", "", "", ""],
            ["Thuế TNDN", "1", "2", "(1)", "5"],
        ],
        scope="separate",
    )
    second = _table(
        "consolidated",
        "PDR_financial_statements_2022_consolidated",
        [
            ["", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
            ["Phải nộp", "", "", "", ""],
            ["Thuế TNDN", "1", "2", "(1)", "6"],
        ],
        scope="consolidated",
    )
    assert MODULE._resolve_tax_payable(item, spec, tables_by_uid={"a": first, "b": second}) is None


def test_navigation_rank_can_break_conflict_without_authorizing_a_cell() -> None:
    item = _item(_ending_question(), "PDR", 2022)
    item["candidates"] = [
        {"rank": 1, "internal_table_uid": "preferred"},
        {"rank": 2, "internal_table_uid": "secondary"},
    ]
    spec = MODULE._family_spec(item)
    assert spec is not None
    preferred = _table(
        "preferred",
        "PDR_financial_statements_2022_separate",
        [
            ["", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
            ["Phải nộp", "", "", "", ""],
            ["Thuế thu nhập doanh nghiệp", "1", "2", "(1)", "5.000.000.000"],
        ],
        scope="separate",
    )
    secondary = _table(
        "secondary",
        "PDR_financial_statements_2022_consolidated",
        [
            ["", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
            ["Phải nộp", "", "", "", ""],
            ["Thuế thu nhập doanh nghiệp", "1", "2", "(1)", "6.000.000.000"],
        ],
        scope="consolidated",
    )
    result = MODULE._resolve_tax_payable(
        item,
        spec,
        tables_by_uid={"preferred": preferred, "secondary": secondary},
    )
    assert result is not None
    assert result[0] == Decimal("5")
    assert result[1][0]["candidate_navigation_rank"] == 1
    assert result[1][0]["candidate_navigation_tiebreak"] is True


def test_missing_source_unit_is_rejected() -> None:
    item = _item(_ending_question(), "PDR", 2022, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "no-unit",
        "PDR_financial_statements_2022_separate",
        [
            ["", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
            ["Phải nộp", "", "", "", ""],
            ["Thuế TNDN", "1", "2", "(1)", "5"],
        ],
        scope="separate",
        headers=["Nhãn dòng", "Số đầu năm", "Tăng", "Giảm", "Số cuối năm"],
        source_title="Thuế và các khoản phải nộp Nhà nước",
    )
    table["context_trace"]["unit_labels"] = []
    assert MODULE._resolve_tax_payable(item, spec, tables_by_uid={"tax": table}) is None
