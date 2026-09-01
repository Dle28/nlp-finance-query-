from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_interest_expense_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("interest_expense_variant_test", SCRIPT)
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
        "id": 9000,
        "question": question,
        "effective_metric": question,
        "question_plan": plan,
    }


def _table(
    uid: str,
    document_id: str,
    headers: list[str],
    rows: list[list[str]],
    *,
    scope: str,
) -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "scope": scope,
        "report_year": int(document_id.split("_")[3]),
        "headers": headers,
        "column_labels": headers,
        "rows": rows,
        "table_function": {"kind": "related_party_schedule"},
        "context_trace": {"source_title": "Bảng các bên liên quan"},
    }


def test_family_spec_separates_payable_and_named_counterparty() -> None:
    payable = _item(
        "Số dư lãi vay phải trả đến ngày 31/12/2022 của công ty mẹ VRE là bao nhiêu triệu đồng?",
        "VRE",
        2022,
        scope="separate",
    )
    payable_spec = MODULE._family_spec(payable)
    assert payable_spec is not None
    assert payable_spec["mode"] == "payable_balance"
    assert payable_spec["counterparty"] == ""

    counterparty = _item(
        "Chi phí lãi vay Ngân hàng TMCP Xăng dầu Petrolimex của PLX năm 2022 là bao nhiêu tỷ đồng?",
        "PLX",
        2022,
    )
    counterparty_spec = MODULE._family_spec(counterparty)
    assert counterparty_spec is not None
    assert counterparty_spec["mode"] == "counterparty_expense"
    assert counterparty_spec["counterparty"] == "ngan hang tmcp xang dau petrolimex"


def test_generic_unscoped_interest_expense_is_not_taken_over() -> None:
    item = _item(
        "Chi phí lãi vay của Công ty Cổ phần Thép Nam Kim trong năm 2021 là bao nhiêu tỷ đồng?",
        "NKG",
        2021,
    )
    assert MODULE._family_spec(item) is None


def test_payable_balance_replays_explicit_million_vnd_cell() -> None:
    item = _item(
        "Số dư lãi vay phải trả đến ngày 31/12/2022 của công ty mẹ VRE là bao nhiêu triệu đồng?",
        "VRE",
        2022,
        scope="separate",
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "vre-payable",
        "VRE_financial_statements_2022_separate",
        ["Nhãn dòng", "31/12/2022Triệu VND", "1/1/2022Triệu VND"],
        [["Lãi vay phải trả", "49.408", "51.182"]],
        scope="separate",
    )
    result = MODULE._resolve_interest_expense(
        item,
        spec,
        tables_by_uid={"vre-payable": table},
    )
    assert result is not None
    # ``49.408`` is a Vietnamese thousands-formatted source cell: 49,408
    # million VND, not a decimal fraction of one million VND.
    assert result[0] == Decimal("49408")
    assert result[1][0]["source_to_vnd_multiplier"] == "1000000"


def test_counterparty_expense_requires_same_section_header() -> None:
    item = _item(
        "Chi phí lãi vay Ngân hàng TMCP Xăng dầu Petrolimex của PLX năm 2022 là bao nhiêu tỷ đồng?",
        "PLX",
        2022,
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "plx-bank-interest",
        "PLX_financial_statements_2022_consolidated",
        ["Nhãn dòng", "Giá trị giao dịch · 2022VND", "2021VND"],
        [
            ["Công ty TNHH Castrol BP-PETCO Việt Nam", "", ""],
            ["Cổ tức được chia", "540.610.332.547", "368.824.834.994"],
            ["Ngân hàng TMCP Xăng dầu Petrolimex", "", ""],
            ["Nhận gốc vay", "3.087.241.972.579", "3.761.921.651.766"],
            ["Chi phí lãi vay", "54.200.198.358", "71.401.950.273"],
        ],
        scope="consolidated",
    )
    result = MODULE._resolve_interest_expense(
        item,
        spec,
        tables_by_uid={"plx-bank-interest": table},
    )
    assert result is not None
    assert result[0] == Decimal("54.200198358")
    assert result[1][0]["row_index"] == 4


def test_conflicting_related_party_values_are_rejected() -> None:
    item = _item(
        "Chi phí lãi vay Ngân hàng TMCP Xăng dầu Petrolimex của PLX năm 2022 là bao nhiêu tỷ đồng?",
        "PLX",
        2022,
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table(
        "plx-bank-interest-a",
        "PLX_financial_statements_2022_consolidated",
        ["Nhãn dòng", "2022VND"],
        [["Ngân hàng TMCP Xăng dầu Petrolimex", ""], ["Chi phí lãi vay", "54.200.198.358"]],
        scope="consolidated",
    )
    second = _table(
        "plx-bank-interest-b",
        "PLX_financial_statements_2022_separate",
        ["Nhãn dòng", "2022VND"],
        [["Ngân hàng TMCP Xăng dầu Petrolimex", ""], ["Chi phí lãi vay", "60.000.000.000"]],
        scope="separate",
    )
    assert (
        MODULE._resolve_interest_expense(
            item,
            spec,
            tables_by_uid={"a": first, "b": second},
        )
        is None
    )
