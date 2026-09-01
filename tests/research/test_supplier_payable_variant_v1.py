from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_supplier_payable_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("supplier_payable_variant_test", SCRIPT)
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
        "id": 338,
        "question": question,
        "effective_metric": question,
        "question_plan": plan,
    }


def _table(
    uid: str,
    document_id: str,
    rows: list[list[str]],
    *,
    kind: str = "related_party_schedule",
    scope: str,
) -> dict:
    headers = ["Nhãn dòng", "31/12/2018 · VND", "01/7/2018 · VND"]
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "scope": scope,
        "report_year": int(document_id.split("_")[3]),
        "headers": headers,
        "column_labels": headers,
        "rows": rows,
        "table_function": {"kind": kind},
        "context_trace": {"source_title": "Bảng phải trả nhà cung cấp"},
    }


def _question() -> str:
    return (
        "Số dư phải trả cho nhà cung cấp Tổng Công ty Dầu Việt Nam - CTCP "
        "của BSR vào cuối năm 2018 là bao nhiêu nghìn tỷ đồng?"
    )


def test_family_spec_extracts_counterparty_and_thousand_billion_unit() -> None:
    spec = MODULE._family_spec(_item(_question(), "BSR", 2018))
    assert spec is not None
    assert spec["counterparty"] == "tong cong ty dau viet nam cong ty co phan"
    assert spec["output_divisor"] == Decimal("1000000000000")


def test_generic_supplier_question_without_named_owner_is_not_taken_over() -> None:
    item = _item(
        "Số dư phải trả cho nhà cung cấp trong năm 2018 của BSR là bao nhiêu tỷ đồng?",
        "BSR",
        2018,
    )
    assert MODULE._family_spec(item) is None


def test_agreeing_separate_and_consolidated_source_rows_replay() -> None:
    item = _item(_question(), "BSR", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    separate = _table(
        "separate",
        "BSR_financial_statements_2018_separate",
        [
            ["Phải trả nhà cung cấp", "", ""],
            ["Tổng Công ty Dầu Việt Nam - Công ty Cổ phần", "2.499.485.052.166", "3.986.408.656.102"],
        ],
        scope="separate",
    )
    consolidated = _table(
        "consolidated",
        "BSR_financial_statements_2018_consolidated",
        [
            ["Phải trả nhà cung cấp", "", ""],
            ["Tổng Công ty Dầu Việt Nam-Công ty Cổ phần", "2.499.485.052.166", "3.986.408.656.102"],
        ],
        kind="financial_note",
        scope="consolidated",
    )
    result = MODULE._resolve_supplier_payable(
        item,
        spec,
        tables_by_uid={"separate": separate, "consolidated": consolidated},
    )
    assert result is not None
    assert result[0] == Decimal("2.499485052166")
    assert result[1][0]["row_index"] == 1
    assert result[1][0]["column_index"] == 1


def test_conflicting_scope_values_are_rejected() -> None:
    item = _item(_question(), "BSR", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table(
        "a",
        "BSR_financial_statements_2018_separate",
        [["Phải trả nhà cung cấp", "", ""], ["Tổng Công ty Dầu Việt Nam - CTCP", "2.499.485.052.166", "-"]],
        scope="separate",
    )
    second = _table(
        "b",
        "BSR_financial_statements_2018_consolidated",
        [["Phải trả nhà cung cấp", "", ""], ["Tổng Công ty Dầu Việt Nam - Công ty Cổ phần", "2.600.000.000.000", "-"]],
        kind="financial_note",
        scope="consolidated",
    )
    assert (
        MODULE._resolve_supplier_payable(
            item,
            spec,
            tables_by_uid={"a": first, "b": second},
        )
        is None
    )


def test_same_counterparty_in_wrong_section_is_rejected() -> None:
    item = _item(_question(), "BSR", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "wrong-section",
        "BSR_financial_statements_2018_consolidated",
        [
            ["Phải trả khác", "", ""],
            ["Tổng Công ty Dầu Việt Nam - Công ty Cổ phần", "2.499.485.052.166", "-"],
        ],
        kind="financial_note",
        scope="consolidated",
    )
    assert MODULE._resolve_supplier_payable(item, spec, tables_by_uid={"x": table}) is None


def test_unsafe_table_kind_is_rejected_even_with_matching_text() -> None:
    item = _item(_question(), "BSR", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "unsafe",
        "BSR_financial_statements_2018_consolidated",
        [["Phải trả nhà cung cấp", "", ""], ["Tổng Công ty Dầu Việt Nam - Công ty Cổ phần", "2.499.485.052.166", "-"]],
        kind="balance_sheet",
        scope="consolidated",
    )
    assert MODULE._resolve_supplier_payable(item, spec, tables_by_uid={"x": table}) is None
