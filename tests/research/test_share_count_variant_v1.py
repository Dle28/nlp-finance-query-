from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_share_count_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("share_count_variant_test", SCRIPT)
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
        "id": 100,
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
    topic: str = "24.5 Cổ phiếu",
    period_labels: list[str] | None = None,
) -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "scope": scope,
        "report_year": int(document_id.split("_")[3]),
        "headers": headers,
        "column_labels": headers,
        "header_row_indices": [0, 1],
        "rows": rows,
        "table_function": {"kind": kind},
        "context_trace": {
            "source_title": f"Báo cáo tài chính; {topic}",
            "topic": {"label": topic, "source": "numbered_source_heading"},
            "period_labels": period_labels or [],
            "unit_labels": [],
        },
    }


def _total_question() -> str:
    return "Tổng số lượng cổ phần của Tập đoàn Bảo Việt (BVH) vào cuối năm 2018 là bao nhiêu cổ phần?"


def _outstanding_question() -> str:
    return "Số lượng cổ phiếu phổ thông đang lưu hành của VJC cuối năm 2021 là bao nhiêu?"


def test_family_spec_is_count_state_only() -> None:
    spec = MODULE._family_spec(_item(_total_question(), "BVH", 2018))
    assert spec is not None
    assert spec["state"] == "total"
    assert MODULE._family_spec(
        _item("Vốn cổ phần của BVH cuối năm 2018 là bao nhiêu nghìn tỷ đồng?", "BVH", 2018)
    ) is None
    assert MODULE._family_spec(
        _item(
            "Số lượng cổ phiếu phổ thông đang lưu hành bình quân trong năm 2021 là bao nhiêu?",
            "VJC",
            2021,
        )
    ) is None


def test_total_share_count_binds_the_count_column_not_vnd_value_column() -> None:
    item = _item(_total_question(), "BVH", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "bvh-share-count",
        "BVH_financial_statements_2018_consolidated",
        [
            ["", "Ngày 31 tháng 12 năm 2018", "", "Ngày 31 tháng 12 năm 2017", ""],
            ["", "Số lượng", "Giá trị VND", "Số lượng", "Giá trị VND"],
            ["Cổ phiếu đăng ký phát hành", "700.886.434", "7.008.864.340.000", "680.471.400", "6.804.714.340.000"],
            ["Cổ phiếu đã bán ra công chúng", "700.886.434", "7.008.864.340.000", "680.471.400", "6.804.714.340.000"],
            ["Cổ phiếu phổ thông", "700.886.434", "7.008.864.340.000", "680.471.400", "6.804.714.340.000"],
            ["Cổ phiếu đang lưu hành (*)", "700.886.434", "7.008.864.340.000", "680.471.400", "6.804.714.340.000"],
        ],
        scope="consolidated",
        headers=[
            "Nhãn dòng",
            "Ngày 31 tháng 12 năm 2018 · Số lượng",
            "Giá trị VND",
            "Ngày 31 tháng 12 năm 2017 · Số lượng",
            "Giá trị VND",
        ],
    )
    result = MODULE._resolve_share_count(
        item, spec, tables_by_uid={"bvh-share-count": table}
    )
    assert result is not None
    assert result[0] == Decimal("700886434")
    assert result[1][0]["column_index"] == 1
    assert "gia tri" not in result[1][0]["column_context"]


def test_outstanding_state_uses_unmarked_second_header_row_and_current_year() -> None:
    item = _item(_outstanding_question(), "VJC", 2021)
    spec = MODULE._family_spec(item)
    assert spec is not None
    assert spec["state"] == "outstanding"
    table = _table(
        "vjc-share-count",
        "VJC_financial_statements_2021_consolidated",
        [
            ["", "2021", "", "2020", ""],
            ["", "Cổ phiếu phổ thông", "Cổ phiếu ưu đãi", "Cổ phiếu phổ thông", "Cổ phiếu ưu đãi"],
            ["Số lượng cổ phiếu đăng ký", "541.611.334", "-", "541.611.334", "-"],
            ["Số lượng cổ phiếu đã phát hành", "541.611.334", "-", "541.611.334", "-"],
            ["Số lượng cổ phiếu đã mua lại", "-", "-", "(17.772.740)", "-"],
            ["Số lượng cổ phiếu đang lưu hành", "541.611.334", "-", "523.838.594", "-"],
        ],
        scope="consolidated",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Cột nguồn 2", "Cột nguồn 3", "Cột nguồn 4", "Cột nguồn 5"],
        topic="24 Vốn góp của chủ sở hữu (a) Số lượng cổ phiếu",
    )
    table["header_row_indices"] = []
    result = MODULE._resolve_share_count(
        item, spec, tables_by_uid={"vjc-share-count": table}
    )
    assert result is not None
    assert result[0] == Decimal("541611334")
    assert result[1][0]["row_index"] == 5
    assert result[1][0]["column_index"] == 1
    assert result[1][0]["candidate_source"] == MODULE.VARIANT_PROTOCOL


def test_treasury_and_average_rows_do_not_satisfy_outstanding_state() -> None:
    item = _item(_outstanding_question(), "VJC", 2021)
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "bad-share-rows",
        "VJC_financial_statements_2021_consolidated",
        [
            ["", "Số cuối năm"],
            ["Cổ phiếu quỹ", "17"],
            ["Số lượng cổ phiếu phổ thông đang lưu hành bình quân", "18"],
        ],
        scope="consolidated",
        headers=["Nhãn dòng", "Số cuối năm · Số lượng cổ phiếu"],
    )
    assert MODULE._resolve_share_count(
        item, spec, tables_by_uid={"bad-share-rows": table}
    ) is None


def test_unscoped_conflicting_share_tables_are_rejected() -> None:
    item = _item(_total_question(), "BVH", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    first = _table(
        "bvh-separate",
        "BVH_financial_statements_2018_separate",
        [["", "Số cuối năm"], ["Cổ phiếu phổ thông", "700.886.434"]],
        scope="separate",
        headers=["Nhãn dòng", "Số cuối năm · Số lượng cổ phiếu"],
    )
    second = _table(
        "bvh-consolidated",
        "BVH_financial_statements_2018_consolidated",
        [["", "Số cuối năm"], ["Cổ phiếu phổ thông", "700.886.435"]],
        scope="consolidated",
        headers=["Nhãn dòng", "Số cuối năm · Số lượng cổ phiếu"],
    )
    assert MODULE._resolve_share_count(
        item,
        spec,
        tables_by_uid={"bvh-separate": first, "bvh-consolidated": second},
    ) is None


def test_row_without_share_count_unit_is_rejected() -> None:
    item = _item(_total_question(), "BVH", 2018)
    spec = MODULE._family_spec(item)
    assert spec is not None
    table = _table(
        "not-share",
        "BVH_financial_statements_2018_separate",
        [["", "Số cuối năm"], ["Số dư", "700.886.434"]],
        scope="separate",
        headers=["Nhãn dòng", "Số cuối năm"],
        topic="Bảng dữ liệu khác",
    )
    assert MODULE._resolve_share_count(
        item, spec, tables_by_uid={"not-share": table}
    ) is None
