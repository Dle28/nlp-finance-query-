from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_loan_provision_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("loan_provision_variant_test", SCRIPT)
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
        "id": 33,
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
    kind: str = "financial_data_schedule",
    headers: list[str] | None = None,
    source_title: str = "Bảng cân đối kế toán; đơn vị tính: triệu đồng",
) -> dict:
    headers = headers or [
        "Nhãn dòng",
        "Thuyết minh",
        "31/12/2020 triệu đồng",
        "31/12/2019 triệu đồng",
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
            "unit_labels": ["triệu đồng"],
        },
    }


def _customer_question() -> str:
    return "Số dư dự phòng rủi ro cho vay khách hàng của MBB vào cuối năm 2020 là bao nhiêu triệu đồng?"


def test_family_spec_accepts_customer_loan_ending_balance() -> None:
    spec = MODULE._family_spec(_item(_customer_question(), "MBB", 2020))
    assert spec is not None
    assert spec["metric_kind"] == "customer_total"
    assert spec["output_divisor"] == Decimal("1000000")


def test_flow_provision_question_is_not_taken_over() -> None:
    item = _item(
        "Chi phí trích lập dự phòng rủi ro cho vay khách hàng của MBB trong năm 2020 là bao nhiêu triệu đồng?",
        "MBB",
        2020,
    )
    assert MODULE._family_spec(item) is None


def test_exact_balance_sheet_row_replays_signed_current_cell() -> None:
    item = _item(_customer_question(), "MBB", 2020, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "mbb-balance",
        "MBB_financial_statements_2020_separate",
        [
            ["", "Thuyết minh", "31/12/2020 triệu đồng", "31/12/2019 triệu đồng"],
            ["Dự phòng rủi ro cho vay khách hàng", "11", "(4.128.256)", "(3.003.627)"],
        ],
        scope="separate",
    )
    result = MODULE._resolve_loan_provision(
        item, spec, tables_by_uid={"mbb-balance": table}
    )
    assert result is not None
    assert result[0] == Decimal("-4128256")
    assert result[1][0]["row_index"] == 1
    assert result[1][0]["column_index"] == 2


def test_common_provision_uses_ending_balance_row() -> None:
    question = (
        "Số dư cuối kỳ dự phòng chung cho các khoản cho vay khách hàng của công ty mẹ VCB "
        "đến ngày 31/12/2015 là bao nhiêu triệu đồng?"
    )
    item = _item(question, "VCB", 2015, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    assert spec["metric_kind"] == "common"
    table = _table(
        "vcb-common",
        "VCB_financial_statements_2015_separate",
        [
            ["", "Năm kết thúc 31/12/2015 Triệu VND", "Năm kết thúc 31/12/2014 Triệu VND"],
            ["Số dư đầu kỳ", "2.245.624", "1.906.643"],
            ["Trích lập dự phòng", "437.663", "337.148"],
            ["Số dư cuối kỳ", "2.688.909", "2.245.624"],
        ],
        scope="separate",
        headers=[
            "Biến động dự phòng chung cho các khoản cho vay khách hàng",
            "Năm kết thúc 31/12/2015 Triệu VND",
            "Năm kết thúc 31/12/2014 Triệu VND",
        ],
        source_title="Biến động dự phòng chung cho các khoản cho vay khách hàng như sau:",
    )
    result = MODULE._resolve_loan_provision(
        item, spec, tables_by_uid={"vcb-common": table}
    )
    assert result is not None
    assert result[0] == Decimal("2688909")
    assert result[1][0]["row_index"] == 3
    assert result[1][0]["column_index"] == 1


def test_customer_movement_total_uses_total_column() -> None:
    question = (
        "Tổng số dư dự phòng rủi ro cho vay khách hàng cuối năm 2018 của VIB "
        "là bao nhiêu triệu đồng?"
    )
    item = _item(question, "VIB", 2018, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "vib-movement",
        "VIB_financial_statements_2018_separate",
        [
            ["", "Dự phòng chung triệu đồng", "Dự phòng cụ thể triệu đồng", "Tổng cộng triệu đồng"],
            ["Số dư đầu năm", "555.005", "389.855", "944.860"],
            ["Dự phòng rủi ro trích lập trong năm", "124.945", "448.569", "573.514"],
            ["Số dư cuối năm", "679.950", "197.815", "877.765"],
        ],
        scope="separate",
        headers=[
            "Số dư đầu năm · Dự phòng rủi ro trích lập trong năm · Số dư cuối năm",
            "Dự phòng chung triệu đồng",
            "Dự phòng cụ thể triệu đồng",
            "Tổng cộng triệu đồng",
        ],
        source_title="Thay đổi dự phòng rủi ro cho vay khách hàng trong năm như sau:",
    )
    result = MODULE._resolve_loan_provision(
        item, spec, tables_by_uid={"vib-movement": table}
    )
    assert result is not None
    assert result[0] == Decimal("877765")
    assert result[1][0]["column_index"] == 3


def test_short_loan_total_requires_and_passes_detail_checksum() -> None:
    question = (
        "Dự phòng các khoản cho vay ngắn hạn cuối năm 2024 của công ty mẹ HBC "
        "là bao nhiêu tỷ đồng?"
    )
    item = _item(question, "HBC", 2024, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    assert spec["metric_kind"] == "short_loan"
    table = _table(
        "hbc-short-loan",
        "HBC_financial_statements_2024_separate",
        [
            ["Chi tiết dự phòng các khoản cho vay ngắn hạn", "31/12/2024VND", "01/01/2024VND"],
            ["Công ty CP Nhà Hòa Bình", "75.075.867.681", "75.075.867.661"],
            ["Công ty CP Chứng khoán Sen Vàng", "1.429.181.347", "1.429.181.347"],
            ["Ông Lê Anh Dũng", "4.359.635.693", "4.359.635.693"],
            ["", "80.864.684.721", "80.864.684.701"],
        ],
        scope="separate",
        headers=[
            "Chi tiết dự phòng các khoản cho vay ngắn hạn",
            "31/12/2024VND",
            "01/01/2024VND",
        ],
        source_title="Chi tiết dự phòng các khoản cho vay ngắn hạn; đơn vị tính: VND",
    )
    result = MODULE._resolve_loan_provision(
        item, spec, tables_by_uid={"hbc-short-loan": table}
    )
    assert result is not None
    assert result[0] == Decimal("80.864684721")
    assert result[1][0]["row_index"] == 4
    assert result[1][0]["column_index"] == 1
    candidate = MODULE._short_loan_total_candidate(
        table, row_index=4, row=table["rows"][4], spec=spec
    )
    assert candidate is not None
    assert candidate["checksum_child_count"] == 3


def test_short_loan_total_rejects_checksum_mismatch() -> None:
    question = (
        "Dự phòng các khoản cho vay ngắn hạn cuối năm 2024 của công ty mẹ HBC "
        "là bao nhiêu tỷ đồng?"
    )
    item = _item(question, "HBC", 2024, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "hbc-bad-total",
        "HBC_financial_statements_2024_separate",
        [
            ["Chi tiết dự phòng các khoản cho vay ngắn hạn", "31/12/2024VND", "01/01/2024VND"],
            ["Công ty CP Nhà Hòa Bình", "75.075.867.681", "75.075.867.661"],
            ["Công ty CP Chứng khoán Sen Vàng", "1.429.181.347", "1.429.181.347"],
            ["Ông Lê Anh Dũng", "4.359.635.693", "4.359.635.693"],
            ["", "81.000.000.000", "80.864.684.701"],
        ],
        scope="separate",
        headers=[
            "Chi tiết dự phòng các khoản cho vay ngắn hạn",
            "31/12/2024VND",
            "01/01/2024VND",
        ],
        source_title="Chi tiết dự phòng các khoản cho vay ngắn hạn; đơn vị tính: VND",
    )
    assert MODULE._resolve_loan_provision(item, spec, tables_by_uid={"x": table}) is None


def test_conflicting_unscoped_balance_rows_are_rejected() -> None:
    item = _item(_customer_question(), "MBB", 2020)
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table(
        "separate",
        "MBB_financial_statements_2020_separate",
        [["Dự phòng rủi ro cho vay khách hàng", "11", "(4.128.256)", "(3.003.627)"]],
        scope="separate",
    )
    second = _table(
        "consolidated",
        "MBB_financial_statements_2020_consolidated",
        [["Dự phòng rủi ro cho vay khách hàng", "11", "(4.354.219)", "(3.200.913)"]],
        scope="consolidated",
    )
    assert MODULE._resolve_loan_provision(
        item, spec, tables_by_uid={"a": first, "b": second}
    ) is None


def test_missing_source_unit_is_rejected() -> None:
    item = _item(_customer_question(), "MBB", 2020, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "no-unit",
        "MBB_financial_statements_2020_separate",
        [["Dự phòng rủi ro cho vay khách hàng", "11", "(4.128.256)", "(3.003.627)"]],
        scope="separate",
        headers=["Nhãn dòng", "Thuyết minh", "31/12/2020", "31/12/2019"],
        source_title="Bảng cân đối kế toán",
    )
    table["context_trace"]["unit_labels"] = []
    assert MODULE._resolve_loan_provision(item, spec, tables_by_uid={"x": table}) is None
