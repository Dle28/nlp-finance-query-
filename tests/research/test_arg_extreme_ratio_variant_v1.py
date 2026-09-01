from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VARIANT_PATH = ROOT / "scripts/research/run_arg_extreme_ratio_variant_v1.py"


def _load_variant():
    spec = importlib.util.spec_from_file_location(
        "arg_extreme_ratio_variant_v1_test", VARIANT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _typed(ticker: str, years: list[int], scope: str = "consolidated") -> dict:
    return {
        "question_id": 1,
        "decomposition_status": "typed_non_executable",
        "route": "simple_period_extremum",
        "entities": [ticker],
        "years": years,
        "scope": scope,
        "operation_ast": {
            "op": "arg_extreme_period",
            "direction": "max",
            "args": [f"x{index}" for index in range(len(years))],
        },
    }


def _base_table(
    *,
    uid: str,
    ticker: str,
    year: int,
    kind: str,
    rows: list[list[str]],
    headers: list[str],
    scope: str = "consolidated",
    source_title: str = "Đơn vị tính: VND",
) -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": f"{ticker}_financial_statements_{year}_{scope}",
        "ticker": ticker,
        "report_year": year,
        "scope": scope,
        "table_function": {"kind": kind},
        "headers": headers,
        "context_trace": {"source_title": source_title},
        "rows": rows,
    }


def test_family_detection_is_metric_based_not_question_id_based() -> None:
    variant = _load_variant()

    assert variant._family_for_question(
        "Năm nào có tỷ trọng giá vốn cho thuê dài hạn đất và cơ sở hạ tầng "
        "trên tổng giá vốn hàng bán và dịch vụ cung cấp cao nhất?"
    ) == "lease_land_cost_share"
    assert variant._family_for_question(
        "Năm nào có tỷ trọng chi phí lãi tiền gửi trong tổng chi phí lãi cao nhất?"
    ) == "deposit_interest_expense_share"
    assert variant._family_for_question(
        "Năm nào tỷ trọng tài sản bộ phận dịch vụ vận tải so với tổng tài sản cao nhất?"
    ) == "transport_segment_asset_share"
    assert variant._family_for_question(
        "Năm nào có tỷ trọng chi phí khấu hao và phân bổ trong tổng chi phí "
        "sản xuất và kinh doanh theo yếu tố cao nhất?"
    ) == "production_factor_depreciation_share"
    assert variant._family_for_question(
        "Năm nào có tỷ trọng chứng chỉ tiền gửi dưới 12 tháng cao nhất?"
    ) == "certificate_deposit_short_maturity_share"
    assert variant._family_for_question(
        "Năm nào có tỷ trọng vay bằng USD trong tổng khoản vay dài hạn cao nhất?"
    ) == "usd_long_term_loan_share"
    assert variant._family_for_question(
        "Năm nào có tỷ trọng trạng thái tiền tệ nội bảng trên tổng tài sản cao nhất?"
    ) == "net_on_balance_currency_asset_share"
    assert variant._family_for_question(
        "Năm nào có tỷ trọng chi phí khấu hao tài sản cố định trong tổng chi phí "
        "quản lý doanh nghiệp cao nhất?"
    ) == "management_depreciation_share"


def test_complete_ratio_ast_enters_guarded_executor() -> None:
    variant = _load_variant()
    typed = _typed("SAB", [2018, 2020], scope="")
    typed["operation_ast"] = {"op": "max", "args": ["x0", "x1"]}
    variant._TYPED_PLANS = {1: typed}
    expected = (Decimal("2020"), [], "ratio-query", "program_arg_extreme_ratio_period_v1")
    calls: list[dict] = []

    def fake_resolve(item, plan, *, tables_by_uid):
        calls.append({"item": item, "plan": plan, "tables_by_uid": tables_by_uid})
        return expected

    variant._resolve_ratio = fake_resolve
    variant._ORIGINAL_PROGRAM_AWARE = lambda item, *, tables_by_uid=None: None
    result = variant._patched_program_aware(
        {
            "id": 1,
            "question": (
                "Năm nào có tỷ trọng chi phí khấu hao và phân bổ trong tổng chi phí "
                "sản xuất và kinh doanh theo yếu tố cao nhất?"
            ),
        },
        tables_by_uid={},
    )

    assert result == expected
    assert len(calls) == 1


def test_current_column_uses_year_or_nam_nay_anchor() -> None:
    variant = _load_variant()
    hdb = _base_table(
        uid="hdb-2023",
        ticker="HDB",
        year=2023,
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Cột nguồn 2", "Cột nguồn 3"],
        rows=[
            ["", "2023Triệu đồng", "2022Triệu đồng"],
            ["Chi phí lãi tiền gửi", "10", "9"],
        ],
        source_title="21. CHI PHÍ LÃI VÀ CÁC KHOẢN CHI PHÍ TƯƠNG TỰ",
    )
    kbc = _base_table(
        uid="kbc-2016",
        ticker="KBC",
        year=2016,
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Đơn vị tính: VND · Năm nay", "Năm trước"],
        rows=[["Giá vốn", "10", "9"]],
        source_title="23. GIÁ VỐN HÀNG BÁN VÀ DỊCH VỤ CUNG CẤP",
    )

    assert variant._current_column(hdb, 2023) == 1
    assert variant._current_column(kbc, 2016) == 1


def test_unqualified_question_keeps_canonical_consolidated_scope() -> None:
    variant = _load_variant()
    typed = _typed("KBC", [2016, 2019], scope="")
    item = {
        "question": "Năm nào có tỷ trọng giá vốn cho thuê trên tổng giá vốn cao nhất?",
        "candidates": [{"scope": "separate", "review_score": 99.0}],
    }

    assert variant._scope_candidates(item, typed)[0] == "consolidated"


def test_kbc_ratio_argmax_requires_note_and_statement_code_11() -> None:
    variant = _load_variant()
    tables = {}
    for year, numerator, denominator in (
        (2016, "90", "-100"),
        (2019, "70", "-100"),
    ):
        note_uid = f"kbc-note-{year}"
        statement_uid = f"kbc-statement-{year}"
        tables[note_uid] = _base_table(
            uid=note_uid,
            ticker="KBC",
            year=year,
            kind="financial_note_detail",
            headers=["Nhãn dòng", "Đơn vị tính: VND · Năm nay"],
            rows=[
                [
                    "Giá vốn cho thuê dài hạn đất và cơ sở hạ tầng",
                    numerator,
                ]
            ],
            source_title="23. GIÁ VỐN HÀNG BÁN VÀ DỊCH VỤ CUNG CẤP",
        )
        tables[statement_uid] = _base_table(
            uid=statement_uid,
            ticker="KBC",
            year=year,
            kind="income_statement",
            headers=["Mã số", "Chỉ tiêu", "Thuyết minh", "Năm nay"],
            rows=[["11", "4. Giá vốn hàng bán và dịch vụ cung cấp", "23", denominator]],
        )

    result = variant._resolve_ratio(
        {
            "id": 999,
            "question": (
                "Năm nào KBC có tỷ trọng giá vốn cho thuê dài hạn đất và cơ sở "
                "hạ tầng trên tổng giá vốn hàng bán và dịch vụ cung cấp cao nhất?"
            ),
        },
        _typed("KBC", [2016, 2019]),
        tables_by_uid=tables,
    )

    assert result is not None
    answer, sources, _query, tier = result
    assert answer == Decimal("2016")
    assert tier == "program_arg_extreme_ratio_period_v1"
    assert [source["role"] for source in sources] == [
        "period_2016_numerator",
        "period_2016_denominator",
        "period_2019_numerator",
        "period_2019_denominator",
    ]


def test_hdb_ratio_argmax_requires_total_to_equal_interest_detail_sum() -> None:
    variant = _load_variant()
    tables = {}
    for year, target, total in (
        (2023, "80", "100"),
        (2024, "70", "90"),
    ):
        tables[f"hdb-{year}"] = _base_table(
            uid=f"hdb-{year}",
            ticker="HDB",
            year=year,
            kind="financial_note_detail",
            headers=["Nhãn dòng", "Cột nguồn 2", "Cột nguồn 3"],
            rows=[
                ["", f"{year}Triệu đồng", f"{year - 1}Triệu đồng"],
                ["Chi phí lãi tiền gửi", target, "1"],
                ["Chi phí lãi phát hành giấy tờ có giá", "10", "1"],
                ["Chi phí lãi tiền vay", "5", "1"],
                ["Chi phí hoạt động tín dụng khác", "5", "1"],
                ["", total, "4"],
            ],
            source_title="21. CHI PHÍ LÃI VÀ CÁC KHOẢN CHI PHÍ TƯƠNG TỰ",
        )

    result = variant._resolve_ratio(
        {
            "id": 1000,
            "question": (
                "Năm nào có tỷ trọng chi phí lãi tiền gửi trong tổng chi phí lãi "
                "cao nhất?"
            ),
        },
        _typed("HDB", [2023, 2024]),
        tables_by_uid=tables,
    )
    assert result is not None
    assert result[0] == Decimal("2023")

    tables["hdb-2024"]["rows"][-1][1] = "101"
    rejected = variant._resolve_ratio(
        {
            "id": 1001,
            "question": (
                "Năm nào có tỷ trọng chi phí lãi tiền gửi trong tổng chi phí lãi "
                "cao nhất?"
            ),
        },
        _typed("HDB", [2023, 2024]),
        tables_by_uid=tables,
    )
    assert rejected is None


def test_ratio_tie_is_rejected() -> None:
    variant = _load_variant()
    tables = {}
    for year in (2023, 2024):
        tables[f"hdb-{year}"] = _base_table(
            uid=f"hdb-{year}",
            ticker="HDB",
            year=year,
            kind="financial_note_detail",
            headers=["Nhãn dòng", "Cột nguồn 2", "Cột nguồn 3"],
            rows=[
                ["", f"{year}Triệu đồng", f"{year - 1}Triệu đồng"],
                ["Chi phí lãi tiền gửi", "80", "1"],
                ["Chi phí lãi phát hành giấy tờ có giá", "10", "1"],
                ["Chi phí lãi tiền vay", "5", "1"],
                ["Chi phí hoạt động tín dụng khác", "5", "1"],
                ["", "100", "4"],
            ],
            source_title="21. CHI PHÍ LÃI VÀ CÁC KHOẢN CHI PHÍ TƯƠNG TỰ",
        )

    result = variant._resolve_ratio(
        {
            "id": 1002,
            "question": (
                "Năm nào có tỷ trọng chi phí lãi tiền gửi trong tổng chi phí lãi "
                "cao nhất?"
            ),
        },
        _typed("HDB", [2023, 2024]),
        tables_by_uid=tables,
    )
    assert result is None
