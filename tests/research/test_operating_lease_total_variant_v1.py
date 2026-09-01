from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_operating_lease_total_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("operating_lease_total_variant_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _item(question: str, ticker: str, year: int, *, scope: str | None = None) -> dict:
    plan = {
        "family": "direct_lookup",
        "tickers": [ticker],
        "years": [year],
        "operands": [{"metric": question}],
    }
    if scope is not None:
        plan["scope"] = scope
    return {
        "id": 37,
        "question": question,
        "effective_metric": question,
        "question_plan": plan,
    }


def _table(
    uid: str,
    document_id: str,
    rows: list[list[str]],
    *,
    kind: str = "financial_data_schedule",
    scope: str,
    source_title: str = "Cam kết cho thuê hoạt động; Tổng Công ty hiện đang cho thuê văn phòng theo hợp đồng thuê hoạt động.",
    headers: list[str] | None = None,
    unit_labels: list[str] | None = None,
) -> dict:
    headers = headers or [
        "Đến 1 năm · Trên 1 – 5 năm",
        "Số cuối năm · 63.632.125.064 · 83.600.068.822",
        "Đơn vị tính: VND · Số đầu năm · 72.529.223.139 · 71.636.840.670",
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
            "unit_labels": unit_labels if unit_labels is not None else ["VND"],
        },
    }


def _question() -> str:
    return "Tổng cam kết cho thuê hoạt động của công ty mẹ CTCP Tập đoàn GELEX (GEX) đến ngày 31/12/2018 là bao nhiêu tỷ đồng?"


def test_family_spec_accepts_total_lessor_question() -> None:
    spec = MODULE._family_spec(_item(_question(), "GEX", 2018, scope="separate"))
    assert spec is not None
    assert spec["output_divisor"] == Decimal("1000000000")


def test_payable_direction_is_not_taken_over() -> None:
    item = _item(
        "Tổng tiền thuê tối thiểu phải trả theo các hợp đồng thuê hoạt động của GEX cuối năm 2018 là bao nhiêu tỷ đồng?",
        "GEX",
        2018,
        scope="separate",
    )
    assert MODULE._family_spec(item) is None


def test_total_row_replays_current_period_with_explicit_vnd() -> None:
    item = _item(_question(), "GEX", 2018, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "gex-lessor",
        "GEX_financial_statements_2018_separate",
        [
            ["Đến 1 năm", "63.632.125.064", "72.529.223.139"],
            ["Trên 1 – 5 năm", "83.600.068.822", "71.636.840.670"],
            ["TỔNG CỘNG", "201.004.296.672", "244.103.155.846"],
        ],
        scope="separate",
    )
    result = MODULE._resolve_operating_lease_total(
        item,
        spec,
        tables_by_uid={"gex-lessor": table},
    )
    assert result is not None
    assert result[0] == Decimal("201.004296672")
    assert result[1][0]["row_index"] == 2
    assert result[1][0]["column_index"] == 1


def test_lessee_context_is_rejected_even_with_total_row() -> None:
    item = _item(_question(), "GEX", 2018, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "gex-lessee",
        "GEX_financial_statements_2018_separate",
        [["Đến 1 năm", "1.353.217.427", "1.353.217.427"], ["TỔNG CỘNG", "53.820.034.681", "55.173.252.108"]],
        scope="separate",
        source_title="Cam kết thuê hoạt động; Tổng Công ty hiện đang thuê đất theo hợp đồng thuê hoạt động.",
    )
    assert MODULE._resolve_operating_lease_total(item, spec, tables_by_uid={"x": table}) is None


def test_revenue_from_renting_context_is_not_lease_commitment_context() -> None:
    item = _item(_question(), "GEX", 2018, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "rent-revenue",
        "GEX_financial_statements_2018_separate",
        [["TỔNG CỘNG", "5.234.636.123", "3.371.224.080"]],
        scope="separate",
        source_title="20. Doanh thu chưa thực hiện; Doanh thu cho thuê văn phòng và khách sạn.",
    )
    assert MODULE._resolve_operating_lease_total(item, spec, tables_by_uid={"x": table}) is None


def test_agreeing_unscoped_lessor_duplicates_are_accepted() -> None:
    item = _item(
        "Tổng khoản tiền thuê tối thiểu theo hợp đồng thuê hoạt động của CTCP Tập đoàn GELEX (GEX) đến ngày 31/12/2017 là bao nhiêu trăm tỷ đồng?",
        "GEX",
        2017,
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table(
        "consolidated",
        "GEX_financial_statements_2017_consolidated",
        [["Đến 1 năm", "72.529.223.139", "60.652.301.927"], ["Trên 1 – 5 năm", "71.636.840.670", "60.076.301.927"], ["Trên 5 năm", "99.937.092.037", "80.312.420.916"], ["TỔNG CỘNG", "244.103.155.846", "168.208.958.450"]],
        scope="consolidated",
        source_title="Cam kết cho thuê hoạt động; Tập đoàn hiện đang cho thuê văn phòng và kho bãi theo hợp đồng thuê hoạt động.",
    )
    second = _table(
        "separate",
        "GEX_financial_statements_2017_separate",
        [["Đến 1 năm", "72.529.223.139", "60.076.301.927"], ["Trên 1 – 5 năm", "71.636.840.670", "60.076.301.927"], ["Trên 5 năm", "99.937.092.037", "80.312.420.916"], ["TỔNG CỘNG", "244.103.155.846", "159.471.703.905"]],
        scope="separate",
        source_title="Cam kết cho thuê hoạt động; Tổng Công ty hiện đang cho thuê văn phòng theo hợp đồng thuê hoạt động.",
    )
    result = MODULE._resolve_operating_lease_total(item, spec, tables_by_uid={"a": first, "b": second})
    assert result is not None
    assert result[0] == Decimal("2.44103155846")


def test_missing_source_unit_is_rejected() -> None:
    item = _item(
        "Tổng số tiền thuê tối thiểu trong tương lai thu được từ các hợp đồng thuê hoạt động của công ty mẹ IJC đến ngày 31/12/2015 là bao nhiêu tỷ đồng?",
        "IJC",
        2015,
        scope="separate",
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "ijc-no-unit",
        "IJC_financial_statements_2015_separate",
        [["Từ 01 năm trở xuống", "7.740.000.000", "7.740.000.000"], ["Trên 01 năm đến 05 năm", "19.610.000.000", "27.350.000.000"], ["Cộng", "27.350.000.000", "35.090.000.000"]],
        kind="financial_note",
        scope="separate",
        headers=["Từ 01 năm trở xuống · Trên 01 năm đến 05 năm", "Số cuối năm · 7.740.000.000 · 19.610.000.000", "Số đầu năm · 7.740.000.000 · 27.350.000.000"],
        unit_labels=[],
        source_title="Tài sản cho thuê hoạt động; các khoản thanh toán tiền thuê tối thiểu trong tương lai thu được từ các hợp đồng thuê hoạt động.",
    )
    assert MODULE._resolve_operating_lease_total(item, spec, tables_by_uid={"x": table}) is None


def test_conflicting_lessor_duplicates_are_rejected() -> None:
    item = _item(_question(), "GEX", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table("a", "GEX_financial_statements_2018_separate", [["TỔNG CỘNG", "201.004.296.672", "-"]], scope="separate")
    second = _table("b", "GEX_financial_statements_2018_consolidated", [["TỔNG CỘNG", "202.000.000.000", "-"]], scope="consolidated")
    assert MODULE._resolve_operating_lease_total(item, spec, tables_by_uid={"a": first, "b": second}) is None
