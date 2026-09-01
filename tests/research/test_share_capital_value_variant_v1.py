from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_share_capital_value_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("share_capital_value_variant_test", SCRIPT)
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
        "id": 360,
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
    scope: str = "separate",
    kind: str = "balance_sheet",
    headers: list[str],
    source_title: str | None = None,
    context_before: str | None = None,
    search_text: str | None = None,
) -> dict:
    table = {
        "internal_table_uid": uid,
        "document_id": document_id,
        "scope": scope,
        "report_year": int(document_id.split("_")[3]),
        "headers": headers,
        "column_labels": headers,
        "header_row_indices": [0],
        "rows": rows,
        "table_function": {"kind": kind},
        "context_trace": {
            "source_title": source_title
            or f"Báo cáo tài chính cho năm kết thúc ngày 31 tháng 12 năm {document_id.split('_')[3]}",
            "summary": "Báo cáo vốn chủ sở hữu.",
            "topic": {"label": "Vốn chủ sở hữu", "source": "numbered_source_heading"},
            "period_labels": [],
            "unit_labels": [],
        },
    }
    if context_before is not None:
        table["context_before"] = context_before
    if search_text is not None:
        table["search_text"] = search_text
    return table


def _owner_question() -> str:
    return (
        "Tổng vốn góp đến ngày 31/12/2025 của công ty mẹ CTCP Phát triển "
        "Hạ tầng Kỹ thuật là bao nhiêu nghìn tỷ đồng?"
    )


def test_family_spec_is_monetary_equity_lookup_only() -> None:
    owner = _item(_owner_question(), "IJC", 2025, scope="separate")
    spec = MODULE._family_spec(owner)
    assert spec is not None
    assert spec["metric_kind"] == "owner_contribution"
    assert MODULE._family_spec(
        _item("Số lượng cổ phiếu đang lưu hành của IJC cuối năm 2025 là bao nhiêu?", "IJC", 2025)
    ) is None
    assert MODULE._family_spec(
        _item("Vốn cổ phần của IJC cuối năm 2025 chênh lệch bao nhiêu tỷ đồng so với VNM?", "IJC", 2025)
    ) is None
    assert MODULE._family_spec(
        _item("Tỷ trọng vốn góp của cổ đông A trong tổng vốn góp đến ngày 31/12/2025 là bao nhiêu phần trăm?", "IJC", 2025)
    ) is None


def test_named_direct_investment_is_a_target_bound_subfamily() -> None:
    question = (
        "Số dư khoản mục vốn góp trực tiếp của công ty mẹ BVH vào "
        "Quỹ Đầu tư Giá trị Bảo Việt đến ngày 31 tháng 12 năm 2019 "
        "là bao nhiêu triệu đồng?"
    )
    spec = MODULE._family_spec(_item(question, "BVH", 2019, scope="separate"))
    assert spec is not None
    assert spec["metric_kind"] == "direct_investment_contribution"
    assert spec["target_phrase"] == "quy dau tu gia tri bao viet"


def test_direct_investment_schedule_binds_named_target_and_vnd_amount() -> None:
    question = (
        "Số dư khoản mục vốn góp trực tiếp của công ty mẹ BVH vào "
        "Quỹ Đầu tư Giá trị Bảo Việt đến ngày 31 tháng 12 năm 2019 "
        "là bao nhiêu triệu đồng?"
    )
    item = _item(question, "BVH", 2019, scope="separate", candidate_uids=["direct"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "direct",
        "BVH_financial_statements_2019_separate",
        [
            ["Nhãn dòng", "Số vốn góp VND", "Tỷ lệ trên vốn điều lệ"],
            ["Đầu tư trực tiếp của Tập đoàn Bảo Việt", "420.000.000.000", "42%"],
        ],
        kind="financial_data_schedule",
        headers=["Nhãn dòng", "Số vốn góp VND", "Tỷ lệ trên vốn điều lệ"],
        context_before=(
            "Quỹ Đầu tư Giá trị Bảo Việt (BVIF). Tại thời điểm ngày "
            "31 tháng 12 năm 2019, vốn góp trực tiếp và gián tiếp của "
            "Tập đoàn vào BVIF như sau:"
        ),
    )
    result = MODULE._resolve_share_capital_value(
        item,
        spec,
        tables_by_uid={"direct": table},
    )
    assert result is not None
    assert result[0] == Decimal("420000")
    assert result[1][0]["row_index"] == 1
    assert result[1][0]["column_index"] == 1
    assert result[1][0]["source_to_vnd_multiplier"] == "1"


def test_named_target_does_not_alias_generic_owner_equity_row() -> None:
    question = (
        "Số dư khoản mục vốn góp trực tiếp của công ty mẹ BVH vào "
        "Quỹ Đầu tư Giá trị Bảo Việt đến ngày 31 tháng 12 năm 2019 "
        "là bao nhiêu triệu đồng?"
    )
    item = _item(question, "BVH", 2019, scope="separate", candidate_uids=["owner"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "owner",
        "BVH_financial_statements_2019_separate",
        [
            ["Mã số", "NGUỒN VỐN", "Ngày 31 tháng 12 năm 2019 VND"],
            ["411", "1. Vốn góp của chủ sở hữu", "7.423.227.640.000"],
        ],
        headers=["Mã số", "NGUỒN VỐN", "Ngày 31 tháng 12 năm 2019 VND"],
        context_before="Quỹ Đầu tư Giá trị Bảo Việt tại ngày 31 tháng 12 năm 2019.",
    )
    assert MODULE._resolve_share_capital_value(item, spec, tables_by_uid={"owner": table}) is None


def test_balance_owner_row_uses_ending_column_and_same_document_unit_anchor() -> None:
    item = _item(_owner_question(), "IJC", 2025, scope="separate", candidate_uids=["target"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    target = _table(
        "target",
        "IJC_financial_statements_2025_separate",
        [
            ["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
            ["1. Vốn góp của chủ sở hữu", "411", "V.22", "6.295.806.400.000", "3.777.483.840.000"],
        ],
        headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
    )
    unit_anchor = _table(
        "unit-anchor",
        "IJC_financial_statements_2025_separate",
        [
            ["CHỈ TIÊU", "Mã số", "Thuyết minh", "31/12/2025 VND", "1/1/2025 VND"],
            ["Doanh thu", "01", "", "10", "9"],
        ],
        headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "31/12/2025 VND", "1/1/2025 VND"],
    )
    result = MODULE._resolve_share_capital_value(
        item,
        spec,
        tables_by_uid={"target": target, "unit-anchor": unit_anchor},
    )
    assert result is not None
    assert result[0] == Decimal("6.2958064")
    assert result[1][0]["row_index"] == 1
    assert result[1][0]["column_index"] == 3
    assert result[1][0]["unit_origin"] == "same_document"
    assert result[1][0]["unit_anchor_internal_table_uid"] == "unit-anchor"


def test_complete_asset_fallback_recovers_exact_row_outside_navigation_top_k() -> None:
    item = _item(_owner_question(), "IJC", 2025, scope="separate", candidate_uids=["hint"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    hint = _table(
        "hint",
        "IJC_financial_statements_2025_separate",
        [["Nhãn dòng", "31/12/2025 VND"], ["Góp vốn", "12.000.000.000"]],
        kind="financial_note",
        headers=["Nhãn dòng", "31/12/2025 VND"],
    )
    target = _table(
        "target",
        "IJC_financial_statements_2025_separate",
        [
            ["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
            ["Vốn góp của chủ sở hữu", "411", "", "6.295.806.400.000", "3.777.483.840.000"],
        ],
        source_title="Báo cáo tài chính Đơn vị tính: VND",
        headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
    )
    result = MODULE._resolve_share_capital_value(
        item,
        spec,
        tables_by_uid={"hint": hint, "target": target},
    )
    assert result is not None
    assert result[0] == Decimal("6.2958064")
    assert result[1][0]["internal_table_uid"] == "target"
    assert MODULE._STATS["candidate_navigation_fallback_to_complete_asset"] >= 1


def test_equity_change_row_binds_current_closing_value_not_movement() -> None:
    question = "Vốn góp của chủ sở hữu của FTS cuối năm 2020 là bao nhiêu nghìn tỷ đồng?"
    item = _item(question, "FTS", 2020, candidate_uids=["equity"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "equity",
        "FTS_financial_statements_2020_separate",
        [
            ["CHỈ TIÊU", "TM", "Số dư đầu năm", "", "Số tăng/ giảm trong năm", "", "", "", "Số dư cuối năm", ""],
            ["", "", "Năm trước", "Năm nay", "Năm trước", "", "Năm nay", "", "Năm trước", "Năm nay"],
            ["", "", "", "", "Tăng", "Giảm", "Tăng", "Giảm", "", ""],
            ["A", "B", "1", "2", "3", "4", "5", "6", "7", "8"],
            ["1. Vốn góp của chủ sở hữu", "", "1.294.815.013.850", "1.404.118.643.850", "109.303.630.000", "-", "120.232.980.000", "120.234.136.200", "1.404.118.643.850", "1.404.117.487.650"],
        ],
        kind="equity_change_statement",
        headers=[
            "CHỈ TIÊU",
            "TM",
            "Số dư đầu năm · Năm trước",
            "Năm nay",
            "Số tăng/ giảm trong năm · Năm trước",
            "Cột nguồn 6",
            "Năm nay",
            "Cột nguồn 8",
            "Số dư cuối năm · Năm trước",
            "Năm nay",
        ],
        source_title="Báo cáo biến động vốn chủ sở hữu năm 2020 Đơn vị tính: VND",
    )
    result = MODULE._resolve_share_capital_value(item, spec, tables_by_uid={"equity": table})
    assert result is not None
    assert result[0] == Decimal("1.40411748765")
    assert result[1][0]["column_index"] == 9


def test_share_capital_binds_money_column_not_share_count_column() -> None:
    question = "Vốn cổ phần đã phát hành của VGT là bao nhiêu nghìn tỷ đồng vào cuối năm 2024?"
    item = _item(question, "VGT", 2024, scope="separate", candidate_uids=["share"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    assert spec["metric_kind"] == "share_capital"
    table = _table(
        "share",
        "VGT_financial_statements_2024_separate",
        [
            ["Nhãn dòng", "31/12/2024 và 1/1/2024 · Số cổ phiếu", "VND"],
            ["Vốn cổ phần đã phát hành Cổ phiếu phổ thông", "500.000.000", "5.000.000.000.000"],
        ],
        kind="financial_note",
        headers=["Nhãn dòng", "31/12/2024 và 1/1/2024 · Số cổ phiếu", "VND"],
    )
    result = MODULE._resolve_share_capital_value(item, spec, tables_by_uid={"share": table})
    assert result is not None
    assert result[0] == Decimal("5")
    assert result[1][0]["column_index"] == 2


def test_transaction_gop_von_row_is_not_owner_contribution_balance() -> None:
    item = _item(_owner_question(), "IJC", 2025, scope="separate", candidate_uids=["bad"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "bad",
        "IJC_financial_statements_2025_separate",
        [
            ["Nhãn dòng", "31/12/2025 VND"],
            ["Góp vốn", "12.000.000.000"],
        ],
        kind="financial_note",
        headers=["Nhãn dòng", "31/12/2025 VND"],
    )
    assert MODULE._resolve_share_capital_value(item, spec, tables_by_uid={"bad": table}) is None


def test_conflicting_duplicate_equity_answers_are_rejected() -> None:
    item = _item(_owner_question(), "IJC", 2025, scope="separate", candidate_uids=["one", "two"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table(
        "one",
        "IJC_financial_statements_2025_separate",
        [["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"], ["Vốn góp của chủ sở hữu", "411", "", "6.295.806.400.000", "3.777.483.840.000"]],
        headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
        source_title="Báo cáo tài chính Đơn vị tính: VND",
    )
    second = _table(
        "two",
        "IJC_financial_statements_2025_separate",
        [["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"], ["Vốn góp của chủ sở hữu", "411", "", "6.295.806.401.000", "3.777.483.840.000"]],
        headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
        source_title="Báo cáo tài chính Đơn vị tính: VND",
    )
    assert MODULE._resolve_share_capital_value(item, spec, tables_by_uid={"one": first, "two": second}) is None


def test_unitless_table_without_same_document_anchor_is_rejected() -> None:
    item = _item(_owner_question(), "IJC", 2025, scope="separate", candidate_uids=["unitless"])
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "unitless",
        "IJC_financial_statements_2025_separate",
        [["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"], ["Vốn góp của chủ sở hữu", "411", "", "6.295.806.400.000", "3.777.483.840.000"]],
        headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
    )
    assert MODULE._resolve_share_capital_value(item, spec, tables_by_uid={"unitless": table}) is None
