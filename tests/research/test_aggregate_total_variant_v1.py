from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_aggregate_total_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("aggregate_total_variant_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _item(
    question: str,
    ticker: str,
    year: int,
    *,
    scope: str | None = None,
    candidate_uids: list[str] | None = None,
) -> dict:
    plan = {
        "family": "direct_lookup",
        "tickers": [ticker],
        "years": [year],
        "operands": [{"metric": question}],
    }
    if scope is not None:
        plan["scope"] = scope
    candidates = [
        {
            "rank": index,
            "internal_table_uid": uid,
            "document_id": f"{ticker}_financial_statements_{year}_separate",
            "ticker": ticker,
            "report_year": year,
            "scope": scope or "separate",
        }
        for index, uid in enumerate(candidate_uids or [], start=1)
    ]
    return {
        "id": 87,
        "question": question,
        "effective_metric": question,
        "question_plan": plan,
        "candidates": candidates,
    }


def _table(
    uid: str,
    document_id: str,
    rows: list[list[str]],
    *,
    scope: str,
    kind: str = "financial_note",
    headers: list[str],
    source_title: str,
    topic_label: str = "",
    period_labels: list[str] | None = None,
    unit_labels: list[str] | None = None,
) -> dict:
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
            "summary": "Bảng chi tiết các khoản mục định lượng.",
            "topic": {"label": topic_label, "source": "numbered_source_heading"},
            "period_labels": period_labels or [],
            "unit_labels": unit_labels if unit_labels is not None else ["VND"],
        },
    }


def _hpx_question() -> str:
    return "Tổng giá vốn hàng bán của HPX trong năm 2024 là bao nhiêu tỷ đồng?"


def _dbc_question() -> str:
    return "Tổng doanh thu thuần năm 2024 của CTCP Tập đoàn Dabaco Việt Nam là bao nhiêu nghìn tỷ đồng?"


def test_segment_total_uses_aggregate_column() -> None:
    item = _item(_hpx_question(), "HPX", 2024, candidate_uids=["hpx-segment"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "hpx-segment",
        "HPX_financial_statements_2024_consolidated",
        [
            ["", "Kinh doanh bất động sản", "Kinh doanh cho thuê", "Loại trừ", "Tổng"],
            ["DOANH THU, GIÁ VỐN", "", "", "", ""],
            ["Tổng giá vốn", "958.597.395.523", "138.521.752.915", "-", "1.097.119.148.438"],
        ],
        scope="consolidated",
        kind="segment_reporting",
        headers=[
            "Nhãn dòng",
            "Kinh doanh bất động sản",
            "Kinh doanh cho thuê",
            "Loại trừ",
            "Tổng",
        ],
        source_title=(
            "BẢN THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT "
            "cho năm tài chính kết thúc ngày 31/12/2024 7.4. Báo cáo bộ phận"
        ),
        topic_label="7.4. Báo cáo bộ phận cho năm tài chính kết thúc ngày 31/12/2024",
        unit_labels=["VND"],
    )
    result = MODULE._resolve_aggregate_total(
        item, spec, tables_by_uid={"hpx-segment": table}
    )
    assert result is not None
    assert result[0] == Decimal("1097.119148438")
    assert result[1][0]["row_index"] == 2
    assert result[1][0]["column_index"] == 4


def test_net_revenue_requires_the_net_revenue_qualifier() -> None:
    item = _item(_dbc_question(), "DBC", 2024, scope="consolidated")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "dbc-revenue",
        "DBC_financial_statements_2024_consolidated",
        [
            ["Nhãn dòng", "Năm nay VND", "Năm trước VND"],
            ["Tổng doanh thu", "13.739.362.734.289", "11.241.164.150.311"],
            ["Doanh thu thuần", "13.573.523.231.898", "11.110.000.756.812"],
        ],
        scope="consolidated",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Năm nay VND", "Năm trước VND"],
        source_title=(
            "Công ty Cổ phần Tập đoàn Dabaco Việt Nam; "
            "24.1 Doanh thu bán hàng và cung cấp dịch vụ năm 2024"
        ),
        topic_label="24.1 Doanh thu bán hàng và cung cấp dịch vụ năm 2024",
    )
    result = MODULE._resolve_aggregate_total(
        item, spec, tables_by_uid={"dbc-revenue": table}
    )
    assert result is not None
    assert result[0] == Decimal("13.573523231898")
    assert result[1][0]["row_index"] == 2


def test_dabaco_segment_total_uses_the_last_total_column() -> None:
    item = _item(_dbc_question(), "DBC", 2024, candidate_uids=["dbc-segment"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "dbc-segment",
        "DBC_financial_statements_2024_consolidated",
        [
            [
                "Nhãn dòng",
                "Thức ăn chăn nuôi",
                "Bất động sản",
                "Con giống",
                "Điều chỉnh và loại trừ",
                "Đơn vị tính: VND",
            ],
            [
                "Tổng doanh thu thuần",
                "14.141.881.998.302",
                "332.465.508.358",
                "9.174.574.158.792",
                "(10.075.398.433.554)",
                "13.573.523.231.898",
            ],
        ],
        scope="consolidated",
        kind="financial_note",
        headers=[
            "Nhãn dòng",
            "Thức ăn chăn nuôi",
            "Bất động sản",
            "Con giống",
            "Điều chỉnh và loại trừ",
            "Đơn vị tính: VND",
        ],
        source_title=(
            "Công ty Cổ phần Tập đoàn Dabaco Việt Nam THUYẾT MINH "
            "BÁO CÁO TÀI CHÍNH HỢP NHẤT ngày 31 tháng 12 năm 2024 "
            "33. THÔNG TIN THEO BỘ PHẬN"
        ),
        topic_label="33. THÔNG TIN THEO BỘ PHẬN tại ngày 31 tháng 12 năm 2024",
    )
    result = MODULE._resolve_aggregate_total(
        item, spec, tables_by_uid={"dbc-segment": table}
    )
    assert result is not None
    assert result[0] == Decimal("13.573523231898")
    assert result[1][0]["column_index"] == 5


def test_bond_total_rejects_payable_expense_table_and_keeps_total_row() -> None:
    question = "Tổng giá trị trái phiếu của HAG cuối năm 2023 là bao nhiêu nghìn đồng?"
    item = _item(question, "HAG", 2023, candidate_uids=["hag-bond"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    good = _table(
        "hag-bond",
        "HAG_financial_statements_2023_separate",
        [
            ["", "Số cuối năm", "Ngàn VND Số đầu năm"],
            ["Trái phiếu thường dài hạn đến hạn trả trong vòng 1 năm", "1.748.934.977", "1.958.725.949"],
            ["Trái phiếu thường dài hạn", "3.199.130.581", "3.581.600.405"],
            ["TỔNG CỘNG", "4.948.065.558", "5.540.326.354"],
        ],
        scope="separate",
        kind="debt_schedule",
        headers=["", "Số cuối năm", "Ngàn VND Số đầu năm"],
        source_title="20. VAY; 20.1 Trái phiếu thường dài hạn của HAG năm 2023",
        topic_label="20. VAY; Trái phiếu thường dài hạn",
        period_labels=["Số cuối năm", "Số đầu năm"],
        unit_labels=["Ngàn VND"],
    )
    bad = _table(
        "hag-expense",
        "HAG_financial_statements_2023_separate",
        [
            ["", "Số cuối năm", "Ngàn VND Số đầu năm"],
            ["Chi phí lãi vay", "3.227.040.751", "2.552.949.571"],
            ["TỔNG CỘNG", "3.588.057.550", "3.082.296.111"],
        ],
        scope="separate",
        kind="debt_schedule",
        headers=["", "Số cuối năm", "Ngàn VND Số đầu năm"],
        source_title="18. CHI PHÍ PHẢI TRẢ của HAG năm 2023",
        topic_label="18. CHI PHÍ PHẢI TRẢ",
        period_labels=["Số cuối năm", "Số đầu năm"],
        unit_labels=["Ngàn VND"],
    )
    result = MODULE._resolve_aggregate_total(
        item, spec, tables_by_uid={"hag-bond": good, "hag-expense": bad}
    )
    assert result is not None
    assert result[0] == Decimal("4948065558")
    assert result[1][0]["internal_table_uid"] == "hag-bond"


def test_source_topic_year_rejects_comparative_table_with_stale_document_year() -> None:
    question = "Tổng doanh thu thuần của GAS năm 2016 là bao nhiêu trăm tỷ đồng?"
    item = _item(question, "GAS", 2016)
    spec = MODULE._family_spec(item)
    assert spec is not None
    current = _table(
        "gas-current",
        "GAS_financial_statements_2016_consolidated",
        [["", "VND", "VND", "VND", "VND"], ["Tổng doanh thu thuần", "65", "-", "-", "59.076.193.175.661"]],
        scope="consolidated",
        kind="income_statement",
        headers=["", "VND", "VND", "VND", "VND"],
        source_title="Kết quả hoạt động kinh doanh hợp nhất cho năm tài chính kết thúc ngày 31 tháng 12 năm 2016",
        topic_label="",
        unit_labels=["VND"],
    )
    prior = _table(
        "gas-prior",
        "GAS_financial_statements_2016_consolidated",
        [["", "VND", "VND", "VND", "VND"], ["Tổng doanh thu thuần", "71", "-", "-", "64.300.204.038.285"]],
        scope="consolidated",
        kind="income_statement",
        headers=["", "VND", "VND", "VND", "VND"],
        source_title="Báo cáo theo bộ phận cho năm tài chính kết thúc ngày 31 tháng 12 năm 2015",
        topic_label="Báo cáo theo bộ phận năm 2015",
        unit_labels=["VND"],
    )
    result = MODULE._resolve_aggregate_total(
        item, spec, tables_by_uid={"gas-current": current, "gas-prior": prior}
    )
    assert result is not None
    assert result[0] == Decimal("590.76193175661")
    assert result[1][0]["internal_table_uid"] == "gas-current"


def test_industry_total_replays_blank_terminal_row_with_checksum() -> None:
    question = "Vào cuối năm 2020, tổng dư nợ cho vay theo ngành nghề kinh doanh của VPB là bao nhiêu triệu đồng?"
    item = _item(question, "VPB", 2020, scope="separate")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "vpb-industry",
        "VPB_financial_statements_2020_separate",
        [
            ["", "31/12/2020 Triệu VND", "%", "31/12/2019 Triệu VND", "%"],
            ["Nông nghiệp", "100.000.000", "45,26", "90.000.000", "46,74"],
            ["Xây dựng", "120.944.599", "54,74", "102.632.283", "53,26"],
            ["", "220.944.599", "100", "192.632.283", "100"],
        ],
        scope="separate",
        kind="financial_note",
        headers=["", "31/12/2020 Triệu VND", "%", "31/12/2019 Triệu VND", "%"],
        source_title="Phân tích dư nợ cho vay theo ngành nghề kinh doanh của VPB năm 2020",
        period_labels=["31/12/2020", "31/12/2019"],
        unit_labels=["Triệu VND", "%"],
    )
    result = MODULE._resolve_aggregate_total(
        item, spec, tables_by_uid={"vpb-industry": table}
    )
    assert result is not None
    assert result[0] == Decimal("220944599")
    assert result[1][0]["row_index"] == 3
    assert result[1][0]["column_index"] == 1


def test_candidate_navigation_rank_can_disambiguate_duplicate_topic_tables() -> None:
    question = "Tổng giá trị trái phiếu của HAG cuối năm 2023 là bao nhiêu nghìn đồng?"
    item = _item(
        question,
        "HAG",
        2023,
        candidate_uids=["hag-primary", "hag-duplicate"],
    )
    spec = MODULE._family_spec(item)
    assert spec is not None
    primary = _table(
        "hag-primary",
        "HAG_financial_statements_2023_separate",
        [
            ["", "Số cuối năm", "Số đầu năm"],
            ["Trái phiếu thường dài hạn", "3.199.130.581", "3.581.600.405"],
            ["TỔNG CỘNG", "4.948.065.558", "5.540.326.354"],
        ],
        scope="separate",
        kind="debt_schedule",
        headers=["", "Số cuối năm", "Số đầu năm"],
        source_title="20. VAY; 20.1 Trái phiếu thường dài hạn của HAG năm 2023",
        topic_label="20. VAY; Trái phiếu thường dài hạn",
        period_labels=["Số cuối năm", "Số đầu năm"],
        unit_labels=["Ngàn VND"],
    )
    duplicate = _table(
        "hag-duplicate",
        "HAG_financial_statements_2023_consolidated",
        [
            ["", "Số cuối năm", "Số đầu năm"],
            ["Trái phiếu thường dài hạn", "3.199.130.581", "3.581.600.405"],
            ["TỔNG CỘNG", "4.948.065.559", "5.540.326.354"],
        ],
        scope="consolidated",
        kind="debt_schedule",
        headers=["", "Số cuối năm", "Số đầu năm"],
        source_title="20. VAY; 20.1 Trái phiếu thường dài hạn của HAG năm 2023",
        topic_label="20. VAY; Trái phiếu thường dài hạn",
        period_labels=["Số cuối năm", "Số đầu năm"],
        unit_labels=["Ngàn VND"],
    )
    result = MODULE._resolve_aggregate_total(
        item,
        spec,
        tables_by_uid={"primary": primary, "duplicate": duplicate},
    )
    assert result is not None
    assert result[0] == Decimal("4948065558")
    assert result[1][0]["internal_table_uid"] == "hag-primary"
    assert result[1][0]["candidate_navigation_rank"] == 1
    assert result[1][0]["candidate_navigation_tiebreak"] is True


def test_derivative_total_requires_contract_value_header() -> None:
    question = "Tổng giá trị hợp đồng công cụ phái sinh cuối năm 2022 của KLB là bao nhiêu triệu đồng?"
    item = _item(question, "KLB", 2022, candidate_uids=["good", "collateral"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    good = _table(
        "good",
        "KLB_financial_statements_2022_consolidated",
        [
            ["", "Tổng giá trị hợp đồng", "Tổng giá trị ghi sổ"],
            ["Số cuối năm", "", ""],
            ["Công cụ tài chính phái sinh tiền tệ", "1.692.506", "21.876"],
            ["Công cụ tài chính phái sinh tiền tệ", "1.388.270", "16.831"],
            ["Cộng", "3.080.776", "38.707"],
        ],
        scope="consolidated",
        kind="financial_note_detail",
        headers=["", "Tổng giá trị hợp đồng", "Tổng giá trị ghi sổ"],
        source_title="5. Các công cụ tài chính phái sinh và các tài sản tài chính khác",
        topic_label="5. Các công cụ tài chính phái sinh và các tài sản tài chính khác",
        period_labels=["Số cuối năm", "Số đầu năm"],
        unit_labels=["Triệu VND"],
    )
    collateral = _table(
        "collateral",
        "KLB_financial_statements_2022_separate",
        [
            ["", "Số cuối năm", "Số đầu năm"],
            ["Công cụ tài chính phái sinh", "84.803.992", "1"],
            ["Cộng", "84.803.992", "1"],
        ],
        scope="separate",
        kind="debt_schedule",
        headers=["", "Số cuối năm", "Số đầu năm"],
        source_title="5. Các công cụ tài chính phái sinh và các tài sản tài chính khác",
        topic_label="5. Các công cụ tài chính phái sinh và các tài sản tài chính khác",
        period_labels=["Số cuối năm", "Số đầu năm"],
        unit_labels=["Triệu VND"],
    )
    result = MODULE._resolve_aggregate_total(
        item,
        spec,
        tables_by_uid={"good": good, "collateral": collateral},
    )
    assert result is not None
    assert result[0] == Decimal("3080776")
    assert result[1][0]["internal_table_uid"] == "good"


def test_unscoped_conflicting_totals_remain_fail_closed() -> None:
    question = "Tổng doanh thu của GEG trong năm 2019 là bao nhiêu tỷ đồng?"
    item = _item(question, "GEG", 2019)
    spec = MODULE._family_spec(item)
    assert spec is not None
    def table(uid: str, value: str, scope: str) -> dict:
        return _table(
            uid,
            f"GEG_financial_statements_2019_{scope}",
            [["", "Điện", "Tổng cộng"], ["Tổng doanh thu", value, value]],
            scope=scope,
            kind="income_statement",
            headers=["", "Điện VND", "Tổng cộng VND"],
            source_title="Báo cáo bộ phận theo lĩnh vực kinh doanh năm 2019",
            topic_label="Báo cáo bộ phận năm 2019",
            unit_labels=["VND"],
        )
    result = MODULE._resolve_aggregate_total(
        item,
        spec,
        tables_by_uid={
            "separate": table("separate", "100.000.000.000", "separate"),
            "consolidated": table("consolidated", "200.000.000.000", "consolidated"),
        },
    )
    assert result is None


def test_missing_source_unit_is_rejected() -> None:
    item = _item(_hpx_question(), "HPX", 2024, scope="consolidated")
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "no-unit",
        "HPX_financial_statements_2024_consolidated",
        [["", "Tổng"], ["Tổng giá vốn", "1.097.119.148.438"]],
        scope="consolidated",
        kind="segment_reporting",
        headers=["", "Tổng"],
        source_title="Báo cáo bộ phận năm 2024",
        topic_label="Báo cáo bộ phận năm 2024",
        unit_labels=[],
    )
    assert MODULE._resolve_aggregate_total(item, spec, tables_by_uid={"x": table}) is None
