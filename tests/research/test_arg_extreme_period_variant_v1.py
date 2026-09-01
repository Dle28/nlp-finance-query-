from __future__ import annotations

import hashlib
import importlib.util
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VARIANT_PATH = ROOT / "scripts/research/run_arg_extreme_period_variant_v1.py"


def _load_variant():
    spec = importlib.util.spec_from_file_location(
        "arg_extreme_period_variant_v1_test", VARIANT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _typed_plan(
    *,
    question_id: int = 1,
    ticker: str = "AAA",
    years: list[int] | None = None,
    scope: str = "consolidated",
    operation: str = "max",
):
    requested_years = years or [2020, 2021]
    return {
        "question_id": question_id,
        "decomposition_status": "typed_non_executable",
        "route": "simple_period_extremum",
        "entities": [ticker],
        "years": requested_years,
        "scope": scope,
        "operands": [
            {
                "ticker": ticker,
                "years": [year],
                "metric_hints": ["doanh thu thuan"],
            }
            for year in requested_years
        ],
        "operation_ast": {
            "op": "arg_extreme_period",
            "direction": operation,
            "args": [f"x{index}" for index in range(len(requested_years))],
        },
    }


def _table(variant, year: int, value: str, *, uid: str | None = None):
    table_uid = uid or f"aaa-{year}"
    return {
        "internal_table_uid": table_uid,
        "document_id": f"AAA_financial_statements_{year}_consolidated",
        "ticker": "AAA",
        "report_year": year,
        "scope": "consolidated",
        "headers": ["Nhãn dòng", f"31/12/{year}"],
        "rows": [
            ["Nhãn dòng", f"31/12/{year}"],
            ["Doanh thu thuần", value],
        ],
    }


def test_metric_cleaner_keeps_the_period_metric_and_drops_question_shell() -> None:
    variant = _load_variant()
    typed = _typed_plan()
    variants = variant._metric_variants(
        {
            "question": "Năm nào AAA có doanh thu thuần cao nhất trong các năm 2020 và 2021?"
        },
        typed,
    )

    assert "doanh thu thuan" in variants
    assert "nam nao" not in variants[0]


def test_metric_alias_recovers_exact_tax_paid_cash_flow_row() -> None:
    variant = _load_variant()
    typed = _typed_plan()
    typed["operands"] = [
        {
            "ticker": "VNM",
            "years": [2019],
            "metric_hints": ["giá trị thuế thu nhập doanh nghiệp đã nộp"],
        }
    ]
    variants = variant._metric_variants(
        {
            "question": (
                "Với công ty mẹ VNM, năm nào ghi nhận giá trị thuế thu nhập "
                "doanh nghiệp đã nộp cao nhất trong các năm 2019, 2022 và 2023?"
            )
        },
        typed,
    )

    assert "thue thu nhap doanh nghiep da nop" in variants


def test_row_label_separates_ocr_lower_to_upper_word_boundary() -> None:
    variant = _load_variant()

    label = variant._row_label(
        ["Vốn cổ phần đã phát hànhCổ phiếu phổ thông", "1.180.534.692"]
    )

    assert variant.BUILDER.normalize(label) == (
        "von co phan da phat hanh co phieu pho thong"
    )


def test_strict_code_40_alias_replays_loi_and_lo_khac_as_one_family() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    typed = _typed_plan(
        ticker="TTF",
        years=[2016, 2017, 2023, 2025],
        scope="consolidated",
    )
    typed["operands"] = [
        {
            "ticker": "TTF",
            "years": [year],
            "metric_hints": ["Lợi nhuận khác"],
        }
        for year in [2016, 2017, 2023, 2025]
    ]
    labels = {
        2016: "14. (Lỗ) lợi nhuận khác",
        2017: "14. Lỗ khác",
        2023: "14. (Lỗ) lợi nhuận khác",
        2025: "14. Lợi nhuận khác",
    }
    values = {
        2016: "-602515304",
        2017: "-14555567878",
        2023: "-69981712952",
        2025: "43934748741",
    }
    tables = {}
    for year in labels:
        uid = f"ttf-{year}"
        tables[uid] = {
            "internal_table_uid": uid,
            "document_id": f"TTF_financial_statements_{year}_consolidated",
            "ticker": "TTF",
            "report_year": year,
            "scope": "consolidated",
            "table_function": {"kind": "income_statement"},
            "unit_hint": "vnd",
            "headers": ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay"],
            "context_trace": {
                "source_title": (
                    "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH HỢP NHẤT "
                    f"cho năm tài chính kết thúc ngày 31 tháng 12 năm {year} VND"
                )
            },
            "rows": [
                ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay"],
                ["40", labels[year], "", values[year]],
            ],
        }

    assert variant._is_context_bound_other_profit_loss_row(tables["ttf-2017"], 1)
    result = variant._resolve_arg_extreme(
        {
            "id": 829,
            "question": (
                "Năm nào TTF có mức Lợi nhuận khác cao nhất trong các năm "
                "2016, 2017, 2023 và 2025?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2025")
    assert [selection["value"] for selection in selections] == [
        Decimal("-602515304"),
        Decimal("-14555567878"),
        Decimal("-69981712952"),
        Decimal("43934748741"),
    ]


def test_unscoped_scope_consensus_uses_preferred_scope_when_winner_is_stable() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    years = [2016, 2017, 2023, 2025]
    typed = _typed_plan(
        ticker="TTF",
        years=years,
        scope=None,
    )
    typed["operands"] = [
        {
            "ticker": "TTF",
            "years": [year],
            "metric_hints": ["Lợi nhuận khác"],
        }
        for year in years
    ]
    values = {
        "consolidated": [-602515304, -14555567878, -69981712952, 43934748741],
        "separate": [14353234722, -17623021251, -78924918067, 39908113997],
    }
    labels = {
        2016: "14. (Lỗ) lợi nhuận khác",
        2017: "14. Lỗ khác",
        2023: "14. (Lỗ) lợi nhuận khác",
        2025: "14. Lợi nhuận khác",
    }
    tables = {}
    for scope, scope_values in values.items():
        for year, value in zip(years, scope_values):
            uid = f"ttf-{scope}-{year}"
            tables[uid] = {
                "internal_table_uid": uid,
                "document_id": f"TTF_financial_statements_{year}_{scope}",
                "ticker": "TTF",
                "report_year": year,
                "scope": scope,
                "table_function": {"kind": "income_statement"},
                "unit_hint": "vnd",
                "headers": ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay"],
                "context_trace": {
                    "source_title": (
                        "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH "
                        f"{scope} năm {year} VND"
                    )
                },
                "rows": [
                    ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay"],
                    ["40", labels[year], "29", str(value)],
                ],
            }

    result = variant._resolve_arg_extreme(
        {
            "id": 829,
            "question": (
                "Năm nào TTF có mức Lợi nhuận khác cao nhất trong các năm "
                "2016, 2017, 2023 và 2025?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2025")
    assert {selection["argmax_scope"] for selection in selections} == {
        "consolidated"
    }
    assert variant._TRACE[-1]["scope_consensus"] is True
    assert variant._TRACE[-1]["scope_consensus_winners"] == {
        "consolidated": 2025,
        "separate": 2025,
    }


def test_total_liabilities_code_300_alias_excludes_cash_flow_change_rows() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    years = [2016, 2017, 2018, 2021, 2022]
    values = [
        "12393987700725",
        "9968932894559",
        "8077150487394",
        "2475731954180",
        "1903239627025",
    ]
    typed = _typed_plan(ticker="HND", years=years, scope=None)
    typed["operands"] = [
        {
            "ticker": "HND",
            "years": [year],
            "metric_hints": ["tổng nợ phải trả của HND"],
        }
        for year in years
    ]
    tables = {}
    for year, value in zip(years, values):
        uid = f"hnd-{year}"
        tables[uid] = {
            "internal_table_uid": uid,
            "document_id": f"HND_financial_statements_{year}",
            "ticker": "HND",
            "report_year": year,
            "scope": "unknown",
            "table_function": {"kind": "balance_sheet"},
            "table_section": {"kind": "balance_sheet"},
            "unit_hint": "vnd",
            "headers": [
                "Nhãn dòng",
                "Mã số",
                "Thuyết minh",
                f"31/12/{year} VND",
                f"1/1/{year} VND",
            ],
            "rows": [
                [
                    "Nhãn dòng",
                    "Mã số",
                    "Thuyết minh",
                    f"31/12/{year} VND",
                    f"1/1/{year} VND",
                ],
                ["NỘ PHẢI TRẢ (300 = 310 + 330)", "300", "", value, "0"],
                [
                    "Biến động các khoản phải trả và nợ phải trả khác",
                    "11",
                    "",
                    "-384",
                    "-100",
                ],
            ],
        }

    assert variant._is_context_bound_total_liabilities(tables["hnd-2016"], 1)
    assert not variant._is_context_bound_total_liabilities(tables["hnd-2016"], 2)
    result = variant._resolve_arg_extreme(
        {
            "id": 884,
            "question": (
                "Năm nào có tổng nợ phải trả của HND cao nhất trong các năm "
                "2016, 2017, 2018, 2021 và 2022?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2016")
    assert [selection["value"] for selection in selections] == [
        Decimal(value) for value in values
    ]


def test_tax_payable_code_313_alias_selects_balance_sheet_current_column() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    years = [2020, 2021]
    typed = _typed_plan(ticker="SJG", years=years, scope="consolidated")
    typed["operands"] = [
        {
            "ticker": "SJG",
            "years": [year],
            "metric_hints": ["tổng thuế và các khoản phải nộp Nhà nước"],
        }
        for year in years
    ]
    tables = {}
    for year, value in zip(years, (100, 200)):
        uid = f"sjg-tax-{year}"
        headers = [
            "NGUỒN VỐN",
            "Mã số",
            "Thuyết minh",
            f"31/12/{year} VND",
            f"1/1/{year} VND",
        ]
        tables[uid] = {
            "internal_table_uid": uid,
            "document_id": f"SJG_financial_statements_{year}_consolidated",
            "ticker": "SJG",
            "report_year": year,
            "scope": "consolidated",
            "table_function": {"kind": "balance_sheet"},
            "headers": headers,
            "rows": [
                headers,
                [
                    "3.",
                    "Thuế và các khoản phải nộp Nhà nước",
                    "313",
                    "V.19",
                    str(value),
                    "1",
                ],
            ],
        }

    assert variant._is_context_bound_tax_payable_balance(tables["sjg-tax-2020"], 1)
    result = variant._resolve_arg_extreme(
        {
            "id": 1,
            "question": (
                "Năm nào SJG có tổng thuế và các khoản phải nộp Nhà nước "
                "cao nhất trong các năm 2020 và 2021?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2021")
    assert [selection["value"] for selection in selections] == [
        Decimal("100"),
        Decimal("200"),
    ]
    assert [selection["column_index"] for selection in selections] == [4, 4]


def test_tax_balance_document_currency_fallback_is_hash_bound(tmp_path) -> None:
    variant = _load_variant()
    source_text = (
        "II. NĂM TÀI CHÍNH, ĐƠN VỊ TIỀN TỆ SỬ DỤNG TRONG KẾ TOÁN\n"
        "Đơn vị tiền tệ sử dụng trong kế toán là Đồng Việt Nam (VND)."
    )
    source_path = tmp_path / "sjg-tax.txt"
    source_path.write_text(source_text, encoding="utf-8")
    table = {
        "internal_table_uid": "sjg-tax-source",
        "document_id": "SJG_financial_statements_2019_consolidated",
        "scope": "consolidated",
        "table_function": {"kind": "balance_sheet"},
        "source_path": str(source_path),
        "source_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "char_start": len(source_text),
        "rows": [
            ["NGUỒN VỐN", "Mã số", "Số cuối năm", "Số đầu năm"],
            ["3.", "Thuế và các khoản phải nộp Nhà nước", "313", "10", "1"],
        ],
    }

    assert variant._is_context_bound_tax_payable_balance(table, 1)
    assert variant._raw_tax_balance_source_multiplier(table) == Decimal("1")

    table["source_sha256"] = "0" * 64
    assert variant._raw_tax_balance_source_multiplier(table) is None


def test_construction_cost_payable_alias_collapses_reconstructed_schedule_kind(
    tmp_path,
) -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    years = [2020, 2022, 2025]
    values = {2020: "100", 2022: "200", 2025: "300"}
    tables = {}
    for year in years:
        if year == 2025:
            kind = "financial_data_schedule"
            source_title = "VI.20 Chi phí phải trả"
            rows = [
                ["", f"31/12/{year}", f"01/01/{year}"],
                ["a. Ngắn hạn", "1000", "900"],
                ["Chi phí lãi vay", "500", "400"],
                ["Chi phí xây dựng", values[year], "250"],
                ["b. Dài hạn", "50", "40"],
            ]
            source_text = "Báo cáo tài chính hợp nhất\nĐơn vị tính: Đồng Việt Nam\n<table>"
        else:
            kind = "financial_note_detail"
            source_title = "22 CHI PHÍ PHẢI TRẢ NGẮN HẠN"
            rows = [
                ["", f"31.12.{year}VND", f"31.12.{year - 1}VND"],
                ["Chi phí xây dựng", values[year], "90"],
                ["Khác", "10", "9"],
            ]
            source_text = "Báo cáo tài chính\n<table>"
        source_path = tmp_path / f"nvl-{year}.txt"
        source_path.write_text(source_text, encoding="utf-8")
        tables[f"nvl-{year}"] = {
            "internal_table_uid": f"nvl-{year}",
            "document_id": f"NVL_financial_statements_{year}_consolidated",
            "ticker": "NVL",
            "report_year": year,
            "scope": "consolidated",
            "table_function": {"kind": kind},
            "table_purpose": {"kind": "period_comparison"},
            "table_section": {"kind": "liability"},
            "headers": rows[0],
            "context_trace": {"source_title": source_title},
            "source_path": str(source_path),
            "source_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
            "char_start": len(source_text),
            "rows": rows,
        }

    assert variant._is_context_bound_construction_cost_payable(
        tables["nvl-2025"], 3
    )
    assert variant._comparison_table_kind(tables["nvl-2020"]) == "financial note"
    assert variant._comparison_table_kind(tables["nvl-2025"]) == "financial note"
    assert variant._source_multiplier_for_strict_contract(tables["nvl-2025"]) == Decimal(
        "1"
    )

    typed = _typed_plan(ticker="NVL", years=years, scope="consolidated")
    typed["operands"] = [
        {
            "ticker": "NVL",
            "years": [year],
            "metric_hints": ["số dư chi phí xây dựng phải trả ngắn hạn"],
        }
        for year in years
    ]
    result = variant._resolve_arg_extreme(
        {
            "id": 978,
            "question": (
                "Trong các năm 2020, 2022 và 2025, năm nào NVL có số dư chi "
                "phí xây dựng phải trả ngắn hạn cao nhất?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2025")
    assert [selection["value"] for selection in selections] == [
        Decimal("100"),
        Decimal("200"),
        Decimal("300"),
    ]


def test_trading_debt_securities_alias_binds_main_note_not_investments() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    years = [2022, 2023, 2025]
    values = {2022: "4070884", 2023: "44095180", 2025: "4375694"}
    tables = {}
    for year in years:
        rows = [
            ["Nhãn dòng", f"31/12/{year} triệu đồng", f"31/12/{year - 1} triệu đồng"],
            ["Chứng khoán Nợ", values[year], "1"],
        ]
        tables[f"mbb-{year}"] = {
            "internal_table_uid": f"mbb-{year}",
            "document_id": f"MBB_financial_statements_{year}_consolidated",
            "ticker": "MBB",
            "report_year": year,
            "scope": "consolidated",
            "table_function": {"kind": "financial_note"},
            "table_purpose": {"kind": "period_comparison"},
            "table_section": {"kind": "asset"},
            "unit_hint": "million_vnd",
            "headers": rows[0],
            "context_trace": {
                "source_title": f"8. CHỨNG KHOÁN KINH DOANH năm {year}",
                "summary": "Thuyết minh báo cáo tài chính; đơn vị: triệu đồng",
                "topic": {"label": "8. CHỨNG KHOÁN KINH DOANH"},
            },
            "rows": rows,
        }

    investment = {
        **tables["mbb-2022"],
        "internal_table_uid": "mbb-investment",
        "table_function": {"kind": "financial_note_detail"},
        "context_trace": {
            "source_title": "13. CHỨNG KHOÁN ĐẦU TƯ SẴN SÀNG ĐỂ BÁN",
            "topic": {"label": "13.1. Chứng khoán đầu tư"},
        },
        "rows": [
            tables["mbb-2022"]["rows"][0],
            ["Chứng khoán nợ", "154.506192", "124.551916"],
        ],
    }
    listing_status = {
        **tables["mbb-2022"],
        "internal_table_uid": "mbb-listing-status",
        "context_trace": {
            "source_title": (
                "8. CHỨNG KHOÁN KINH DOANH (tiếp theo) "
                "Tình trạng niêm yết của chứng khoán kinh doanh"
            ),
            "topic": {"label": "Tình trạng niêm yết"},
        },
    }

    assert variant._is_context_bound_trading_debt_securities(
        tables["mbb-2022"], 1
    )
    assert not variant._is_context_bound_trading_debt_securities(investment, 1)
    assert not variant._is_context_bound_trading_debt_securities(
        listing_status, 1
    )
    assert variant._comparison_table_kind(tables["mbb-2022"]) == "financial note"
    assert variant._source_multiplier_for_strict_contract(
        tables["mbb-2022"]
    ) == Decimal("1000000")

    typed = _typed_plan(
        ticker="MBB",
        years=years,
        scope="consolidated",
    )
    typed["operands"] = [
        {
            "ticker": "MBB",
            "years": [year],
            "metric_hints": ["dư nợ chứng khoán kinh doanh nợ"],
        }
        for year in years
    ]
    result = variant._resolve_arg_extreme(
        {
            "id": 883,
            "question": (
                "Ngân hàng TMCP Quân đội có dư nợ chứng khoán kinh doanh nợ "
                "cao nhất vào cuối năm nào trong các năm 2022, 2023 và 2025?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2023")
    assert [selection["value"] for selection in selections] == [
        Decimal("4070884000000"),
        Decimal("44095180000000"),
        Decimal("4375694000000"),
    ]


def test_other_income_uses_only_bounded_raw_source_unit_fallback(tmp_path) -> None:
    variant = _load_variant()

    def make_table(year: int, source_text: str, *, uid: str):
        source_path = tmp_path / f"asm-{year}.txt"
        source_path.write_text(source_text, encoding="utf-8")
        return {
            "internal_table_uid": uid,
            "document_id": f"ASM_financial_statements_{year}_separate",
            "ticker": "ASM",
            "report_year": year,
            "scope": "separate",
            "table_function": {"kind": "income_statement"},
            "headers": ["Mã số", "Chỉ tiêu", "Thuyết minh", f"Năm {year}"],
            "context_trace": {
                "source_title": (
                    "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH RIÊNG "
                    f"năm {year}"
                )
            },
            "rows": [
                ["Mã số", "Chỉ tiêu", "Thuyết minh", f"Năm {year}"],
                ["31", "11. Thu nhập khác", "VI.07", "20"],
            ],
            "source_path": str(source_path),
            "char_start": len(source_text),
        }

    exact = make_table(
        2021,
        "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH RIÊNG\n"
        "Đơn vị tính: VND\n<table>",
        uid="asm-income-2021",
    )
    ocr = make_table(
        2022,
        "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH RIÊNG\n"
        "Don vi ün: √ND\n<table>",
        uid="asm-income-2022",
    )

    assert variant._is_context_bound_other_income(exact, 1)
    assert variant._is_context_bound_other_income(ocr, 1)
    assert variant._source_multiplier_for_strict_contract(exact) == Decimal("1")
    assert variant._source_multiplier_for_strict_contract(ocr) == Decimal("1")

    unrelated = {
        **exact,
        "context_trace": {"source_title": "Bảng dữ liệu tài chính"},
    }
    assert not variant._is_context_bound_other_income(unrelated, 1)
    assert variant._source_multiplier_for_strict_contract(unrelated) is None


def test_strict_source_contract_accepts_other_income_with_bounded_unit_evidence(
    tmp_path,
) -> None:
    variant = _load_variant()

    def make_table(year: int, *, uid: str):
        source_text = (
            "Báo cáo tài chính riêng\n"
            "Đơn vị tính: VND\n"
            f"BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH RIÊNG năm {year}\n<table>"
        )
        source_path = tmp_path / f"asm-contract-{year}.txt"
        source_path.write_text(source_text, encoding="utf-8")
        return {
            "internal_table_uid": uid,
            "document_id": f"ASM_financial_statements_{year}_separate",
            "ticker": "ASM",
            "report_year": year,
            "scope": "separate",
            "table_function": {"kind": "income_statement"},
            "headers": ["Mã số", "Chỉ tiêu", "Thuyết minh", f"Năm {year}"],
            "context_trace": {
                "source_title": (
                    "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH RIÊNG "
                    f"năm {year}"
                )
            },
            "rows": [
                ["Mã số", "Chỉ tiêu", "Thuyết minh", f"Năm {year}"],
                ["31", "11. Thu nhập khác", "VI.07", "20"],
            ],
            "source_path": str(source_path),
            "char_start": len(source_text),
        }

    first = make_table(2021, uid="asm-contract-2021")
    second = make_table(2022, uid="asm-contract-2022")
    item = {
        "id": 822,
        "question": (
            "Năm nào ASM có Thu nhập khác cao nhất trong các năm 2021 và 2022?"
        ),
    }

    assert variant._strict_source_contract_reason(
        item=item,
        years=[2021, 2022],
        selections=[
            _strict_selection(variant, first, year=2021),
            _strict_selection(variant, second, year=2022),
        ],
        tables_by_uid={
            "asm-contract-2021": first,
            "asm-contract-2022": second,
        },
    ) is None


def test_strict_brokerage_alias_replays_short_term_payables_across_raw_kinds() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    typed = _typed_plan(
        ticker="KHG",
        years=[2019, 2023],
        scope="separate",
    )
    typed["operands"] = [
        {
            "ticker": "KHG",
            "years": [year],
            "metric_hints": ["tổng chi phí hoa hồng môi giới bất động sản"],
        }
        for year in [2019, 2023]
    ]
    tables = {}
    for year, kind, label, value in [
        (2019, "financial_note_detail", "Chi phí môi giới bắt động sản", "18711269101"),
        (2023, "debt_schedule", "Chi phí môi giới bất động sản", "26932187593"),
    ]:
        uid = f"khg-{year}"
        tables[uid] = {
            "internal_table_uid": uid,
            "document_id": f"KHG_financial_statements_{year}_separate",
            "ticker": "KHG",
            "report_year": year,
            "scope": "separate",
            "table_function": {"kind": kind},
            "unit_hint": "vnd",
            "headers": ["Nhãn dòng", f"31/12/{year} · VND", f"01/01/{year} · VND"],
            "context_trace": {"source_title": "17. CHI PHÍ PHẢI TRẢ NGẮN HẠN"},
            "rows": [
                ["", f"31/12/{year}", f"01/01/{year}"],
                ["", "VND", "VND"],
                [label, value, "0"],
            ],
        }

    assert variant._is_context_bound_brokerage_cost(tables["khg-2019"])
    assert variant._comparison_table_kind(tables["khg-2019"]) == "debt schedule"
    assert variant._comparison_table_kind(tables["khg-2023"]) == "debt schedule"
    assert variant._strict_context_covers_missing_tokens(
        tables["khg-2019"], missing_tokens={"hoa", "hong"}
    )

    result = variant._resolve_arg_extreme(
        {
            "id": 971,
            "question": (
                "CTCP Tập đoàn Khải Hoàn Land ở phạm vi công ty mẹ ghi nhận "
                "tổng chi phí hoa hồng môi giới bất động sản cao nhất vào năm "
                "nào trong giai đoạn 2019 đến 2023?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2023")
    assert [selection["value"] for selection in selections] == [
        Decimal("18711269101"),
        Decimal("26932187593"),
    ]


def test_strict_crown_payable_alias_replays_related_party_schedule() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    typed = _typed_plan(
        ticker="SAB",
        years=[2019, 2021, 2025],
        scope="separate",
    )
    typed["operands"] = [
        {
            "ticker": "SAB",
            "years": [year],
            "metric_hints": [
                "số dư cuối kỳ phải trả cho Công ty Liên doanh TNHH Crown Sài Gòn"
            ],
        }
        for year in [2019, 2021, 2025]
    ]
    tables = {}
    values = {
        2019: "226245964160",
        2021: "559509431031",
        2025: "404695685526",
    }
    for year, value in values.items():
        uid = f"sab-crown-{year}"
        tables[uid] = {
            "internal_table_uid": uid,
            "document_id": f"SAB_financial_statements_{year}_separate",
            "ticker": "SAB",
            "report_year": year,
            "scope": "separate",
            "table_function": {"kind": "related_party_schedule"},
            "unit_hint": "vnd",
            "headers": [
                "Nhãn dòng",
                f"31/12/{year}VND",
                f"1/1/{year}VND",
            ],
            "context_trace": {
                "source_title": (
                    "Phải trả người bán là các bên liên quan "
                    f"trong báo cáo tài chính riêng năm {year}"
                )
            },
            "rows": [
                ["Nhãn dòng", f"31/12/{year}VND", f"1/1/{year}VND"],
                ["Công ty Liên doanh TNHH Crown Sài Gòn", value, "0"],
            ],
        }

    assert variant._is_context_bound_crown_payable(tables["sab-crown-2021"], 1)
    assert variant._comparison_table_kind(tables["sab-crown-2021"]) == (
        "related party schedule"
    )
    result = variant._resolve_arg_extreme(
        {
            "id": 921,
            "question": (
                "Năm nào SAB công ty mẹ có số dư cuối kỳ phải trả cho "
                "Công ty Liên doanh TNHH Crown Sài Gòn cao nhất trong các năm "
                "2019, 2021 và 2025?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2021")
    assert [selection["value"] for selection in selections] == [
        Decimal("226245964160"),
        Decimal("559509431031"),
        Decimal("404695685526"),
    ]


def test_strict_stb_accrued_interest_alias_is_scope_invariant() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    years = [2017, 2022, 2024]
    metric = "số dư lãi dự thu từ cho vay khách hàng cuối kỳ tính bằng triệu đồng"
    typed = _typed_plan(
        ticker="STB",
        years=years,
        scope=None,
    )
    typed["operands"] = [
        {
            "ticker": "STB",
            "years": [year],
            "metric_hints": [metric],
        }
        for year in years
    ]
    values = {
        "consolidated": {
            2017: "22399323",
            2022: "3370271",
            2024: "3390704",
        },
        "separate": {
            2017: "22369585",
            2022: "3375236",
            2024: "3373306",
        },
    }
    labels = {
        2017: "Lãi từ cho vay khách hàng (i)",
        2022: "Lãi từ cho vay khách hàng (*)",
        2024: "Lãi dự thu từ cho vay khách hàng",
    }
    tables = {}
    for scope, scope_values in values.items():
        for year in years:
            uid = f"stb-accrued-{scope}-{year}"
            tables[uid] = {
                "internal_table_uid": uid,
                "document_id": f"STB_financial_statements_{year}_{scope}",
                "ticker": "STB",
                "report_year": year,
                "scope": scope,
                "table_function": {"kind": "debt_schedule"},
                "unit_hint": None,
                "headers": [
                    "Nhãn dòng",
                    f"31/12/{year} Triệu đồng",
                    f"31/12/{year - 1} Triệu đồng",
                ],
                "context_trace": {
                    "source_title": (
                        "Ngân hàng Thương mại Cổ phần Sài Gòn Thương Tín "
                        f"15.2 Các khoản lãi, phí phải thu năm {year}"
                    )
                },
                "rows": [
                    [
                        "Nhãn dòng",
                        f"31/12/{year} Triệu đồng",
                        f"31/12/{year - 1} Triệu đồng",
                    ],
                    [labels[year], scope_values[year], "0"],
                ],
            }

    assert variant._is_context_bound_customer_loan_accrued_interest(
        tables["stb-accrued-consolidated-2017"], 1
    )
    assert variant._comparison_table_kind(
        tables["stb-accrued-consolidated-2017"]
    ) == "debt schedule"
    assert variant._strict_context_covers_missing_tokens(
        tables["stb-accrued-consolidated-2017"],
        missing_tokens={"du", "thu"},
    )

    result = variant._resolve_arg_extreme(
        {
            "id": 989,
            "question": (
                "Ngân hàng TMCP Sài Gòn Tài Lộc ghi nhận mức số dư lãi dự "
                "thu từ cho vay khách hàng cuối kỳ tính bằng triệu đồng cao "
                "nhất vào năm nào trong các mốc 2017, 2022 và 2024?"
            ),
        },
        typed,
        tables_by_uid=tables,
    )

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2017")
    # The winner is stable in both source scopes; the preferred consolidated
    # cohort is deterministic but does not affect the answer year.
    assert [selection["value"] for selection in selections] == [
        Decimal("22399323"),
        Decimal("3370271"),
        Decimal("3390704"),
    ]


def test_strict_investment_property_factory_alias_replays_carrying_amount() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    years = [2015, 2017, 2019]
    typed = _typed_plan(
        ticker="KBC",
        years=years,
        scope="consolidated",
    )
    typed["operands"] = [
        {
            "ticker": "KBC",
            "years": [year],
            "metric_hints": ["giá trị còn lại"],
        }
        for year in years
    ]
    values = {2015: "20415184100", 2017: "134884233798", 2019: "432718621923"}
    factory_labels = {
        2015: "Nhà xuống (bao gồm chỉ phí phát triển đất và cơ sở hạ tầng)",
        2017: "Nhà xưởng (bao gồm chỉ phí phát triển đất và cơ sở hạ tầng)",
        2019: "Nhà xưởng (bao gồm chi phí phát triển đất và cơ sở hạ tầng)",
    }
    tables = {}
    for year in years:
        value_index = 14 if year != 2017 else 12
        movement_rows = [
            ["Số dư đầu năm", "100"],
            ["- Tăng trong năm", "40"],
        ]
        if year != 2017:
            movement_rows.extend(
                [
                    ["- Giảm trong năm", "(5)"],
                ]
            )
        rows = [
            ["", "Đơn vị tính: VND"],
            ["", factory_labels[year]],
            ["Nguyên giá:", ""],
            *movement_rows,
            ["Số cuối năm", "200"],
            ["Giá trị hao mòn lũy kế:", ""],
            ["Số dư đầu năm", "10"],
            ["- Tăng trong năm", "20"],
        ]
        if year != 2017:
            rows.append(["- Giảm trong năm", "(1)"])
        rows.extend(
            [
            ["Số dư cuối năm", "20"],
            ["Giá trị còn lại:", ""],
            ["Số dư đầu năm", "30"],
            ["Số dư cuối năm", values[year]],
            ]
        )
        assert len(rows) == value_index + 1
        uid = f"kbc-investment-{year}"
        tables[uid] = {
            "internal_table_uid": uid,
            "document_id": f"KBC_financial_statements_{year}_consolidated",
            "ticker": "KBC",
            "report_year": year,
            "scope": "consolidated",
            "table_function": {"kind": "financial_note"},
            "table_purpose": {"kind": "movement_schedule"},
            "unit_hint": "vnd",
            "headers": ["Số dư cuối năm", "Đơn vị tính: VND"],
            "context_before": "12. BẤT ĐỘNG SẢN ĐẦU TƯ",
            "context_trace": {
                "source_title": (
                    "THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT "
                    f"năm {year}. BẤT ĐỘNG SẢN ĐẦU TƯ"
                )
            },
            "rows": rows,
        }

    # The synthetic rows intentionally contain several equally named closing
    # balances.  Only the one below ``Giá trị còn lại`` may activate the alias.
    assert variant._is_context_bound_investment_property_factory(
        tables["kbc-investment-2019"], 14
    )
    assert not variant._is_context_bound_investment_property_factory(
        tables["kbc-investment-2019"], 6
    )
    unrelated = {
        **tables["kbc-investment-2019"],
        "context_before": "13. CHI PHÍ XÂY DỰNG CƠ BẢN DỞ DANG",
        "context_trace": {"source_title": "CHI PHÍ XÂY DỰNG CƠ BẢN DỞ DANG"},
    }
    assert not variant._is_context_bound_investment_property_factory(unrelated, 14)

    item = {
        "id": 878,
        "question": (
            "Năm nào trong giai đoạn 2015, 2017 và 2019 ghi nhận giá trị còn "
            "lại cuối năm của bất động sản đầu tư (nhà xưởng) lớn nhất của KBC?"
        ),
    }
    result = variant._resolve_arg_extreme(item, typed, tables_by_uid=tables)

    assert result is not None
    answer, selections, _query, _tier = result
    assert answer == Decimal("2019")
    assert [selection["value"] for selection in selections] == [
        Decimal("20415184100"),
        Decimal("134884233798"),
        Decimal("432718621923"),
    ]


def test_period_extreme_replays_all_exact_year_cells() -> None:
    variant = _load_variant()
    typed = _typed_plan()
    tables = {
        "aaa-2020": _table(variant, 2020, "10"),
        "aaa-2021": _table(variant, 2021, "20"),
    }
    item = {
        "id": 1,
        "question": "Năm nào AAA có doanh thu thuần cao nhất trong các năm 2020 và 2021?",
        "candidates": [],
    }

    result = variant._resolve_arg_extreme(item, typed, tables_by_uid=tables)

    assert result is not None
    answer, selections, query, tier = result
    assert answer == Decimal("2021")
    assert tier == variant.ARG_EXTREME_TIER
    assert [selection["argmax_year"] for selection in selections] == [2020, 2021]
    assert [selection["value"] for selection in selections] == [Decimal("10"), Decimal("20")]
    assert "period_2021" in query
    assert "max()" in query


def test_period_extreme_rejects_mixed_row_family() -> None:
    variant = _load_variant()
    typed = _typed_plan()
    tables = {
        "aaa-2020": _table(variant, 2020, "10"),
        "aaa-2021": {
            **_table(variant, 2021, "20"),
            "rows": [
                ["Nhãn dòng", "31/12/2021"],
                ["Lợi nhuận sau thuế", "20"],
            ],
        },
    }
    item = {
        "id": 1,
        "question": "Năm nào AAA có doanh thu thuần cao nhất trong các năm 2020 và 2021?",
        "candidates": [],
    }

    assert variant._resolve_arg_extreme(item, typed, tables_by_uid=tables) is None


def test_ratio_and_multi_row_composition_are_not_forced() -> None:
    variant = _load_variant()
    ratio_item = {
        "id": 1,
        "question": "Năm nào AAA có tỷ trọng doanh thu trên tổng doanh thu cao nhất trong các năm 2020 và 2021?",
        "candidates": [],
    }
    composition_item = {
        "id": 1,
        "question": "Năm nào AAA có tổng số dư các khoản phải thu và phải trả với bên liên quan cao nhất trong các năm 2020 và 2021?",
        "candidates": [],
    }

    assert variant._unsupported_reason(ratio_item["question"], ["doanh thu"]) is not None
    assert variant._unsupported_reason(composition_item["question"], ["tong so du"]) is not None


def test_tied_extreme_is_rejected() -> None:
    variant = _load_variant()
    typed = _typed_plan()
    tables = {
        "aaa-2020": _table(variant, 2020, "10"),
        "aaa-2021": _table(variant, 2021, "10"),
    }
    item = {
        "id": 1,
        "question": "Năm nào AAA có doanh thu thuần cao nhất trong các năm 2020 và 2021?",
        "candidates": [],
    }

    assert variant._resolve_arg_extreme(item, typed, tables_by_uid=tables) is None


def _strict_selection(variant, table, *, year: int):
    return {
        "internal_table_uid": table["internal_table_uid"],
        "report_year": year,
        "row_index": 1,
        "column_index": 1,
    }


def test_strict_source_contract_accepts_note_detail_reconstruction_with_same_unit() -> None:
    variant = _load_variant()
    assert "thuan" in variant._required_row_tokens(
        "doanh thu thuan ban hang va cung cap dich vu",
        strict=True,
    )
    assert {"ben", "lien", "quan"}.issubset(
        variant._required_row_tokens(
            "tong doanh thu cung cap dich vu cho ben lien quan",
            strict=True,
        )
    )
    first = {
        **_table(variant, 2020, "10", uid="aaa-2020"),
        "table_function": {"kind": "financial_note"},
        "unit_hint": "vnd",
        "headers": ["Nhãn dòng", "Năm nay VND"],
    }
    second = {
        **_table(variant, 2021, "20", uid="aaa-2021"),
        "table_function": {"kind": "financial_note_detail"},
        "unit_hint": "vnd",
        "headers": ["Nhãn dòng", "Năm nay VND"],
    }
    item = {"id": 1, "question": "Năm nào AAA có doanh thu thuần cao nhất?"}

    assert variant._strict_source_contract_reason(
        item=item,
        years=[2020, 2021],
        selections=[
            _strict_selection(variant, first, year=2020),
            _strict_selection(variant, second, year=2021),
        ],
        tables_by_uid={"aaa-2020": first, "aaa-2021": second},
    ) is None


def test_strict_source_contract_accepts_context_bound_cash_flow_schedule() -> None:
    variant = _load_variant()
    schedule = {
        **_table(variant, 2021, "-20", uid="aaa-tax-2021"),
        "table_function": {"kind": "financial_data_schedule"},
        "unit_hint": "vnd",
        "context_trace": {
            "source_title": "Báo cáo lưu chuyển tiền tệ từ hoạt động kinh doanh"
        },
        "rows": [
            ["Nhãn dòng", "2021 VND"],
            ["Thuế thu nhập doanh nghiệp đã nộp", "-20"],
        ],
    }

    assert variant._comparison_table_kind(schedule) == "cash flow statement"

    generic_schedule = {
        **schedule,
        "context_trace": {"source_title": "Bảng dữ liệu tài chính"},
    }
    assert variant._comparison_table_kind(generic_schedule) == "financial data schedule"


def test_strict_source_contract_normalizes_context_bound_provision_expense_detail() -> None:
    variant = _load_variant()
    reconstructed_detail = {
        **_table(variant, 2025, "20", uid="aaa-provision-2025"),
        "table_function": {"kind": "financial_note_detail"},
        "context_trace": {
            "source_title": "32. Chi phí dự phòng rủi ro tín dụng"
        },
        "rows": [
            [
                "Trích lập dự phòng cụ thể cho vay khách hàng(Thuyết minh 9)",
                "20",
            ],
        ],
    }
    unrelated_detail = {
        **reconstructed_detail,
        "context_trace": {"source_title": "Thuyết minh tài sản cố định"},
    }

    assert variant._comparison_table_kind(reconstructed_detail) == "debt schedule"
    assert variant._comparison_table_kind(unrelated_detail) == "financial note"


def test_strict_source_contract_normalizes_context_bound_related_party_revenue() -> None:
    variant = _load_variant()
    first = {
        **_table(variant, 2020, "10", uid="ssh-2020"),
        "document_id": "SSH_financial_statements_2020_separate",
        "ticker": "SSH",
        "table_function": {"kind": "financial_note"},
        "unit_hint": "vnd",
        "headers": ["Nhãn dòng", "Năm nay VND"],
        "context_trace": {
            "source_title": (
                "Trong năm, Công ty đã có các giao dịch chủ yếu sau với bên liên quan"
            )
        },
        "rows": [
            ["Nhãn dòng", "Năm nay"],
            ["Doanh thu cung cấp dịch vụ", "10"],
        ],
    }
    second = {
        **first,
        "internal_table_uid": "ssh-2021",
        "document_id": "SSH_financial_statements_2021_separate",
        "report_year": 2021,
        "table_function": {"kind": "related_party_schedule"},
        "rows": [
            ["Nhãn dòng", "Năm nay"],
            ["Doanh thu cung cấp dịch vụ (Thuyết minh số 26)", "20"],
        ],
    }

    assert variant._comparison_table_kind(first) == "related party schedule"
    assert variant._comparison_table_kind(second) == "related party schedule"
    assert variant._strict_source_contract_reason(
        item={"id": 1, "question": "Năm nào SSH có doanh thu cung cấp dịch vụ cho bên liên quan cao nhất?"},
        years=[2020, 2021],
        selections=[
            _strict_selection(variant, first, year=2020),
            _strict_selection(variant, second, year=2021),
        ],
        tables_by_uid={"ssh-2020": first, "ssh-2021": second},
    ) is None


def test_related_party_context_normalization_does_not_admit_generic_schedule() -> None:
    variant = _load_variant()
    generic = {
        **_table(variant, 2020, "10", uid="ssh-generic"),
        "document_id": "SSH_financial_statements_2020_separate",
        "ticker": "SSH",
        "table_function": {"kind": "financial_data_schedule"},
        "unit_hint": "vnd",
        "context_trace": {
            "source_title": "Bảng dữ liệu tài chính có doanh thu cung cấp dịch vụ"
        },
        "rows": [
            ["Nhãn dòng", "Năm nay"],
            ["Doanh thu cung cấp dịch vụ", "10"],
        ],
    }

    assert variant._comparison_table_kind(generic) == "financial data schedule"


def test_related_party_transaction_total_sums_continuations_and_rejects_duplicate_total() -> None:
    variant = _load_variant()
    variant._STRICT_SOURCE_CONTRACT = True
    typed = _typed_plan(
        ticker="AAA",
        years=[2020, 2021],
        scope="separate",
    )
    typed["operands"] = [
        {
            "ticker": "AAA",
            "years": [year],
            "metric_hints": ["tổng giá trị giao dịch với bên liên quan"],
        }
        for year in [2020, 2021]
    ]

    def transaction_table(
        *,
        year: int,
        ordinal: int,
        uid: str,
        values: list[tuple[str, str]],
        anchor: bool = False,
        raw_kind: str = "financial_note",
    ):
        return {
            "internal_table_uid": uid,
            "document_id": f"AAA_financial_statements_{year}_separate",
            "ticker": "AAA",
            "report_year": year,
            "scope": "separate",
            "local_ordinal": ordinal,
            "table_function": {"kind": raw_kind},
            "unit_hint": "vnd",
            "headers": ["Nhãn dòng", f"Giá trị giao dịch · {year} VND"],
            "header_row_indices": [0],
            "context_before": (
                "Các giao dịch chủ yếu với các bên liên quan"
                if anchor
                else "Bảng nối tiếp"
            ),
            "rows": [
                ["Nhãn dòng", f"Giá trị giao dịch · {year} VND"],
                *[[label, value] for label, value in values],
            ],
        }

    tables = {
        "aaa-2020-1": transaction_table(
            year=2020,
            ordinal=1,
            uid="aaa-2020-1",
            values=[("Mua hàng hóa", "10"), ("Tổng cộng", "999")],
            anchor=True,
            raw_kind="related_party_schedule",
        ),
        "aaa-2020-2": transaction_table(
            year=2020,
            ordinal=2,
            uid="aaa-2020-2",
            values=[("Bán hàng hóa", "20")],
        ),
        # A different section with the same document/scope must terminate
        # the continuation group instead of being swept into the total.
        "aaa-2020-unrelated": {
            **transaction_table(
                year=2020,
                ordinal=3,
                uid="aaa-2020-unrelated",
                values=[("Không thuộc disclosure", "1000")],
            ),
            "headers": ["Nhãn dòng", "31/12/2020 VND"],
            "context_before": "Thuyết minh tài sản khác",
        },
        "aaa-2021": transaction_table(
            year=2021,
            ordinal=1,
            uid="aaa-2021",
            values=[("Mua hàng hóa", "25")],
            anchor=True,
        ),
    }
    item = {
        "id": 9999,
        "question": (
            "Trong các năm 2020 và 2021, năm nào ghi nhận tổng giá trị "
            "giao dịch với bên liên quan cao nhất cho AAA?"
        ),
    }

    group = variant._related_party_transaction_group(
        [tables["aaa-2020-1"], tables["aaa-2020-2"], tables["aaa-2020-unrelated"]],
        year=2020,
        scope="separate",
    )
    assert [table["internal_table_uid"] for table in group] == [
        "aaa-2020-1",
        "aaa-2020-2",
    ]

    result = variant._resolve_arg_extreme(item, typed, tables_by_uid=tables)
    assert result is not None
    answer, evidence, query, tier = result
    assert answer == Decimal("2020")
    assert tier == variant.RELATED_PARTY_TRANSACTION_TOTAL_TIER
    assert [row["value"] for row in evidence[:2]] == [Decimal("30"), Decimal("25")]
    assert len([row for row in evidence if row.get("related_party_transaction_component")]) == 3
    assert "operand_role.isin" in query


def test_strict_source_contract_rejects_schedule_and_mixed_multiplier() -> None:
    variant = _load_variant()
    first = {
        **_table(variant, 2020, "10", uid="aaa-2020"),
        "table_function": {"kind": "balance_sheet"},
        "unit_hint": "vnd",
        "headers": ["Nhãn dòng", "31/12/2020 VND"],
    }
    schedule = {
        **_table(variant, 2021, "20", uid="aaa-2021"),
        "table_function": {"kind": "financial_data_schedule"},
        "unit_hint": "vnd",
        "headers": ["Nhãn dòng", "31/12/2021 VND"],
    }
    item = {"id": 1, "question": "Năm nào AAA có doanh thu thuần cao nhất?"}

    reason = variant._strict_source_contract_reason(
        item=item,
        years=[2020, 2021],
        selections=[
            _strict_selection(variant, first, year=2020),
            _strict_selection(variant, schedule, year=2021),
        ],
        tables_by_uid={"aaa-2020": first, "aaa-2021": schedule},
    )
    assert reason == "UNSAFE_TABLE_KIND"

    second = {
        **_table(variant, 2021, "20", uid="aaa-2021"),
        "table_function": {"kind": "balance_sheet"},
        "unit_hint": "million_vnd",
        "headers": ["Nhãn dòng", "31/12/2021 Triệu VND"],
    }
    reason = variant._strict_source_contract_reason(
        item=item,
        years=[2020, 2021],
        selections=[
            _strict_selection(variant, first, year=2020),
            _strict_selection(variant, second, year=2021),
        ],
        tables_by_uid={"aaa-2020": first, "aaa-2021": second},
    )
    assert reason == "MIXED_SOURCE_UNIT"
