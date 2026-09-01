from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path


def _builder_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/e2e/build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location("competition_submission_builder_semantic", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table(*, uid, ticker, year, scope, kind, headers, rows, row_paths=None):
    return {
        "internal_table_uid": uid,
        "ticker": ticker,
        "report_year": year,
        "scope": scope,
        "headers": deepcopy(headers),
        "rows": deepcopy(rows),
        "row_paths": deepcopy(row_paths or [""] * len(rows)),
        "header_row_indices": [0],
        "table_function": {"kind": kind, "label": kind},
        "context_trace": {"source_title": kind},
        "unit_hint": None,
        "document_id": f"{ticker}_financial_statements_{year}_{scope}",
    }


def _item(question, *, ticker=None, year=2021, scope=None, metric=None):
    ticker_list = [ticker] if ticker else []
    return {
        "question": question,
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ticker_list,
            "years": [year],
            "scope": scope,
            "operands": [{"metric": metric or question}],
        },
    }


def _candidate(uid, document_id, row, *, rank=1):
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "rank": rank,
        "review_score": 1.0,
        "evidence_window": [{"index": row[0], "row": row[1]}],
    }


def test_semantic_column_binds_merged_period_to_net_value():
    builder = _builder_module()
    table = _table(
        uid="bvh-investment",
        ticker="BVH",
        year=2021,
        scope="separate",
        kind="financial_note",
        headers=[
            "Nhãn dòng",
            "Ngày 31 tháng 12 năm 2021 · Giá gốc VND",
            "Dự phòng VND",
            "Giá trị thuần VND",
            "Ngày 31 tháng 12 năm 2020 · Giá gốc VND",
            "Dự phòng VND",
            "Giá trị thuần VND",
        ],
        rows=[
            ["", "Ngày 31 tháng 12 năm 2021", "", "", "Ngày 31 tháng 12 năm 2020", "", ""],
            ["", "Giá gốc VND", "Dự phòng VND", "Giá trị thuần VND", "Giá gốc VND", "Dự phòng VND", "Giá trị thuần VND"],
            ["Đầu tư góp vốn vào đơn vị khác", "527", "-", "492", "616", "-", "526"],
        ],
        row_paths=["", "", "Đầu tư góp vốn vào đơn vị khác"],
    )
    evidence = [{"index": index, "row": row} for index, row in enumerate(table["rows"])]
    item = _item(
        "Tổng giá trị thuần khoản đầu tư góp vốn vào đơn vị khác của BVH đến ngày 31/12/2021 là bao nhiêu triệu đồng?",
        ticker="BVH",
        year=2021,
        metric="Tổng giá trị thuần khoản đầu tư góp vốn vào đơn vị khác BVH",
    )

    choice = builder.choose_semantic_column(
        table,
        evidence,
        2,
        table["rows"][2],
        2021,
        item["question"],
        item["question_plan"]["operands"][0]["metric"],
        parser=builder.parse_decimal,
    )

    assert choice == (3, builder.Decimal("492"))


def test_semantic_rank_recovers_full_company_alias_and_rejects_other_issuer():
    builder = _builder_module()
    question = "Chi phí lãi vay của Công ty Cổ phần Thép Nam Kim trong năm 2021 là bao nhiêu tỷ đồng?"
    item = _item(question, year=2021, metric="Chi phí lãi vay Công ty Cổ phần Thép Nam Kim")
    correct = _table(
        uid="nkg-interest",
        ticker="NKG",
        year=2021,
        scope="consolidated",
        kind="income_statement",
        headers=["Nhãn dòng", "2021 VND"],
        rows=[["Nhãn dòng", "2021 VND"], ["Chi phí lãi vay", "41.077.184.523"]],
    )
    wrong = _table(
        uid="cre-interest",
        ticker="CRE",
        year=2021,
        scope="separate",
        kind="debt_schedule",
        headers=["Nhãn dòng", "2021 VND"],
        rows=[["Nhãn dòng", "2021 VND"], ["Chi phí lãi vay", "99.000.000.000"]],
    )
    candidates = [
        _candidate("cre-interest", wrong["document_id"], (1, wrong["rows"][1]), rank=1),
        _candidate("nkg-interest", correct["document_id"], (1, correct["rows"][1]), rank=2),
    ]
    item["candidates"] = candidates

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"nkg-interest": correct, "cre-interest": wrong},
        allow_uncertain=True,
    )

    assert ranked
    assert ranked[0]["internal_table_uid"] == "nkg-interest"
    assert ranked[0]["value"] == builder.Decimal("41.077184523")
    assert all(row["internal_table_uid"] != "cre-interest" for row in ranked)


def test_semantic_rank_uses_aggregate_column_for_segment_total():
    builder = _builder_module()
    item = _item(
        "Doanh thu của DTK năm 2024 là bao nhiêu trăm tỷ đồng?",
        ticker="DTK",
        year=2024,
        metric="Doanh thu DTK",
    )
    table = _table(
        uid="dtk-revenue",
        ticker="DTK",
        year=2024,
        scope="separate",
        kind="income_statement",
        headers=["Nhãn dòng", "Cột nguồn 2", "Cột nguồn 3", "Cột nguồn 4", "Cột nguồn 5"],
        rows=[
            ["", "Điện", "Than", "Khác", "Cộng"],
            ["Doanh thu", "12.266.582.474.537", "321.623.638.295", "125.715.051.282", "12.713.921.164.114"],
        ],
    )
    item["candidates"] = [_candidate("dtk-revenue", table["document_id"], (1, table["rows"][1]))]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"dtk-revenue": table},
        allow_uncertain=True,
    )

    assert ranked
    assert ranked[0]["column_index"] == 4
    assert ranked[0]["raw_value"] == builder.Decimal("12713921164114")


def test_semantic_guard_rejects_navigation_and_wrong_period_cells():
    builder = _builder_module()
    item = _item(
        "Tổng cam kết cho thuê hoạt động của GEX đến ngày 31/12/2018 là bao nhiêu tỷ đồng?",
        ticker="GEX",
        year=2018,
        scope="separate",
        metric="Tổng cam kết cho thuê hoạt động GEX",
    )
    table = _table(
        uid="gex-toc",
        ticker="GEX",
        year=2018,
        scope="separate",
        kind="cash_flow_statement",
        headers=["Nhãn dòng", "Cột nguồn 2"],
        rows=[
            ["", "Trang"],
            ["Báo cáo kết quả hoạt động kinh doanh riêng", "7"],
        ],
    )
    item["candidates"] = [_candidate("gex-toc", table["document_id"], (1, table["rows"][1]))]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"gex-toc": table},
        allow_uncertain=True,
    )

    assert ranked == []


def test_registry_identity_adapter_does_not_mutate_frozen_review_item():
    builder = _builder_module()
    original = {
        "question": "Nguyên giá tài sản cố định thuê tài chính của Công ty Cổ phần Tập đoàn C.E.O đến ngày 31/12/2017 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": [],
            "years": [2017],
            "warnings": ["No ticker/company alias was resolved."],
            "operands": [{"metric": "Nguyên giá tài sản cố định thuê tài chính C.E.O", "ticker": None}],
        },
    }
    enriched, stats = builder.enrich_review_items_with_registry_identity({221: original})

    assert original["question_plan"]["tickers"] == []
    assert enriched[221]["question_plan"]["tickers"] == ["CEO"]
    assert enriched[221]["question_plan"]["operands"][0]["ticker"] == "CEO"
    assert stats["recovered_single_registry_identity"] == 1


def test_semantic_guard_binds_long_term_loan_to_the_reported_row():
    builder = _builder_module()
    table = _table(
        uid="hpg-financial-assets",
        ticker="HPG",
        year=2021,
        scope="consolidated",
        kind="balance_sheet",
        headers=["Nhãn dòng", "31/12/2021 Giá trị ghi sổ Triệu VND"],
        rows=[
            ["Dược phân loại là các khoản cho vay và phải thu:", ""],
            ["- Tiền và các khoản tương đương tiền", "22.471.376"],
            ["- Phải thu về cho vay dài hạn và phải thu dài hạn khác", "119.105"],
        ],
        row_paths=[
            "Dược phân loại là các khoản cho vay và phải thu:",
            "Dược phân loại là các khoản cho vay và phải thu > Tiền và các khoản tương đương tiền",
            "Dược phân loại là các khoản cho vay và phải thu > Phải thu về cho vay dài hạn",
        ],
    )
    item = _item(
        "Tổng số dư phải thu về cho vay dài hạn cuối năm 2021 của HPG là bao nhiêu triệu đồng?",
        ticker="HPG",
        year=2021,
        metric="Tổng số dư phải thu về cho vay dài hạn HPG",
    )
    item["candidates"] = [
        _candidate("hpg-financial-assets", table["document_id"], (1, table["rows"][1]), rank=1),
        _candidate("hpg-financial-assets", table["document_id"], (2, table["rows"][2]), rank=2),
    ]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"hpg-financial-assets": table},
        allow_uncertain=True,
    )

    assert ranked
    assert ranked[0]["row_index"] == 2
    assert all(row["row_index"] != 1 for row in ranked)


def test_semantic_guard_rejects_derivative_maturity_component_for_contract_total():
    builder = _builder_module()
    table = _table(
        uid="klb-derivatives",
        ticker="KLB",
        year=2022,
        scope="separate",
        kind="financial_note",
        headers=["Nhãn dòng", "Cộng · Triệu VND"],
        rows=[
            ["Công cụ tài chính phái sinh và các tài sản tài chính khác", "38.707"],
            ["Tổng giá trị hợp đồng công cụ phái sinh", "41.200"],
        ],
    )
    item = _item(
        "Tổng giá trị hợp đồng công cụ phái sinh cuối năm 2022 của KLB là bao nhiêu triệu đồng?",
        ticker="KLB",
        year=2022,
        metric="Tổng giá trị hợp đồng công cụ phái sinh KLB",
    )
    item["candidates"] = [
        _candidate("klb-derivatives", table["document_id"], (0, table["rows"][0]), rank=1),
        _candidate("klb-derivatives", table["document_id"], (1, table["rows"][1]), rank=2),
    ]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"klb-derivatives": table},
        allow_uncertain=True,
    )

    assert ranked
    assert ranked[0]["row_index"] == 1
    assert all(row["row_index"] != 0 for row in ranked)


def test_semantic_guard_binds_industry_total_to_the_matching_column_and_terminal_row():
    builder = _builder_module()
    table = _table(
        uid="vpb-industry",
        ticker="VPB",
        year=2020,
        scope="consolidated",
        kind="financial_note",
        headers=["Nhãn dòng", "Tổng dư nợ cho vay", "Tổng tiền gửi, tiền vay"],
        rows=[
            ["Nhãn dòng", "Tổng dư nợ cho vay", "Tổng tiền gửi, tiền vay"],
            ["Trong nước", "261.798.526", "298.256.900"],
            ["Nước ngoài", "-", "24.471.695"],
            ["", "261.798.526", "322.728.595"],
        ],
    )
    item = _item(
        "Vào cuối năm 2020, tổng dư nợ cho vay theo ngành nghề kinh doanh của VPB là bao nhiêu triệu đồng?",
        ticker="VPB",
        year=2020,
        metric="Tổng dư nợ cho vay theo ngành nghề kinh doanh VPB",
    )
    item["candidates"] = [
        _candidate("vpb-industry", table["document_id"], (1, table["rows"][1]), rank=1),
        _candidate("vpb-industry", table["document_id"], (3, table["rows"][3]), rank=2),
    ]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"vpb-industry": table},
        allow_uncertain=True,
    )

    assert ranked
    assert ranked[0]["row_index"] == 3
    assert ranked[0]["column_index"] == 1


def test_semantic_guard_rejects_deposit_and_loan_combined_row_for_loan_total():
    builder = _builder_module()
    table = _table(
        uid="bid-mixed",
        ticker="BID",
        year=2023,
        scope="separate",
        kind="debt_schedule",
        headers=["Nhãn dòng", "Tổng · Triệu VND"],
        rows=[["Tiền gửi và cho vay các TCTD khác", "60.300.769"]],
    )
    item = _item(
        "Tổng dư nợ cho vay cuối năm 2023 của BID là bao nhiêu triệu đồng?",
        ticker="BID",
        year=2023,
        metric="Tổng dư nợ cho vay BID",
    )
    item["candidates"] = [_candidate("bid-mixed", table["document_id"], (0, table["rows"][0]))]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"bid-mixed": table},
        allow_uncertain=True,
    )

    assert ranked == []


def test_semantic_guard_rejects_prior_year_when_requested_year_cell_is_dash():
    builder = _builder_module()
    table = _table(
        uid="mbb-associate-investment",
        ticker="MBB",
        year=2016,
        scope="separate",
        kind="financial_note",
        headers=["Nhãn dòng", "31/12/2016 Triệu VND", "31/12/2015 Triệu VND"],
        rows=[["Đầu tư vào công ty liên kết – giá gốc", "-", "258.591"]],
    )
    item = _item(
        "Tổng giá gốc các khoản đầu tư vào công ty liên kết của MBB đến ngày 31 tháng 12 năm 2016 là bao nhiêu triệu đồng?",
        ticker="MBB",
        year=2016,
        metric="Tổng giá gốc đầu tư vào công ty liên kết MBB",
    )
    item["candidates"] = [
        _candidate(
            "mbb-associate-investment",
            table["document_id"],
            (0, table["rows"][0]),
        )
    ]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"mbb-associate-investment": table},
        allow_uncertain=True,
    )

    assert ranked == []


def test_semantic_guard_distinguishes_customer_receivable_from_other_receivable():
    builder = _builder_module()
    table = _table(
        uid="nvl-receivables",
        ticker="NVL",
        year=2019,
        scope="separate",
        kind="balance_sheet",
        headers=["Nhãn dòng", "31/12/2019 VND"],
        rows=[
            ["Phải thu ngắn hạn khác", "1.469.123.938.629"],
            ["Phải thu ngắn hạn của khách hàng", "107.330.909.046"],
        ],
    )
    item = _item(
        "Tổng số dư phải thu ngắn hạn của khách hàng của NVL cuối năm 2019 là bao nhiêu tỷ đồng?",
        ticker="NVL",
        year=2019,
        metric="Tổng số dư phải thu ngắn hạn của khách hàng NVL",
    )
    item["candidates"] = [
        _candidate("nvl-receivables", table["document_id"], (0, table["rows"][0]), rank=1),
        _candidate("nvl-receivables", table["document_id"], (1, table["rows"][1]), rank=2),
    ]

    ranked = builder.rank_semantic_cells(
        item,
        tables_by_uid={"nvl-receivables": table},
        allow_uncertain=True,
    )

    assert ranked
    assert ranked[0]["row_index"] == 1
    assert all(row["row_index"] != 0 for row in ranked)
