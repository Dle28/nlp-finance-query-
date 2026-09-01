from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path


def _builder_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/e2e/build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location("competition_submission_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve(builder, item, tables):
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    return builder.resolve_source_first_direct_lookup(
        item,
        tables_by_pair=by_pair,
        parse_decimal=builder.parse_decimal,
        candidate_evidence_window=builder.candidate_evidence_window,
        choose_year_column=builder.choose_year_column,
        source_multiplier=builder.source_multiplier,
        requested_divisor=builder.requested_divisor,
    )


def _resolve_with_neighbor_fallback(builder, item, tables):
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    return builder.resolve_source_first_direct_lookup(
        item,
        tables_by_pair=by_pair,
        parse_decimal=builder.parse_decimal,
        candidate_evidence_window=builder.candidate_evidence_window,
        choose_year_column=builder.choose_year_column,
        source_multiplier=builder.source_multiplier,
        requested_divisor=builder.requested_divisor,
        report_year_neighbor_fallback=1,
    )


def _feedback_reason(
    builder,
    item,
    table,
    *,
    row_index,
    row_text,
    column_index,
    target_text,
    qualifier_phrases=(),
):
    return builder.source_first_lookup_module._source_first_feedback_reason(
        item,
        table,
        row_index=row_index,
        row_text=row_text,
        column_index=column_index,
        target_text=target_text,
        parse_decimal=builder.parse_decimal,
        qualifier_phrases=qualifier_phrases,
    )


def _table(*, uid, ticker, year, scope, kind, headers, rows, source_title=""):
    return {
        "internal_table_uid": uid,
        "ticker": ticker,
        "report_year": year,
        "scope": scope,
        "headers": deepcopy(headers),
        "rows": deepcopy(rows),
        "header_row_indices": [0],
        "table_function": {"kind": kind, "label": kind},
        "context_trace": {"source_title": source_title},
        "unit_hint": None,
        "document_id": f"{ticker}_financial_statements_{year}_{scope}",
    }


def test_source_first_prefers_balance_sheet_over_eps_copy():
    builder = _builder_module()
    item = {
        "question": "Quỹ khen thưởng, phúc lợi của HT1 cuối năm 2019 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": [],
            "years": [2019],
            "operands": [{"metric": "Quỹ khen thưởng, phúc lợi HT1 cuối năm"}],
        },
    }
    tables = [
        _table(
            uid="eps",
            ticker="HT1",
            year=2019,
            scope="consolidated",
            kind="financial_note_detail",
            headers=["", "Năm nay", "Năm trước"],
            rows=[
                ["", "Năm nay", "Năm trước"],
                ["Trừ: Quỹ khen thưởng, phúc lợi (VND) (*)", "-", "(87.540.000.000)"],
            ],
            source_title="30. Lãi trên cổ phiếu",
        ),
        _table(
            uid="balance",
            ticker="HT1",
            year=2019,
            scope="consolidated",
            kind="balance_sheet",
            headers=["Mã số", "NGUỒN VỐN", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
            rows=[
                ["Mã số", "NGUỒN VỐN", "Thuyết minh", "Số cuối năm", "Số đầu năm"],
                ["322", "9. Quỹ khen thưởng, phúc lợi", "19", "57.764.463.052", "36.234.906.990"],
            ],
            source_title="Bảng cân đối kế toán",
        ),
    ]
    result = _resolve(builder, item, tables)
    assert result is not None
    assert result["answer"] == builder.Decimal("57.764463052")
    assert result["selection"]["source_first_table_kind"] == "balance_sheet"


def test_source_first_handles_ocr_metric_and_unknown_standalone_scope():
    builder = _builder_module()
    item = {
        "question": "Chi phí lương và các khoản khác theo lương của công ty mẹ CTCP Chứng khoán FPT trong năm 2021 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["FTS", "FPT"],
            "years": [2021],
            "scope": "separate",
            "operands": [{"metric": "Chi phí lương và các khoản khác theo lương CTCP Chứng khoán FPT"}],
        },
    }
    table = _table(
        uid="fts",
        ticker="FTS",
        year=2021,
        scope="unknown",
        kind="financial_note_detail",
        headers=["STT", "Loại chi phí quản lý CTCK", "Năm nay", "Năm trước"],
        rows=[
            ["STT", "Loại chi phí quản lý CTCK", "Năm nay", "Năm trước"],
            ["1", "Chi phí lương và khác khoản khác theo lương", "30.686.828.047", "26.189.738.031"],
        ],
        source_title="7.36. Chi phí quản lý CTCK",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("30.686828047")
    assert result["selection"]["source_first_match_mode"] in {
        "ordered_with_ocr_gap",
        "fuzzy_ocr_label",
    }


def test_source_first_prefers_strict_total_over_relaxed_child_row():
    builder = _builder_module()
    item = {
        "question": "Tổng chi phí hoạt động của công ty mẹ CTG năm 2019 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["CTG"],
            "years": [2019],
            "scope": "separate",
            "operands": [{"metric": "Tổng chi phí hoạt động CTG"}],
        },
    }
    table = _table(
        uid="ctg-income",
        ticker="CTG",
        year=2019,
        scope="separate",
        kind="income_statement",
        headers=["Nhãn dòng", "2019 Triệu đồng", "2018 Triệu đồng"],
        rows=[
            ["Nhãn dòng", "2019 Triệu đồng", "2018 Triệu đồng"],
            ["Chi phí hoạt động dịch vụ", "-2.362.494", "-2.000.000"],
            ["TỔNG CHI PHÍ HOẠT ĐỘNG", "-14.733.282", "-13.000.000"],
        ],
        source_title="Báo cáo kết quả hoạt động kinh doanh",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("-14733282")
    assert result["selection"]["row_label"] == "TỔNG CHI PHÍ HOẠT ĐỘNG"


def test_source_first_prefers_complete_label_over_semantic_tail():
    builder = _builder_module()
    item = {
        "question": "Giá vốn hàng hóa của DIG trong năm 2023 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["DIG"],
            "years": [2023],
            "operands": [{"metric": "Giá vốn hàng hóa DIG"}],
        },
    }
    tables = [
        _table(
            uid="dig-short",
            ticker="DIG",
            year=2023,
            scope="consolidated",
            kind="financial_note_detail",
            headers=["Nhãn dòng", "2023 VND"],
            rows=[
                ["Nhãn dòng", "2023 VND"],
                ["Giá vốn hàng hóa", "37.015.620.950"],
            ],
            source_title="Chi phí kinh doanh",
        ),
        _table(
            uid="dig-compound",
            ticker="DIG",
            year=2023,
            scope="consolidated",
            kind="financial_note_detail",
            headers=["Nhãn dòng", "2023 VND"],
            rows=[
                ["Nhãn dòng", "2023 VND"],
                ["Giá vốn hàng hóa và thành phẩm", "184.970.538.922"],
            ],
            source_title="Chi phí kinh doanh",
        ),
    ]
    result = _resolve(builder, item, tables)
    assert result is not None
    assert result["answer"] == builder.Decimal("37.01562095")
    assert result["selection"]["row_label"] == "Giá vốn hàng hóa"


def test_source_first_does_not_use_retrieval_rank_for_conflicting_values():
    builder = _builder_module()
    item = {
        "question": "Tổng tài sản của MBB đến ngày 31/12/2020 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["MBB"],
            "years": [2020],
            "scope": "separate",
            "operands": [{"metric": "Tổng tài sản MBB"}],
        },
        "candidates": [
            {"internal_table_uid": "mbb-a", "rank": 1},
            {"internal_table_uid": "mbb-b", "rank": 2},
        ],
    }
    common = {
        "ticker": "MBB",
        "year": 2020,
        "scope": "separate",
        "kind": "balance_sheet",
        "headers": ["Nhãn dòng", "2020 Triệu đồng"],
        "source_title": "Bảng cân đối kế toán",
    }
    tables = [
        _table(
            uid="mbb-a",
            rows=[["Nhãn dòng", "2020 Triệu đồng"], ["TỔNG TÀI SẢN", "400.000"]],
            **common,
        ),
        _table(
            uid="mbb-b",
            rows=[["Nhãn dòng", "2020 Triệu đồng"], ["TỔNG TÀI SẢN", "450.000"]],
            **common,
        ),
    ]
    assert _resolve(builder, item, tables) is None


def test_source_first_fails_closed_on_conflicting_unqualified_scopes():
    builder = _builder_module()
    item = {
        "question": "Số dư phải thu ngắn hạn khách hàng của VPI cuối năm 2024 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["VPI"],
            "years": [2024],
            "operands": [{"metric": "Số dư phải thu ngắn hạn khách hàng VPI cuối năm"}],
        },
    }
    common = {
        "ticker": "VPI",
        "year": 2024,
        "kind": "balance_sheet",
        "headers": ["Nhãn dòng", "Số cuối năm", "Số đầu năm"],
        "rows": [
            ["Nhãn dòng", "Số cuối năm", "Số đầu năm"],
            ["Phải thu ngắn hạn khách hàng", "179.433.940.407", "151.192.256.925"],
        ],
        "source_title": "Bảng cân đối kế toán",
    }
    tables = [
        _table(uid="vpi-c", scope="consolidated", **common),
        _table(uid="vpi-s", scope="separate", **common),
    ]
    tables[0]["rows"][1][1] = "179.433.940.407"
    tables[0]["rows"][1][2] = "151.192.256.925"
    tables[1]["rows"][1][1] = "151.192.256.925"
    tables[1]["rows"][1][2] = "140.000.000.000"
    result = _resolve(builder, item, tables)
    assert result is None


def test_reclassified_receivables_total_uses_gross_closing_value_column():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng cộng các khoản phải thu của công ty mẹ Công ty cổ phần "
            "chứng khoán SSI vào cuối năm 2016 là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["SSI"],
            "years": [2016],
            "scope": "separate",
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    table = _table(
        uid="ssi-receivables-doubtful-total-2016",
        ticker="SSI",
        year=2016,
        scope="separate",
        kind="financial_note",
        headers=[
            "Nhãn dòng",
            "Giá trịphải thu khó đòicuối nămVND",
            "Số dự phòngđầu nămVND",
            "Số trích lậptrong nămVND",
            "Số hoàn nhậptrong nămVND",
            "Số dự phòngcuối nămVND",
            "Giá trịphải thu khó đòiđầu nămVND",
        ],
        rows=[
            [
                "Nhãn dòng",
                "Giá trịphải thu khó đòicuối nămVND",
                "Số dự phòngđầu nămVND",
                "Số trích lậptrong nămVND",
                "Số hoàn nhậptrong nămVND",
                "Số dự phòngcuối nămVND",
                "Giá trịphải thu khó đòiđầu nămVND",
            ],
            [
                "Phải thu các dịch vụ công ty chứng khoán cung cấp khó đòi",
                "12.971.609.076",
                "1.161.000.000",
                "11.708.127.607",
                "-",
                "12.869.127.607",
                "1.206.000.000",
            ],
            [
                "Tổng cộng",
                "16.024.974.123",
                "3.431.111.960",
                "12.491.380.694",
                "-",
                "15.922.492.654",
                "4.259.365.047",
            ],
        ],
        source_title=(
            "Công ty Cổ phần Chứng khoán Sài Gòn B09-CTCK "
            "THUYẾT MINH BÁO CÁO TÀI CHÍNH RIÊNG tại ngày 31 tháng 12 năm 2016 "
            "9. CÁC KHOẢN PHẢI THU (tiếp theo) Chi tiết dự phòng suy giảm giá trị "
            "các khoản phải thu"
        ),
    )

    distractor = _table(
        uid="ssi-receivables-unrelated-income-2016",
        ticker="SSI",
        year=2016,
        scope="separate",
        kind="income_statement",
        headers=["Nhãn dòng", "2016 VND"],
        rows=[
            ["Nhãn dòng", "2016 VND"],
            ["3. Lãi từ các khoản cho vay và phải thu", "429.400.211.341"],
        ],
        source_title="Báo cáo kết quả hoạt động kinh doanh riêng",
    )

    result = builder.source_first_reclassified_direct_answer(
        item,
        tables_by_pair={("SSI", 2016): [table, distractor]},
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_reclassified_direct_v1"
    assert result["answer"] == builder.Decimal("16.024974123")
    assert result["selection"]["source_first_match_mode"] == (
        "contextual_financial_receivables_total"
    )
    assert result["selection"]["period_selection_mode"] == (
        "contextual_receivables_total_column"
    )
    assert result["selection"]["row_index"] == 2
    assert result["selection"]["column_index"] == 1
    assert result["selection"]["promotion_allowed"] is False


def test_reclassified_receivables_total_rejects_non_note_or_ambiguous_column():
    builder = _builder_module()
    item = {
        "question": "Tổng cộng các khoản phải thu của SSI cuối năm 2016 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["SSI"],
            "years": [2016],
            "scope": "separate",
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    balance_sheet = _table(
        uid="ssi-receivables-balance-sheet-2016",
        ticker="SSI",
        year=2016,
        scope="separate",
        kind="balance_sheet",
        headers=["Mã số", "CHỈ TIÊU", "Thuyết minh", "Số cuối năm VND"],
        rows=[
            ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Số cuối năm VND"],
            ["117", "7. Các khoản phải thu", "", "53.619.347.570"],
        ],
        source_title="Báo cáo tình hình tài chính riêng tại ngày 31 tháng 12 năm 2016",
    )
    ambiguous = _table(
        uid="ssi-receivables-ambiguous-2016",
        ticker="SSI",
        year=2016,
        scope="separate",
        kind="financial_note",
        headers=[
            "Nhãn dòng",
            "Giá trị phải thu khó đòi cuối năm VND",
            "Giá trị phải thu khác cuối năm VND",
        ],
        rows=[
            [
                "Nhãn dòng",
                "Giá trị phải thu khó đòi cuối năm VND",
                "Giá trị phải thu khác cuối năm VND",
            ],
            ["Tổng cộng", "16.024.974.123", "99.000.000"],
        ],
        source_title=(
            "9. CÁC KHOẢN PHẢI THU Chi tiết dự phòng suy giảm giá trị "
            "các khoản phải thu tại ngày 31 tháng 12 năm 2016"
        ),
    )

    assert builder._financial_receivables_total_table_contract(
        item,
        balance_sheet,
    ) is False
    assert builder._financial_receivables_total_table_contract(
        item,
        ambiguous,
    ) is True
    result = builder.source_first_reclassified_direct_answer(
        item,
        tables_by_pair={("SSI", 2016): [ambiguous]},
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )
    assert result is None


def test_source_first_checks_scope_conflict_before_preferred_table_function():
    """A balance-sheet preference must not hide a conflicting separate copy."""

    builder = _builder_module()
    item = {
        "question": "Tổng tài sản của MBB đến ngày 31/12/2020 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["MBB"],
            "years": [2020],
            "operands": [{"metric": "Tổng tài sản MBB"}],
        },
    }
    common = {
        "ticker": "MBB",
        "year": 2020,
        "headers": ["Nhãn dòng", "2020 Triệu đồng"],
        "rows": [
            ["Nhãn dòng", "2020 Triệu đồng"],
            ["TỔNG TÀI SẢN", "400.000"],
        ],
        "source_title": "Bảng cân đối kế toán",
    }
    tables = [
        _table(
            uid="mbb-consolidated",
            scope="consolidated",
            kind="balance_sheet",
            **common,
        ),
        _table(
            uid="mbb-separate",
            scope="separate",
            kind="financial_note_detail",
            rows=[
                ["Nhãn dòng", "2020 Triệu đồng"],
                ["TỔNG TÀI SẢN", "450.000"],
            ],
            **{key: value for key, value in common.items() if key != "rows"},
        ),
    ]
    assert _resolve(builder, item, tables) is None


def test_candidate_bound_accepts_exact_row_when_global_scope_is_ambiguous():
    builder = _builder_module()
    item = {
        "question": "Số dư vay ngắn hạn của TTF cuối năm 2020 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["TTF"],
            "years": [2020],
            "scope": None,
            "operands": [{"metric": "Số dư vay ngắn hạn TTF cuối năm"}],
        },
        "candidates": [
            {
                "internal_table_uid": "ttf-note",
                "document_id": "TTF_financial_statements_2020_consolidated",
                "scope": "consolidated",
                "rank": 1,
                "review_score": 0.80,
            }
        ],
    }
    table = _table(
        uid="ttf-note",
        ticker="TTF",
        year=2020,
        scope="consolidated",
        kind="financial_note",
        headers=["Nhãn dòng", "Vay ngắn hạn", "Vay dài hạn"],
        rows=[
            ["", "Vay ngắn hạn", "Vay dài hạn"],
            ["Số cuối năm", "507.238.147.131", "-"],
        ],
        source_title="24. Vay",
    )
    by_pair = {("TTF", 2020): [table]}
    result = builder.source_first_candidate_bound_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
        },
    )
    assert result is not None
    assert result["answer"] == builder.Decimal("507.238147131")
    assert result["tier"] == "source_first_candidate_bound_v1"
    assert result["diagnostics"]["match_mode"] == "exact_contiguous"
    assert result["diagnostics"]["promotion_allowed"] is False


def test_source_first_distinguishes_reported_vamc_principal_from_provision():
    builder = _builder_module()
    item = {
        "question": "Số dư trái phiếu đặc biệt do VAMC phát hành của công ty mẹ ABB đến ngày 31/12/2023 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABB"],
            "years": [2023],
            "scope": "separate",
            "operands": [{"metric": "Số dư trái phiếu đặc biệt do VAMC phát hành"}],
        },
    }
    tables = [
        _table(
            uid="provision",
            ticker="ABB",
            year=2023,
            scope="separate",
            kind="debt_schedule",
            headers=["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
            rows=[
                ["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
                ["Trái phiếu đặc biệt do VAMC phát hành", "187.902", "-"],
            ],
            source_title="13.5 Dự phòng rủi ro chứng khoán đầu tư",
        ),
        _table(
            uid="principal",
            ticker="ABB",
            year=2023,
            scope="separate",
            kind="debt_schedule",
            headers=["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
            rows=[
                ["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
                ["Trái phiếu đặc biệt do VAMC phát hành (a)", "2.720.958", "-"],
            ],
            source_title="13.2 Chứng khoán đầu tư giữ đến ngày đáo hạn",
        ),
    ]
    result = _resolve(builder, item, tables)
    assert result is not None
    assert result["answer"] == builder.Decimal("2720958")
    assert result["selection"]["internal_table_uid"] == "principal"


def test_source_first_rejects_definition_tail_after_metric_label():
    builder = _builder_module()
    item = {
        "question": "Dư nợ đủ tiêu chuẩn của công ty mẹ MBB đến ngày 31/12/2022 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["MBB"],
            "years": [2022],
            "scope": "separate",
            "operands": [{"metric": "Dư nợ đủ tiêu chuẩn"}],
        },
    }
    table = _table(
        uid="definition",
        ticker="MBB",
        year=2022,
        scope="separate",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Số cuối năm", "Số đầu năm"],
        rows=[
            ["Nhãn dòng", "Số cuối năm", "Số đầu năm"],
            [
                "Nợ đủ tiêu chuẩn (a) Nợ trong hạn và được đánh giá là có khả năng thu hồi đầy đủ cả nợ gốc và lãi đúng hạn; hoặc (b) Nợ quá hạn dưới 10 ngày",
                "-",
                "-",
            ],
        ],
        source_title="Phân loại nợ",
    )
    assert _resolve(builder, item, [table]) is None


def test_source_first_prefers_loan_quality_table_over_definition_and_securities():
    builder = _builder_module()
    item = {
        "question": "Dư nợ đủ tiêu chuẩn của công ty mẹ MBB đến ngày 31/12/2022 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["MBB"],
            "years": [2022],
            "scope": "separate",
            "operands": [{"metric": "Dư nợ đủ tiêu chuẩn"}],
        },
    }
    loan_quality = _table(
        uid="loan-quality",
        ticker="MBB",
        year=2022,
        scope="separate",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "31/12/2022 triệu đồng", "31/12/2021 triệu đồng"],
        rows=[
            ["Nhãn dòng", "31/12/2022 triệu đồng", "31/12/2021 triệu đồng"],
            ["Nợ đủ tiêu chuẩn", "428.450.055", "336.767.464"],
        ],
        source_title="10.1 Phân tích chất lượng nợ cho vay",
    )
    securities = _table(
        uid="securities-quality",
        ticker="MBB",
        year=2022,
        scope="separate",
        kind="financial_note",
        headers=["Nhãn dòng", "31/12/2022 triệu đồng", "31/12/2021 triệu đồng"],
        rows=[
            ["Nhãn dòng", "31/12/2022 triệu đồng", "31/12/2021 triệu đồng"],
            ["Nợ đủ tiêu chuẩn", "70.530.144", "55.140.307"],
        ],
        source_title="13.3 Phân tích chất lượng chứng khoán",
    )
    result = _resolve(builder, item, [loan_quality, securities])
    assert result is not None
    assert result["answer"] == builder.Decimal("428450055")
    assert result["selection"]["internal_table_uid"] == "loan-quality"


def test_source_first_uses_nearest_year_section_for_repeated_metric():
    builder = _builder_module()
    item = {
        "question": "Lợi nhuận thuần trong năm 2023 của VIB là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["VIB"],
            "years": [2023],
            "operands": [{"metric": "Lợi nhuận thuần"}],
        },
    }
    table = _table(
        uid="repeated-years",
        ticker="VIB",
        year=2023,
        scope="separate",
        kind="financial_note",
        headers=["Nhãn dòng", "2023 triệu VND"],
        rows=[
            ["Nhãn dòng", "2023 triệu VND"],
            ["Số dư tại ngày 1/1/2023", ""],
            ["Lợi nhuận thuần trong năm", "8.516.907"],
            ["Số dư tại ngày 1/1/2022", ""],
            ["Lợi nhuận thuần trong năm", "8.461.027"],
        ],
        source_title="Vốn và các quỹ",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("8516907")
    assert result["selection"]["row_index"] == 2


def test_source_first_preserves_loan_number_in_row_match():
    builder = _builder_module()
    item = {
        "question": "Số dư khoản vay 1 của VSC đến ngày 31/12/2017 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["VSC"],
            "years": [2017],
            "operands": [{"metric": "Số dư khoản vay 1"}],
        },
    }
    table = _table(
        uid="loans",
        ticker="VSC",
        year=2017,
        scope="consolidated",
        kind="debt_schedule",
        headers=["Nhãn dòng", "31/12/2017 VND", "01/01/2017 VND"],
        rows=[
            ["Nhãn dòng", "31/12/2017 VND", "01/01/2017 VND"],
            ["Khoản vay 1 USD", "4.500.000", "-"],
            ["Khoản vay 4 USD", "4.831.480", "-"],
        ],
        source_title="Các khoản vay",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("4.5")


def test_source_first_uses_start_period_column_for_start_of_year_question():
    builder = _builder_module()
    item = {
        "question": "Số dư trái phiếu chính phủ của STB đầu năm 2017 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["STB"],
            "years": [2017],
            "operands": [{"metric": "Số dư trái phiếu chính phủ STB đầu năm"}],
        },
    }
    table = _table(
        uid="start-period",
        ticker="STB",
        year=2017,
        scope="separate",
        kind="debt_schedule",
        headers=["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
        rows=[
            ["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
            ["Trái phiếu Chính phủ", "8.919.232", "9.636.738"],
        ],
        source_title="13. Chứng khoán đầu tư",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("9636738")
    assert result["selection"]["column_index"] == 2


def test_source_first_reads_metric_column_and_period_row():
    builder = _builder_module()
    item = {
        "question": "Chi phí thuế mặt bằng của VRE cuối năm 2019 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["VRE"],
            "years": [2019],
            "operands": [{"metric": "Chi phí thuế mặt bằng VRE cuối năm"}],
        },
    }
    table = _table(
        uid="transposed",
        ticker="VRE",
        year=2019,
        scope="consolidated",
        kind="financial_note",
        headers=["", "Chi phíthuế mặt bằngTriệu VND", "TổngTriệu VND"],
        rows=[
            ["", "Chi phíthuế mặt bằngTriệu VND", "TổngTriệu VND"],
            ["Số dư đầu năm", "268.105", "593.379"],
            ["Tăng trong năm", "-", "111.804"],
            ["Số dư cuối năm", "258.051", "554.712"],
        ],
        source_title="(b) Chi phí trả trước dài hạn",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    # The source cell is OCR-formatted ``258.051``.  Under the existing
    # financial-table parser contract the dots are thousands separators, so
    # the replayed raw value is 258051 million VND rather than 258.051.
    assert result["answer"] == builder.Decimal("258051")
    assert result["selection"]["period_selection_mode"] == "column_metric_row_end"


def test_source_first_keeps_generic_provision_distinct_from_credit_risk_expense():
    builder = _builder_module()
    item = {
        "question": "Chi phí dự phòng của STB trong năm 2020 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["STB"],
            "years": [2020],
            "operands": [{"metric": "Chi phí dự phòng STB"}],
        },
    }
    note = _table(
        uid="generic-provision-note",
        ticker="STB",
        year=2020,
        scope="consolidated",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Năm nay Triệu đồng", "Năm trước Triệu đồng"],
        rows=[
            ["Nhãn dòng", "Năm nay Triệu đồng", "Năm trước Triệu đồng"],
            ["Chi phí dự phòng", "1.422.948", "226.415"],
        ],
        source_title="32. Chi phí hoạt động",
    )
    credit_risk = _table(
        uid="credit-risk-expense",
        ticker="STB",
        year=2020,
        scope="consolidated",
        kind="income_statement",
        headers=["Nhãn dòng", "Năm nay Triệu đồng", "Năm trước Triệu đồng"],
        rows=[
            ["Nhãn dòng", "Năm nay Triệu đồng", "Năm trước Triệu đồng"],
            ["Chi phí dự phòng rủi ro tín dụng", "(3.036.974)", "(2.152.889)"],
        ],
        source_title="Báo cáo kết quả hoạt động kinh doanh",
    )
    result = _resolve(builder, item, [note, credit_risk])
    assert result is not None
    assert result["answer"] == builder.Decimal("1422948")
    assert result["selection"]["internal_table_uid"] == "generic-provision-note"


def test_source_first_prefers_investment_bond_balance_over_collateral_register():
    builder = _builder_module()
    item = {
        "question": "Số dư trái phiếu Chính phủ của STB đầu năm 2017 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["STB"],
            "years": [2017],
            "operands": [{"metric": "Số dư trái phiếu Chính phủ STB đầu năm"}],
        },
        "candidates": [
            {"internal_table_uid": "collateral", "rank": 1},
            {"internal_table_uid": "investment", "rank": 3},
        ],
    }
    collateral = _table(
        uid="collateral",
        ticker="STB",
        year=2017,
        scope="separate",
        kind="debt_schedule",
        headers=["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
        rows=[
            ["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
            ["Trái phiếu Chính phủ (Thuyết minh số 13)", "8.919.232", "9.636.738"],
        ],
        source_title="36.2 Tài sản, giấy tờ có giá đưa đi thế chấp, cầm cố",
    )
    investment = _table(
        uid="investment",
        ticker="STB",
        year=2017,
        scope="separate",
        kind="debt_schedule",
        headers=["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
        rows=[
            ["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
            ["- Trái phiếu Chính phủ (i)", "29.709.768", "27.045.792"],
        ],
        source_title="13. Chứng khoán đầu tư",
    )
    result = _resolve(builder, item, [collateral, investment])
    assert result is not None
    assert result["answer"] == builder.Decimal("27045792")
    assert result["selection"]["internal_table_uid"] == "investment"


def test_source_first_rejects_start_only_reclassification_table_for_end_question():
    builder = _builder_module()
    item = {
        "question": "Tiền và các khoản tương đương tiền của ACV cuối năm 2015 là bao nhiêu trăm tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ACV"],
            "years": [2015],
            "operands": [{"metric": "Tiền và các khoản tương đương tiền ACV cuối năm"}],
        },
    }
    reclassification = _table(
        uid="reclassification",
        ticker="ACV",
        year=2015,
        scope="consolidated",
        kind="balance_sheet",
        headers=[
            "TÀI SẢN",
            "Mã số",
            "Sau điều chỉnh VND",
            "Tại ngày 01/01/2015 Điều chỉnh VND",
            "Trước điều chỉnh VND",
        ],
        rows=[
            [
                "TÀI SẢN",
                "Mã số",
                "Sau điều chỉnh VND",
                "Tại ngày 01/01/2015 Điều chỉnh VND",
                "Trước điều chỉnh VND",
            ],
            [
                "Tiền và các khoản tương đương tiền",
                "110",
                "3.897.438.211.993",
                "(57.999.999.747)",
                "3.955.438.211.740",
            ],
        ],
        source_title="Báo cáo tài chính kết thúc ngày 31/12/2015",
    )
    reported = _table(
        uid="reported-end",
        ticker="ACV",
        year=2015,
        scope="consolidated",
        kind="financial_note",
        headers=["Nhãn dòng", "31/12/2015 VND", "01/01/2015 VND"],
        rows=[
            ["Nhãn dòng", "31/12/2015 VND", "01/01/2015 VND"],
            [
                "Tiền và các khoản tương đương tiền",
                "4.466.482.193.053",
                "3.897.438.211.993",
            ],
        ],
        source_title="32. Công cụ tài chính",
    )
    result = _resolve(builder, item, [reclassification, reported])
    assert result is not None
    assert result["answer"] == builder.Decimal("44.66482193053")
    assert result["selection"]["internal_table_uid"] == "reported-end"


def test_source_first_allows_receivable_for_long_term_loan_metric():
    builder = _builder_module()
    item = {
        "question": "Tổng cho vay dài hạn của công ty mẹ DLG đến ngày 31/12/2016 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["DLG"],
            "years": [2016],
            "scope": "separate",
            "operands": [{"metric": "Tổng cho vay dài hạn DLG 31/12/"}],
        },
    }
    table = _table(
        uid="dlg-balance",
        ticker="DLG",
        year=2016,
        scope="separate",
        kind="balance_sheet",
        headers=["TÀI SẢN", "Mã số", "Thuyết minh", "31/12/2016 VND"],
        rows=[
            ["TÀI SẢN", "Mã số", "Thuyết minh", "31/12/2016 VND"],
            [
                "Phải thu về cho vay dài hạn",
                "215",
                "10b",
                "225.647.099.300",
            ],
        ],
        source_title="Bảng cân đối kế toán",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("225.6470993")


def test_source_first_preserves_action_and_qualifier_when_related_party_is_first_column():
    builder = _builder_module()
    item = {
        "question": "Tiền gửi của công ty mẹ BID tại đại diện chủ sở hữu đến ngày 31 tháng 12 năm 2022 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["BID"],
            "years": [2022],
            "scope": "separate",
            "operands": [{"metric": "Tiền gửi Ngân hàng TMCP Đầu tư và Phát triển Việt Nam tại đại diện chủ sở hữu 31 tháng 12 năm"}],
        },
    }
    table = _table(
        uid="bid-related-party",
        ticker="BID",
        year=2022,
        scope="separate",
        kind="related_party_schedule",
        headers=["Bên liên quan", "Số dư", "Phải thu Triệu VND", "(Phải trả)"],
        rows=[
            ["Bên liên quan", "Số dư", "Phải thu Triệu VND", "(Phải trả)"],
            [
                "Đại diện chủ sở hữu (NHNN)",
                "- Tiền gửi của BIDV tại đại diện chủ sở hữu",
                "106.304.480",
                "-",
            ],
        ],
        source_title="Chi tiết số dư lớn với các bên liên quan",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("106304480")
    assert result["selection"]["internal_table_uid"] == "bid-related-party"


def test_source_first_can_bind_named_counterparty_row():
    builder = _builder_module()
    item = {
        "question": "Số dư cho vay Công ty Cổ phần Nhà Hòa Bình đến ngày 31/12/2016 của công ty mẹ HBC là bao nhiêu trăm tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["HBC"],
            "years": [2016],
            "scope": "separate",
            "operands": [{"metric": "Số dư cho vay Công ty Cổ phần Nhà Hòa Bình 31/12/ HBC trăm"}],
        },
    }
    table = _table(
        uid="counterparty",
        ticker="HBC",
        year=2016,
        scope="separate",
        kind="financial_note",
        headers=["Đối tượng", "Nội dung", "2016 VND"],
        rows=[
            ["Đối tượng", "Nội dung", "2016 VND"],
            ["Công ty Cổ phần Nhà Hòa Bình", "Cho vay", "221.951.021.299"],
        ],
        source_title="Các bên liên quan",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("2.21951021299")


def test_source_first_keeps_counterparty_and_selects_investment_cost_column():
    builder = _builder_module()
    item = {
        "question": (
            "Giá gốc khoản đầu tư tại CTCP Sài Gòn - Rạch Giá của công ty mẹ "
            "Ngân hàng TMCP Kiên Long (KLB) đến ngày 31/12/2017 là bao nhiêu "
            "triệu đồng?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["KLB"],
            "years": [2017],
            "scope": "separate",
            "operands": [
                {
                    "metric": (
                        "Giá gốc khoản đầu tư tại CTCP Sài Gòn - Rạch Giá "
                        "Ngân hàng TMCP Kiên Long 31/12/"
                    )
                }
            ],
        },
    }
    table = _table(
        uid="klb-investment-cost",
        ticker="KLB",
        year=2017,
        scope="separate",
        kind="financial_note_detail",
        headers=[
            "Tên",
            "31/12/2017 Tỷ lệ nắm giữ %",
            "Giá gốc Triệu VND",
            "31/12/2016 Tỷ lệ nắm giữ %",
            "Giá gốc Triệu VND",
        ],
        rows=[
            [
                "Tên",
                "31/12/2017",
                "",
                "31/12/2016",
                "",
            ],
            [
                "",
                "Tỷ lệ nắm giữ %",
                "Giá gốc Triệu VND",
                "Tỷ lệ nắm giữ %",
                "Giá gốc Triệu VND",
            ],
            [
                "Đầu tư vào các doanh nghiệp khác CTCP Đầu tư Xây dựng Hồng Phát",
                "2,92%",
                "5.250",
                "2,92%",
                "5.250",
            ],
            [
                "CTCP Sài Gòn - Rạch Giá",
                "6,96%",
                "9.271",
                "6,96%",
                "9.271",
            ],
        ],
        source_title="Đầu tư vào công ty con, công ty liên kết",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("9271")
    assert result["selection"]["row_index"] == 3
    assert result["selection"]["column_index"] == 2
    assert (
        result["selection"]["period_selection_mode"]
        == "qualified_metric_explicit_requested_year"
    )


def test_source_first_rejects_minority_row_for_unqualified_shareholder_profit():
    builder = _builder_module()
    item = {
        "question": "Lợi nhuận thuần phân bổ cho cổ đông của NVL năm 2018 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["NVL"],
            "years": [2018],
            "operands": [{"metric": "Lợi nhuận thuần phân bổ cho cổ đông NVL năm"}],
        },
    }
    table = _table(
        uid="shareholders",
        ticker="NVL",
        year=2018,
        scope="consolidated",
        kind="financial_note",
        headers=["Nhãn dòng", "2018 VND"],
        rows=[
            ["Nhãn dòng", "2018 VND"],
            ["Lợi nhuận thuần phân bổ cho cổ đông không kiểm soát trong năm", "40.065.474.562"],
            ["Lợi nhuận thuần phân bổ cho các cổ đông", "3.227.004.714.155"],
        ],
        source_title="Lãi trên cổ phiếu",
    )
    result = _resolve(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("3227.004714155")
    assert result["selection"]["row_label"] == "Lợi nhuận thuần phân bổ cho các cổ đông"


def test_source_first_uses_comparative_column_only_after_exact_year_miss():
    builder = _builder_module()
    item = {
        "question": "Chi phí thuế mặt bằng của VRE cuối năm 2019 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["VRE"],
            "years": [2019],
            "operands": [{"metric": "Chi phí thuế mặt bằng VRE cuối năm"}],
        },
    }
    table = _table(
        uid="vre-2020",
        ticker="VRE",
        year=2020,
        scope="consolidated",
        kind="income_statement",
        headers=["Nhãn dòng", "2019", "2020"],
        rows=[
            ["Nhãn dòng", "2019", "2020"],
            ["Chi phí thuê mặt bằng", "247.997.000", "312.500.000"],
        ],
        source_title="Báo cáo kết quả hoạt động kinh doanh",
    )
    result = _resolve_with_neighbor_fallback(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("247.997")
    assert result["diagnostics"]["report_year"] == 2019
    assert result["diagnostics"]["source_report_year"] == 2020
    assert result["diagnostics"]["report_year_offset"] == 1


def test_source_first_maps_generic_prior_year_to_target_column():
    builder = _builder_module()
    item = {
        "question": "Chi phí nhân viên của GEG trong năm 2019 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["GEG"],
            "years": [2019],
            "operands": [{"metric": "Chi phí nhân viên GEG"}],
        },
    }
    table = _table(
        uid="geg-2020-generic",
        ticker="GEG",
        year=2020,
        scope="separate",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Năm nay VND", "Năm trước VND"],
        rows=[
            ["Nhãn dòng", "Năm nay VND", "Năm trước VND"],
            ["Chi phí nhân viên", "90.000.000.000", "82.000.000.000"],
        ],
        source_title="Chi phí quản lý doanh nghiệp",
    )
    result = _resolve_with_neighbor_fallback(builder, item, [table])
    assert result is not None
    assert result["answer"] == builder.Decimal("82")
    assert result["selection"]["column_index"] == 2
    assert result["diagnostics"]["period_selection_mode"] == "generic_prior_year"


def test_source_first_rejects_neighbor_without_requested_period_column():
    builder = _builder_module()
    item = {
        "question": "Chi phí thuê mặt bằng của VRE cuối năm 2019 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["VRE"],
            "years": [2019],
            "operands": [{"metric": "Chi phí thuê mặt bằng VRE cuối năm"}],
        },
    }
    table = _table(
        uid="vre-2020-no-2019-column",
        ticker="VRE",
        year=2020,
        scope="consolidated",
        kind="income_statement",
        headers=["Nhãn dòng", "31/12/2020 Triệu VND", "1/1/2020 Triệu VND"],
        rows=[
            ["Nhãn dòng", "31/12/2020 Triệu VND", "1/1/2020 Triệu VND"],
            ["Chi phí thuê mặt bằng", "247.997", "180.000"],
        ],
        source_title="Báo cáo kết quả hoạt động kinh doanh",
    )
    assert _resolve_with_neighbor_fallback(builder, item, [table]) is None


def test_source_first_neighbor_rejects_broad_prefixed_row_match():
    builder = _builder_module()
    item = {
        "question": "Tiền gửi của BID đến ngày 31/12/2022 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["BID"],
            "years": [2022],
            "operands": [{"metric": "Tiền gửi"}],
        },
    }
    table = _table(
        uid="bid-2023-prefixed-row",
        ticker="BID",
        year=2023,
        scope="separate",
        kind="financial_note",
        headers=["Nhãn dòng", "Năm nay Triệu đồng", "Năm trước Triệu đồng"],
        rows=[
            ["Nhãn dòng", "Năm nay Triệu đồng", "Năm trước Triệu đồng"],
            ["Trả lãi tiền gửi", "82.000", "56.000"],
        ],
        source_title="Chi phí lãi",
    )
    assert _resolve_with_neighbor_fallback(builder, item, [table]) is None


def test_source_first_neighbor_rejects_reclassification_comparative_column():
    builder = _builder_module()
    item = {
        "question": "Số dư lợi nhuận chưa phân phối của VGC đến ngày 31/12/2016 là bao nhiêu trăm tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["VGC"],
            "years": [2016],
            "operands": [{"metric": "Lợi nhuận chưa phân phối VGC"}],
        },
    }
    table = _table(
        uid="vgc-2017-reclassification",
        ticker="VGC",
        year=2017,
        scope="consolidated",
        kind="balance_sheet",
        headers=[
            "Nhãn dòng",
            "Mã số",
            "Phân loại lại VND",
            "Đã trình bày trên báo cáo năm trước VND",
        ],
        rows=[
            [
                "Nhãn dòng",
                "Mã số",
                "Phân loại lại VND",
                "Đã trình bày trên báo cáo năm trước VND",
            ],
            ["Lợi nhuận sau thuế chưa phân phối", "421", "741.287.274.365", "715.989.588.907"],
        ],
        source_title="Số liệu so sánh",
    )
    assert _resolve_with_neighbor_fallback(builder, item, [table]) is None


def test_source_first_exact_year_wins_over_neighbor_report():
    builder = _builder_module()
    item = {
        "question": "Tổng tài sản của MBB đến ngày 31/12/2020 là bao nhiêu triệu đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["MBB"],
            "years": [2020],
            "operands": [{"metric": "Tổng tài sản MBB"}],
        },
    }
    exact = _table(
        uid="mbb-2020",
        ticker="MBB",
        year=2020,
        scope="separate",
        kind="balance_sheet",
        headers=["Nhãn dòng", "2020", "2019"],
        rows=[
            ["Nhãn dòng", "2020", "2019"],
            ["TỔNG TÀI SẢN CÓ", "400.000.000", "350.000.000"],
        ],
        source_title="Bảng cân đối kế toán",
    )
    neighbor = _table(
        uid="mbb-2021",
        ticker="MBB",
        year=2021,
        scope="separate",
        kind="balance_sheet",
        headers=["Nhãn dòng", "2020", "2021"],
        rows=[
            ["Nhãn dòng", "2020", "2021"],
            ["TỔNG TÀI SẢN CÓ", "401.000.000", "450.000.000"],
        ],
        source_title="Bảng cân đối kế toán",
    )
    result = _resolve_with_neighbor_fallback(builder, item, [exact, neighbor])
    assert result is not None
    assert result["answer"] == builder.Decimal("400")
    assert result["diagnostics"]["source_report_year"] == 2020
    assert result["diagnostics"]["report_year_offset"] == 0


def test_table_ticker_recovers_v2_document_identity():
    builder = _builder_module()
    assert (
        builder.table_ticker(
            {"document_id": "VRE_financial_statements_2020_consolidated"}
        )
        == "VRE"
    )
    assert builder.table_ticker({"document_id": "not_a_financial_asset"}) == ""


def test_source_first_does_not_turn_generic_customer_loan_into_provision():
    builder = _builder_module()
    item = {
        "question": "Số dư dự phòng rủi ro cho vay khách hàng của ACB năm 2019 là bao nhiêu?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ACB"],
            "years": [2019],
            "scope": "separate",
            "operands": [{"metric": "Dự phòng rủi ro cho vay khách hàng"}],
        },
    }
    table = _table(
        uid="acb-loans-only",
        ticker="ACB",
        year=2019,
        scope="separate",
        kind="balance_sheet",
        headers=["Nhãn dòng", "2019"],
        rows=[
            ["Nhãn dòng", "2019"],
            ["Cho vay khách hàng", "100"],
        ],
        source_title="Dư nợ cho vay khách hàng",
    )
    assert _resolve(builder, item, [table]) is None


def test_source_first_temporal_route_replays_both_years_before_arithmetic():
    builder = _builder_module()
    item = {
        "question": "Tăng trưởng khoản vay ngắn hạn của MCH từ cuối năm 2021 đến cuối năm 2022 là bao nhiêu %?",
        "question_plan": {
            "family": "temporal_change",
            "tickers": ["MCH"],
            "years": [2021, 2022],
            "operation_ast": {"op": "percentage_change"},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="mch-2021-loan",
            ticker="MCH",
            year=2021,
            scope="consolidated",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2021"],
            rows=[["Nhãn dòng", "2021"], ["Khoản vay ngắn hạn", "100"]],
        ),
        _table(
            uid="mch-2022-loan",
            ticker="MCH",
            year=2022,
            scope="consolidated",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2022"],
            rows=[["Nhãn dòng", "2022"], ["Khoản vay ngắn hạn", "120"]],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    result = builder.source_first_temporal_answer(item, tables_by_pair=by_pair)
    assert result is not None
    assert result["answer"] == builder.Decimal("20")
    assert [row["role"] for row in result["sources"]] == ["x_old", "x_new"]
    assert [row["internal_table_uid"] for row in result["sources"]] == [
        "mch-2021-loan",
        "mch-2022-loan",
    ]


def test_source_first_temporal_rejects_relaxed_row_missing_qualifier():
    builder = _builder_module()
    item = {
        "question": "Tăng trưởng lãi vay phải trả người bán ngắn hạn của FPT từ cuối năm 2023 đến cuối năm 2025 là bao nhiêu %?",
        "question_plan": {
            "family": "temporal_change",
            "tickers": ["FPT"],
            "years": [2023, 2025],
            "operation_ast": {"op": "percentage_change"},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="fpt-2023-loan-interest",
            ticker="FPT",
            year=2023,
            scope="consolidated",
            kind="income_statement",
            headers=["Nhãn dòng", "2023"],
            rows=[["Nhãn dòng", "2023"], ["Lãi vay phải trả", "10"]],
        ),
        _table(
            uid="fpt-2025-loan-interest",
            ticker="FPT",
            year=2025,
            scope="consolidated",
            kind="income_statement",
            headers=["Nhãn dòng", "2025"],
            rows=[["Nhãn dòng", "2025"], ["Lãi vay phải trả", "20"]],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    assert builder.source_first_temporal_answer(item, tables_by_pair=by_pair) is None


def test_source_first_temporal_rejects_cross_year_row_reclassification():
    builder = _builder_module()
    item = {
        "question": "Mức tăng trưởng tổng doanh thu bộ phận của MSR từ năm 2020 sang năm 2025 là bao nhiêu %?",
        "question_plan": {
            "family": "temporal_change",
            "tickers": ["MSR"],
            "years": [2020, 2025],
            "operation_ast": {"op": "percentage_change"},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="msr-2020-segment-revenue",
            ticker="MSR",
            year=2020,
            scope="consolidated",
            kind="income_statement",
            headers=["Nhãn dòng", "2020"],
            rows=[["Nhãn dòng", "2020"], ["Doanh thu bộ phận", "100"]],
        ),
        _table(
            uid="msr-2025-segment-revenue",
            ticker="MSR",
            year=2025,
            scope="consolidated",
            kind="income_statement",
            headers=["Nhãn dòng", "2025"],
            rows=[["Nhãn dòng", "2025"], ["Doanh thu thuần bộ phận", "150"]],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    assert builder.source_first_temporal_answer(item, tables_by_pair=by_pair) is None


def test_source_first_cross_entity_replays_two_exact_rows_before_subtracting():
    builder = _builder_module()
    item = {
        "question": (
            "Chênh lệch chi phí dịch vụ mua ngoài giữa CTCP Alpha và CTCP Beta "
            "trong năm 2024 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["AAA", "BBB"],
            "years": [2024],
            "scope": "separate",
            "operation_ast": {"op": "subtract", "args": ["entity_a", "entity_b"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="aaa-services",
            ticker="AAA",
            year=2024,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[
                ["Nhãn dòng", "2024 Triệu đồng"],
                ["Chi phí dịch vụ mua ngoài", "100"],
            ],
        ),
        _table(
            uid="bbb-services",
            ticker="BBB",
            year=2024,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[
                ["Nhãn dòng", "2024 Triệu đồng"],
                ["Chi phí dịch vụ mua ngoài", "40"],
            ],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_cross_entity_answer(item, tables_by_pair=by_pair)

    assert result is not None
    assert result["answer"] == builder.Decimal("60")
    assert [source["role"] for source in result["sources"]] == [
        "entity_AAA",
        "entity_BBB",
    ]
    assert result["tier"] == "source_first_cross_entity_v1"


def test_source_first_cross_entity_uses_absolute_value_for_generic_difference():
    builder = _builder_module()
    item = {
        "question": (
            "Chênh lệch chi phí dịch vụ mua ngoài giữa CTCP Alpha và CTCP Beta "
            "trong năm 2024 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["AAA", "BBB"],
            "years": [2024],
            "scope": "separate",
            "operation_ast": {"op": "subtract", "args": ["entity_a", "entity_b"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="aaa-services-reversed",
            ticker="AAA",
            year=2024,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[
                ["Nhãn dòng", "2024 Triệu đồng"],
                ["Chi phí dịch vụ mua ngoài", "40"],
            ],
        ),
        _table(
            uid="bbb-services-reversed",
            ticker="BBB",
            year=2024,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[
                ["Nhãn dòng", "2024 Triệu đồng"],
                ["Chi phí dịch vụ mua ngoài", "100"],
            ],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_cross_entity_answer(item, tables_by_pair=by_pair)

    assert result is not None
    assert result["answer"] == builder.Decimal("60")
    assert "abs(" in result["query"]


def test_source_first_cross_entity_rejects_signed_tax_expense_without_negative_request():
    builder = _builder_module()
    result = {
        "selection": {
            "source_first_match_mode": "exact_contiguous",
            "row_label": "Chi phí thuế TNDN hiện hành",
            "raw_value": builder.Decimal("-18382997"),
            "source_first_table_kind": "income_statement",
        },
        "sources": [
            {
                "source_report_year": 2023,
                "row_label": "Chi phí thuế TNDN hiện hành",
                "source_first_scope": "separate",
            }
        ],
    }

    assert (
        builder._cross_entity_source_result_is_safe(
            result,
            requested_metric="Chi phí thuế TNDN hiện hành",
            requested_year=2023,
            question=(
                "Chênh lệch chi phí thuế TNDN hiện hành giữa AAA và BBB "
                "trong năm 2024"
            ),
        )
        is False
    )


def test_source_first_cross_entity_rejects_investment_business_row_confusion():
    builder = _builder_module()
    item = {
        "question": (
            "Chênh lệch thu nhập từ mua bán chứng khoán đầu tư giữa CTCP Alpha "
            "và CTCP Beta năm 2024 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["AAA", "BBB"],
            "years": [2024],
            "scope": "separate",
            "operation_ast": {"op": "subtract", "args": ["entity_a", "entity_b"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="aaa-security-business",
            ticker="AAA",
            year=2024,
            scope="separate",
            kind="financial_note",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[
                ["Nhãn dòng", "2024 Triệu đồng"],
                ["Thu nhập từ mua bán chứng khoán kinh doanh", "100"],
            ],
        ),
        _table(
            uid="bbb-security-investment",
            ticker="BBB",
            year=2024,
            scope="separate",
            kind="financial_note",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[
                ["Nhãn dòng", "2024 Triệu đồng"],
                ["Thu nhập từ mua bán chứng khoán đầu tư", "40"],
            ],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    assert builder.source_first_cross_entity_answer(item, tables_by_pair=by_pair) is None


def test_source_first_cross_entity_rejects_governance_roster_source():
    builder = _builder_module()
    result = {
        "selection": {
            "source_first_match_mode": "exact_contiguous",
            "row_label": "Thu nhập lãi tiền gửi",
            "source_first_table_kind": "governance_roster",
        },
        "sources": [
            {
                "source_report_year": 2024,
                "row_label": "Thu nhập lãi tiền gửi",
                "source_first_scope": "separate",
            }
        ],
    }

    assert (
        builder._cross_entity_source_result_is_safe(
            result,
            requested_metric="Thu nhập lãi tiền gửi",
            requested_year=2024,
            question="Chênh lệch thu nhập lãi tiền gửi giữa AAA và BBB",
        )
        is False
    )


def test_source_first_cross_entity_requires_one_scope_when_question_is_unqualified():
    builder = _builder_module()
    item = {
        "question": (
            "Chênh lệch tiền mặt và vàng giữa CTCP Alpha và CTCP Beta "
            "cuối năm 2024 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["AAA", "BBB"],
            "years": [2024],
            "operation_ast": {"op": "subtract", "args": ["entity_a", "entity_b"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="aaa-cash",
            ticker="AAA",
            year=2024,
            scope="separate",
            kind="balance_sheet",
            headers=["Nhãn dòng", "Số cuối năm Triệu đồng"],
            rows=[["Nhãn dòng", "Số cuối năm Triệu đồng"], ["Tiền mặt và vàng", "100"]],
        ),
        _table(
            uid="bbb-cash",
            ticker="BBB",
            year=2024,
            scope="consolidated",
            kind="balance_sheet",
            headers=["Nhãn dòng", "Số cuối năm Triệu đồng"],
            rows=[["Nhãn dòng", "Số cuối năm Triệu đồng"], ["Tiền mặt và vàng", "40"]],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    assert builder.source_first_cross_entity_answer(item, tables_by_pair=by_pair) is None


def test_source_first_cross_entity_recovers_one_missing_ticker_from_exact_code_stock_alias():
    builder = _builder_module()
    item = {
        "question": (
            "Chênh lệch vốn chủ sở hữu giữa CTCP Tập đoàn Hòa Phát và "
            "Ngân hàng TMCP Quân đội năm 2024 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["HPG"],
            "years": [2024],
            "operation_ast": {"op": "subtract", "args": ["entity_a", "entity_b"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="hpg-equity",
            ticker="HPG",
            year=2024,
            scope="consolidated",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[["Nhãn dòng", "2024 Triệu đồng"], ["Vốn chủ sở hữu", "100"]],
        ),
        _table(
            uid="mbb-equity",
            ticker="MBB",
            year=2024,
            scope="consolidated",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2024 Triệu đồng"],
            rows=[["Nhãn dòng", "2024 Triệu đồng"], ["Vốn chủ sở hữu", "40"]],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_cross_entity_answer(item, tables_by_pair=by_pair)

    assert result is not None
    assert result["answer"] == builder.Decimal("60")
    assert result["ticker_source"] == "exact_code_stock_alias_recovery"
    assert result["planned_tickers"] == ["HPG"]
    assert [source["role"] for source in result["sources"]] == [
        "entity_HPG",
        "entity_MBB",
    ]


def test_source_first_cross_entity_recovers_structural_alias_and_preserves_less_direction():
    builder = _builder_module()
    item = {
        "question": (
            "Cuối năm 2020, số lượng cổ phiếu đang lưu hành của CTCP Tasco "
            "ít hơn của CTCP Dịch vụ Hoàng Huy là bao nhiêu?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["HUT"],
            "years": [2020],
            "operation_ast": {"op": "subtract", "args": ["entity_a", "entity_b"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="hut-shares",
            ticker="HUT",
            year=2020,
            scope="separate",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2020"],
            rows=[["Nhãn dòng", "2020"], ["Số lượng cổ phiếu đang lưu hành", "100"]],
        ),
        _table(
            uid="hhs-shares",
            ticker="HHS",
            year=2020,
            scope="separate",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2020"],
            rows=[["Nhãn dòng", "2020"], ["Số lượng cổ phiếu đang lưu hành", "160"]],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_cross_entity_answer(item, tables_by_pair=by_pair)

    assert result is not None
    assert result["answer"] == builder.Decimal("60")
    assert result["ticker_source"] == "exact_code_stock_alias_recovery"
    assert "entity_HHS" in result["query"]
    assert "entity_HUT" in result["query"]


def test_source_first_cross_entity_rejects_equity_component_for_total_equity_request():
    builder = _builder_module()
    result = {
        "selection": {
            "source_first_match_mode": "exact_contiguous",
            "row_label": "Vốn đầu tư của chủ sở hữu",
            "source_first_table_kind": "balance_sheet",
        },
        "sources": [
            {
                "source_report_year": 2021,
                "row_label": "Vốn đầu tư của chủ sở hữu",
                "source_first_scope": "separate",
            }
        ],
    }

    assert (
        builder._cross_entity_source_result_is_safe(
            result,
            requested_metric="Vốn chủ sở hữu",
            requested_year=2021,
            question="Chênh lệch vốn chủ sở hữu giữa AAA và BBB năm 2021",
        )
        is False
    )


def test_source_first_binds_hierarchical_section_to_metric_header():
    builder = _builder_module()
    item = {
        "question": (
            "Giá trị còn lại của quyền sử dụng đất của MBB vào cuối năm 2018 "
            "là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["MBB"],
            "years": [2018],
            "operands": [{"metric": "Giá trị còn lại quyền sử dụng đất MBB"}],
        },
    }
    table = _table(
        uid="mbb-hierarchical",
        ticker="MBB",
        year=2018,
        scope="consolidated",
        kind="financial_note_detail",
        headers=[
            "Nhãn dòng",
            "Quyền sử dụng đất Triệu đồng",
            "Phần mềm máy vi tính Triệu đồng",
            "Tổng cộng Triệu đồng",
        ],
        rows=[
            [
                "",
                "Quyền sử dụng đất Triệu đồng",
                "Phần mềm máy vi tính Triệu đồng",
                "Tổng cộng Triệu đồng",
            ],
            ["Nguyên giá", "", "", ""],
            ["Số dư cuối năm", "1.000", "2.000", "3.000"],
            ["Giá trị còn lại", "", "", ""],
            ["Tại ngày cuối năm", "933.246", "177.431", "1.113.162"],
        ],
    )
    table["header_row_indices"] = [2]

    result = _resolve(builder, item, [table])

    assert result is not None
    assert result["answer"] == builder.Decimal("933246")
    assert result["selection"]["row_index"] == 4
    assert result["selection"]["column_index"] == 1
    assert result["selection"]["period_selection_mode"] == "contextual_section_metric_period"


def test_source_first_binds_contextual_rent_total_to_closing_column():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng khoản tiền thuê tối thiểu theo hợp đồng thuê hoạt động của GEX "
            "đến ngày 31/12/2017 là bao nhiêu trăm tỷ đồng?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["GEX"],
            "years": [2017],
            "operands": [{"metric": "Tổng khoản tiền thuê tối thiểu theo hợp đồng thuê hoạt động GEX 31/12/ trăm"}],
        },
    }
    table = _table(
        uid="gex-rent",
        ticker="GEX",
        year=2017,
        scope="consolidated",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Số cuối năm", "Đơn vị tính: VND"],
        rows=[
            ["", "Số cuối năm", "Đơn vị tính: VND"],
            ["", "Số đầu năm", ""],
            ["Đến 1 năm", "72.529.223.139", "27.244.235.607"],
            ["Trên 1 - 5 năm", "71.636.840.670", "60.652.301.927"],
            ["Trên 5 năm", "99.937.092.037", "80.312.420.916"],
            ["TỔNG CỘNG", "244.103.155.846", "168.208.958.450"],
        ],
        source_title="Cam kết cho thuê hoạt động; các khoản tiền thuê tối thiểu trong tương lai",
    )

    result = _resolve(builder, item, [table])

    assert result is not None
    assert result["answer"] == builder.Decimal("2.44103155846")
    assert result["selection"]["row_index"] == 5
    assert result["selection"]["column_index"] == 1
    assert result["selection"]["source_first_match_mode"] == "contextual_rent_commitment_total"


def test_source_first_binds_contextual_rent_maturity_bucket_to_closing_column():
    builder = _builder_module()
    item = {
        "question": (
            "Cam kết thuê hoạt động đến hạn trong 1 năm của HDB cuối năm "
            "2020 là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["HDB"],
            "years": [2020],
            "scope": "separate",
            "operands": [{"metric": "Cam kết thuê hoạt động"}],
        },
    }
    table = _table(
        uid="hdb-rent-maturity",
        ticker="HDB",
        year=2020,
        scope="separate",
        kind="financial_note",
        headers=["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
        rows=[
            ["", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
            ["Các cam kết thuê hoạt động", "1.303.726", "1.277.845"],
            ["Trong đó:", "", ""],
            ["- Đến hạn trong 1 năm", "17.186", "17.041"],
            ["- Đến hạn từ 1 đến 5 năm", "509.662", "436.389"],
        ],
        source_title="42. Cam kết thuê hoạt động; các khoản tiền thuê tối thiểu",
    )

    result = _resolve(builder, item, [table])

    assert result is not None
    assert result["answer"] == builder.Decimal("17.186")
    assert result["selection"]["row_index"] == 3
    assert result["selection"]["column_index"] == 1
    assert (
        result["selection"]["source_first_match_mode"]
        == "contextual_rent_commitment_maturity_1y"
    )
    assert result["selection"]["period_selection_mode"] == (
        "contextual_explicit_period_column"
    )


def test_source_first_rejects_one_year_maturity_without_lease_context():
    builder = _builder_module()
    item = {
        "question": (
            "Cam kết thuê hoạt động đến hạn trong 1 năm của HDB cuối năm "
            "2020 là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["HDB"],
            "years": [2020],
            "scope": "separate",
            "operands": [{"metric": "Cam kết thuê hoạt động"}],
        },
    }
    table = _table(
        uid="hdb-liquidity-maturity",
        ticker="HDB",
        year=2020,
        scope="separate",
        kind="financial_note",
        headers=["Nhãn dòng", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
        rows=[
            ["", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
            ["Đến hạn trong 1 năm", "17.186", "17.041"],
        ],
        source_title="Bảng kỳ hạn thanh khoản; các khoản nợ tài chính",
    )

    assert _resolve(builder, item, [table]) is None


def test_source_first_counts_parent_operating_lease_maturity_threshold():
    builder = _builder_module()
    item = {
        "question": (
            "Trong năm 2020, có bao nhiêu công ty mẹ của MBB, HDB, KLB và NAB "
            "có cam kết thuê hoạt động đến hạn trong 1 năm lớn hơn 40 tỷ đồng?"
        ),
        "question_plan": {
            "family": "conditional_analytical",
            "tickers": ["MBB", "HDB", "KLB", "NAB"],
            "years": [2020],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "plan_required", "args": []},
            "operands": [],
        },
    }
    values = {
        "MBB": ("- đến hạn trong 1 năm", "31.007"),
        "HDB": ("- Đến hạn trong 1 năm", "17.186"),
        "KLB": ("Trong vòng 1 năm", "49.649"),
        "NAB": ("Đến một năm", "79.657"),
    }
    tables = []
    for ticker, (row_label, value) in values.items():
        tables.append(
            _table(
                uid=f"{ticker.lower()}-rent-threshold",
                ticker=ticker,
                year=2020,
                scope="separate",
                kind="segment_reporting" if ticker == "KLB" else "financial_note",
                headers=[
                    "Nhãn dòng",
                    "Số cuối nămTriệu đồng",
                    "Số đầu nămTriệu đồng",
                ],
                rows=[
                    ["", "Số cuối nămTriệu đồng", "Số đầu nămTriệu đồng"],
                    [row_label, value, "0"],
                ],
                source_title="Cam kết thuê hoạt động; tiền thuê tối thiểu trong tương lai",
            )
        )

    by_pair = {(table["ticker"], 2020): [table] for table in tables}
    result = builder.source_first_multi_entity_lease_threshold_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("2")
    assert result["positive_tickers"] == ["KLB", "NAB"]
    assert result["values"] == {
        "MBB": "31.007",
        "HDB": "17.186",
        "KLB": "49.649",
        "NAB": "79.657",
    }
    assert result["tier"] == "source_first_multi_entity_lease_threshold_v1"


def test_source_first_counts_parent_interest_expense_threshold():
    builder = _builder_module()
    item = {
        "question": (
            "Căn cứ số liệu năm 2016 của công ty mẹ AAA, công ty mẹ NKG, "
            "công ty mẹ DCM và công ty mẹ DPM, tổng số công ty có phát sinh "
            "chi phí lãi vay nhiều hơn 100 tỷ là bao nhiêu?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["AAA", "NKG", "DCM", "DPM"],
            "years": [2016],
            "scope": "separate",
            "requested_unit": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
            "operands": [],
        },
    }
    p_and_l_values = {
        "AAA": ("- Trong đó: Chi phí lãi vay", "23.874.478.344"),
        "NKG": ("- Trong đó: chi phí lãi vay", "141.639.235.578"),
        "DCM": ("- Trong đó: Chỉ phí lãi vay", "203.937.110.047"),
    }
    tables = [
        _table(
            uid=f"{ticker.lower()}-interest-threshold",
            ticker=ticker,
            year=2016,
            scope="separate",
            kind="income_statement",
            headers=[
                "CHỈ TIÊU",
                "Mã số",
                "Thuyết minh",
                "Năm 2016VND",
                "Năm 2015VND",
            ],
            rows=[
                [
                    "CHỈ TIÊU",
                    "Mã số",
                    "Thuyết minh",
                    "Năm 2016VND",
                    "Năm 2015VND",
                ],
                [p_and_l_values[ticker][0], "23", "", p_and_l_values[ticker][1], "0"],
            ],
            source_title="Báo cáo kết quả hoạt động kinh doanh",
        )
        for ticker in p_and_l_values
    ]
    tables.append(
        _table(
            uid="dpm-interest-threshold-note",
            ticker="DPM",
            year=2016,
            scope="separate",
            kind="financial_note_detail",
            headers=["Nhãn dòng", "Năm nayVND", "Năm trướcVND"],
            rows=[
                ["", "Năm nay", "Năm trước"],
                ["", "VND", "VND"],
                ["Chi phí lãi vay", "4.473.655.664", "0"],
            ],
            source_title="32. CHI PHÍ TÀI CHÍNH",
        )
    )

    result = builder.source_first_multi_entity_interest_threshold_answer(
        item,
        tables_by_pair={
            (table["ticker"], table["report_year"]): [table]
            for table in tables
        },
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("2")
    assert result["positive_tickers"] == ["NKG", "DCM"]
    assert result["values"] == {
        "AAA": "23.874478344",
        "NKG": "141.639235578",
        "DCM": "203.937110047",
        "DPM": "4.473655664",
    }
    assert result["tier"] == "source_first_multi_entity_interest_threshold_v1"
    assert [source["role"] for source in result["sources"]] == [
        "entity_AAA",
        "entity_NKG",
        "entity_DCM",
        "entity_DPM",
    ]
    assert result["sources"][-1]["source_first_table_kind"] == (
        "financial_note_detail"
    )


def test_multi_entity_interest_threshold_rejects_unqualified_scope():
    builder = _builder_module()
    item = {
        "question": (
            "Năm 2016, AAA, NKG, DCM và DPM có bao nhiêu công ty có phát sinh "
            "chi phí lãi vay nhiều hơn 100 tỷ?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["AAA", "NKG", "DCM", "DPM"],
            "years": [2016],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }

    assert builder._multi_entity_interest_threshold_spec(item) is None


def test_source_first_binds_investment_property_depreciation_to_total_column():
    builder = _builder_module()
    item = {
        "question": (
            "Trích khấu hao bất động sản đầu tư cho thuê của SSH là bao nhiêu "
            "tỷ đồng trong năm 2023?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["SSH"],
            "years": [2023],
            "operands": [{"metric": "Trích khấu hao bất động sản đầu tư cho thuê SSH"}],
        },
    }
    table = _table(
        uid="ssh-investment-property",
        ticker="SSH",
        year=2023,
        scope="separate",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Nhà cửa VND", "Tài sản khác VND", "Tổng VND"],
        rows=[
            ["", "Nhà cửa", "Tài sản khác", "Tổng"],
            ["", "VND", "VND", "VND"],
            ["GIÁ TRỊ HAO MÒN LŨY KẾ", "", "", ""],
            ["Khấu hao trong năm", "1.427.879.136", "358.193.912", "1.786.073.048"],
        ],
        source_title="Tăng giảm bất động sản đầu tư; bất động sản đầu tư cho thuê; giá trị hao mòn lũy kế",
    )

    result = _resolve(builder, item, [table])

    assert result is not None
    assert result["answer"] == builder.Decimal("1.786073048")
    assert result["selection"]["row_index"] == 3
    assert result["selection"]["column_index"] == 3
    assert result["selection"]["source_first_match_mode"] == "contextual_investment_property_depreciation_total"


def test_source_first_recovers_unique_ticker_from_candidate_document_id():
    builder = _builder_module()
    item = {
        "question": "Số dư vay ngắn hạn cuối năm 2020 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": [],
            "years": [2020],
            "operands": [{"metric": "Số dư vay ngắn hạn CP Tập đoàn Kỹ nghệ gỗ Trường Thành cuối năm"}],
        },
        "candidates": [
            {
                "internal_table_uid": "ttf-transposed",
                "document_id": "TTF_financial_statements_2020_consolidated",
            }
        ],
    }
    table = _table(
        uid="ttf-transposed",
        ticker="TTF",
        year=2020,
        scope="consolidated",
        kind="financial_note_detail",
        headers=["Số đầu năm", "Cột nguồn 2", "Vay ngắn hạn", "Vay dài hạn", "VND Tổng cộng"],
        rows=[
            ["", "", "Vay ngắn hạn", "Vay dài hạn", "VND Tổng cộng"],
            ["Số đầu năm", "", "131.290.187.188", "362.913.767.131", "494.203.954.319"],
            ["Số cuối năm", "", "507.238.147.131", "-", "507.238.147.131"],
        ],
    )
    table["header_row_indices"] = [0, 2]

    result = _resolve(builder, item, [table])

    assert result is not None
    assert result["answer"] == builder.Decimal("507.238147131")
    assert result["selection"]["row_index"] == 2
    assert result["selection"]["column_index"] == 2


def test_candidate_bound_source_replay_uses_top_table_context():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng trái phiếu thường của Công ty CP Hoàng Anh Gia Lai (HAG) "
            "vào cuối năm 2021 là bao nhiêu nghìn đồng?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["HAG"],
            "years": [2021],
            "scope": None,
            "operands": [{"metric": "Tổng trái phiếu thường CP Hoàng Anh Gia Lai"}],
        },
        "candidates": [
            {
                "rank": 1,
                "internal_table_uid": "hag-bond-top",
                "document_id": "HAG_financial_statements_2021_separate",
                "scope": "separate",
                "review_score": 0.69,
            }
        ],
    }
    table = _table(
        uid="hag-bond-top",
        ticker="HAG",
        year=2021,
        scope="separate",
        kind="debt_schedule",
        headers=["Tổ chức thu xếp phát hành", "Trái chủ", "Số cuối nămNgàn VND"],
        rows=[
            ["Tổ chức thu xếp phát hành", "Trái chủ", "Số cuối nămNgàn VND"],
            ["ACBS", "Công ty liên quan", "300.000.000"],
            ["Chi phí phát hành trái phiếu", "", "(283.173)"],
            ["TỔNG CỘNG", "", "299.716.827"],
        ],
        source_title="21.1 Trái phiếu thường dài hạn",
    )
    tables_by_pair = {("HAG", 2021): [table]}
    resolver_kwargs = {
        "parse_decimal": builder.parse_decimal,
        "candidate_evidence_window": builder.candidate_evidence_window,
        "choose_year_column": builder.choose_year_column,
        "source_multiplier": builder.source_multiplier,
        "requested_divisor": builder.requested_divisor,
        "report_year_neighbor_fallback": None,
    }

    result = builder.source_first_candidate_bound_answer(
        item,
        tables_by_pair=tables_by_pair,
        resolver_kwargs=resolver_kwargs,
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("299716.827")
    assert result["tier"] == "source_first_candidate_bound_v1"
    assert result["diagnostics"]["candidate_bound_boundary"] == "table"
    assert result["selection"]["internal_table_uid"] == "hag-bond-top"


def test_candidate_bound_source_replay_can_hydrate_neighbor_table_in_document():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng số tài sản tài chính của Ngân hàng TMCP Xuất nhập khẩu Việt Nam "
            "(EIB) đến ngày 31/12/2020 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["EIB"],
            "years": [2020],
            "scope": None,
            "operands": [{"metric": "Tổng số tài sản tài chính"}],
        },
        "candidates": [
            {
                "rank": 1,
                "internal_table_uid": "eib-navigation-table",
                "document_id": "EIB_financial_statements_2020_separate",
                "scope": "separate",
                "review_score": 0.63,
            }
        ],
    }
    navigation = _table(
        uid="eib-navigation-table",
        ticker="EIB",
        year=2020,
        scope="separate",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "2020 Triệu đồng"],
        rows=[["Nhãn dòng", "2020 Triệu đồng"], ["Vay các TCTD", "1.000"]],
        source_title="41. Vay các TCTD",
    )
    target = _table(
        uid="eib-financial-assets",
        ticker="EIB",
        year=2020,
        scope="separate",
        kind="debt_schedule",
        headers=[
            "Nhãn dòng",
            "Kinh doanhTriệu đồng",
            "Giữ đến ngày đáo hạnTriệu đồng",
            "Cho vay và phải thuTriệu đồng",
            "Tổng cộng giá trị ghi sốTriệu đồng",
        ],
        rows=[
            ["", "Kinh doanh", "Giữ đến ngày đáo hạn", "Cho vay và phải thu", "Tổng cộng giá trị ghi số"],
            ["Tài sản tài chính", "", "", "", ""],
            ["Tiền mặt", "-", "-", "-", "2.000"],
            ["", "10", "20", "30", "60"],
        ],
        source_title="41. Thuyết minh bổ sung về tài sản tài chính",
    )
    tables_by_pair = {("EIB", 2020): [navigation, target]}
    resolver_kwargs = {
        "parse_decimal": builder.parse_decimal,
        "candidate_evidence_window": builder.candidate_evidence_window,
        "choose_year_column": builder.choose_year_column,
        "source_multiplier": builder.source_multiplier,
        "requested_divisor": builder.requested_divisor,
        "report_year_neighbor_fallback": None,
    }

    result = builder.source_first_candidate_bound_answer(
        item,
        tables_by_pair=tables_by_pair,
        resolver_kwargs=resolver_kwargs,
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("60")
    assert result["diagnostics"]["candidate_bound_boundary"] == "document"
    assert result["selection"]["internal_table_uid"] == "eib-financial-assets"


def test_source_first_selects_answer_metric_from_extreme_condition_year():
    builder = _builder_module()
    question = (
        "Trong các năm 2021, 2023 và 2025, chi phí lãi vay của CTCP Tập đoàn Hà Đô "
        "trong năm có tổng số dư tiền và các khoản tương đương tiền cuối năm lớn nhất "
        "là bao nhiêu tỷ đồng?"
    )
    item = {
        "question": question,
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HDG"],
            "years": [2021, 2023, 2025],
            "scope": None,
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
        "candidates": [
            {"rank": 1, "scope": "consolidated", "report_year": 2025},
            {"rank": 2, "scope": "consolidated", "report_year": 2021},
        ],
    }
    cash = {2021: "230.395.142.669", 2023: "694.458.293.386", 2025: "265.730.670.677"}
    interest = {2021: "386.784.756.824", 2023: "500.688.616.629", 2025: "299.780.784.516"}
    tables_by_pair = {}
    for year in (2021, 2023, 2025):
        tables_by_pair[("HDG", year)] = [
            _table(
                uid=f"hdg-cash-{year}",
                ticker="HDG",
                year=year,
                scope="consolidated",
                kind="balance_sheet",
                headers=["Nhãn dòng", "Số cuối năm VND"],
                rows=[
                    ["Nhãn dòng", "Số cuối năm VND"],
                    ["I. Tiền và các khoản tương đương tiền", cash[year]],
                ],
                source_title="Bảng cân đối kế toán",
            ),
            _table(
                uid=f"hdg-interest-{year}",
                ticker="HDG",
                year=year,
                scope="consolidated",
                kind="financial_note_detail",
                headers=["Nhãn dòng", "Năm nay VND"],
                rows=[
                    ["Nhãn dòng", "Năm nay VND"],
                    ["Chi phí lãi vay", interest[year]],
                ],
                source_title="Chi phí tài chính",
            ),
        ]
    resolver_kwargs = {
        "parse_decimal": builder.parse_decimal,
        "candidate_evidence_window": builder.candidate_evidence_window,
        "choose_year_column": builder.choose_year_column,
        "source_multiplier": builder.source_multiplier,
        "requested_divisor": builder.requested_divisor,
        "report_year_neighbor_fallback": None,
    }

    result = builder.source_first_conditional_temporal_answer(
        item,
        tables_by_pair=tables_by_pair,
        resolver_kwargs=resolver_kwargs,
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("500.688616629")
    assert result["selected_year"] == 2023
    # Condition values are compared in source-normalized units, not the
    # answer metric's requested output unit (billion VND).
    assert result["condition_values"]["2023"] == "694458293386"


def test_source_first_period_extreme_replays_one_row_across_years():
    builder = _builder_module()
    item = {
        "question": (
            "Doanh thu thuần cao nhất của HPG trong các năm 2021, 2022 và "
            "2023 là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HPG"],
            "years": [2021, 2022, 2023],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    values = {2021: "100.000.000.000", 2022: "150.000.000.000", 2023: "120.000.000.000"}
    tables = [
        _table(
            uid=f"hpg-revenue-{year}",
            ticker="HPG",
            year=year,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", f"{year} VND"],
            rows=[["Nhãn dòng", f"{year} VND"], ["Doanh thu thuần", value]],
        )
        for year, value in values.items()
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    result = builder.source_first_period_extreme_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_period_extreme_v1"
    assert result["answer"] == builder.Decimal("150")
    assert result["selected_year"] == 2022
    assert [source["role"] for source in result["sources"]] == [
        "period_extreme_candidate_2021",
        "answer_metric",
        "period_extreme_candidate_2023",
    ]
    answer_source = next(
        source for source in result["sources"] if source["role"] == "answer_metric"
    )
    assert answer_source["value"] == result["answer"]


def test_source_first_period_extreme_preserves_scaled_decimal_answer():
    builder = _builder_module()
    item = {
        "question": (
            "Lãi cơ bản trên cổ phiếu cao nhất của HPG trong các năm 2021, "
            "2022 và 2023 là bao nhiêu nghìn đồng/cổ phiếu?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HPG"],
            "years": [2021, 2022, 2023],
            "scope": "separate",
            "requested_unit": "thousand_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    values = {2021: "1000", 2022: "3375", 2023: "960"}
    tables = [
        _table(
            uid=f"hpg-eps-scaled-{year}",
            ticker="HPG",
            year=year,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", f"{year} VND/cp"],
            rows=[
                ["Nhãn dòng", f"{year} VND/cp"],
                ["Lãi cơ bản trên cổ phiếu", value],
            ],
        )
        for year, value in values.items()
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    result = builder.source_first_period_extreme_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("3.375")
    assert result["selected_year"] == 2022
    assert result["period_values"] == {
        "2021": "1",
        "2022": "3.375",
        "2023": "0.96",
    }


def test_source_first_period_extreme_rejects_tied_winner():
    builder = _builder_module()
    item = {
        "question": (
            "Lãi cơ bản trên cổ phiếu cao nhất của HPG trong các năm 2021, "
            "2022 và 2023 là bao nhiêu nghìn đồng/cổ phiếu?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HPG"],
            "years": [2021, 2022, 2023],
            "scope": "separate",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    tables = [
        _table(
            uid=f"hpg-eps-{year}",
            ticker="HPG",
            year=year,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", f"{year} VND/cp"],
            rows=[
                ["Nhãn dòng", f"{year} VND/cp"],
                ["Lãi cơ bản trên cổ phiếu", "10"],
            ],
        )
        for year in (2021, 2022, 2023)
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    assert builder.source_first_period_extreme_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    ) is None


def test_source_first_period_extreme_rejects_row_identity_drift():
    builder = _builder_module()
    item = {
        "question": "Doanh thu thuần cao nhất của HPG trong các năm 2021, 2022 và 2023 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HPG"],
            "years": [2021, 2022, 2023],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    labels = {
        2021: "Doanh thu thuần",
        2022: "Doanh thu bán hàng và cung cấp dịch vụ",
        2023: "Doanh thu thuần",
    }
    tables = [
        _table(
            uid=f"hpg-revenue-drift-{year}",
            ticker="HPG",
            year=year,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", f"{year} VND"],
            rows=[["Nhãn dòng", f"{year} VND"], [labels[year], "100.000.000.000"]],
        )
        for year in (2021, 2022, 2023)
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    assert builder.source_first_period_extreme_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    ) is None


def test_source_first_period_extreme_does_not_treat_gross_revenue_as_net():
    builder = _builder_module()
    item = {
        "question": (
            "Doanh thu thuần cao nhất của HPG trong các năm 2021, 2022 và "
            "2023 là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HPG"],
            "years": [2021, 2022, 2023],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    tables = [
        _table(
            uid=f"hpg-gross-revenue-{year}",
            ticker="HPG",
            year=year,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", f"{year} VND"],
            rows=[
                ["Nhãn dòng", f"{year} VND"],
                ["Doanh thu bán hàng và cung cấp dịch vụ", "100.000.000.000"],
            ],
        )
        for year in (2021, 2022, 2023)
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    assert builder.source_first_period_extreme_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    ) is None


def test_source_first_period_extreme_rejects_acquisition_schedule_snapshot():
    builder = _builder_module()
    item = {
        "question": (
            "Giá trị còn lại của tài sản cố định vô hình lớn nhất của SHB "
            "trong các năm tài chính 2016, 2018 và 2020 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["SHB"],
            "years": [2016, 2018, 2020],
            "scope": "consolidated",
            "requested_unit": "million_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    tables = [
        _table(
            uid="shb-acquisition-snapshot",
            ticker="SHB",
            year=2016,
            scope="consolidated",
            kind="financial_data_schedule",
            headers=[
                "Nhãn dòng",
                "Giá trị ghi sổ trước thời điểm mua Triệu VND",
                "Các điều chỉnh tài sản và nợ phải trả Triệu VND",
                "Giá trị ghi nhận tại thời điểm mua Triệu VND",
            ],
            rows=[
                [
                    "Nhãn dòng",
                    "Giá trị ghi sổ trước thời điểm mua Triệu VND",
                    "Các điều chỉnh tài sản và nợ phải trả Triệu VND",
                    "Giá trị ghi nhận tại thời điểm mua Triệu VND",
                ],
                ["Tài sản cố định vô hình", "1.047", "-", "1.047"],
            ],
            source_title="Tài sản và nợ phải trả tại ngày mua",
        ),
        *[
            _table(
                uid=f"shb-balance-{year}",
                ticker="SHB",
                year=year,
                scope="consolidated",
                kind="balance_sheet",
                headers=["Nhãn dòng", f"31/12/{year} Triệu VND"],
                rows=[
                    ["Nhãn dòng", f"31/12/{year} Triệu VND"],
                    ["Tài sản cố định vô hình", "3.538.006"],
                ],
            )
            for year in (2018, 2020)
        ],
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    assert builder.source_first_period_extreme_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    ) is None


def test_source_first_period_extreme_rejects_mixed_sign_provision_series():
    builder = _builder_module()
    item = {
        "question": (
            "Mức giá trị lớn nhất của chỉ tiêu Tổng số dư dự phòng rủi ro "
            "cho vay khách hàng của VIB qua các năm tài chính 2015, 2022 "
            "và 2023 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["VIB"],
            "years": [2015, 2022, 2023],
            "scope": "consolidated",
            "requested_unit": "million_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    values = {2015: "752.476", 2022: "(3.064.773)", 2023: "(4.270.530)"}
    tables = [
        _table(
            uid=f"vib-provision-{year}",
            ticker="VIB",
            year=year,
            scope="consolidated",
            kind="balance_sheet",
            headers=["Nhãn dòng", f"{year} Triệu VND"],
            rows=[
                ["Nhãn dòng", f"{year} Triệu VND"],
                ["Dự phòng rủi ro cho vay khách hàng", value],
            ],
        )
        for year, value in values.items()
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    assert builder.source_first_period_extreme_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    ) is None


def test_temporal_row_signature_normalizes_parenthetical_lai_lo_label():
    builder = _builder_module()
    assert builder._temporal_row_signature("III. Lãi/(lỗ) thuần") == "lai thuan"
    assert builder._temporal_row_signature("Lãi thuần") == "lai thuan"


def test_conditional_source_guard_rejects_derived_row_for_requested_total():
    builder = _builder_module()
    result = {
        "selection": {
            "source_first_match_mode": "exact_contiguous",
            "source_report_year": 2024,
            "row_label": "Trích trước chi phí bán hàng",
        }
    }

    assert not builder._conditional_source_result_is_safe(
        result,
        metric="Tổng chi phí bán hàng",
        requested_year=2024,
    )


def test_conditional_source_guard_rejects_generic_subtotal_for_named_metric():
    builder = _builder_module()
    result = {
        "selection": {
            "source_first_match_mode": "exact_contiguous",
            "source_report_year": 2025,
            "row_label": "TỔNG CỘNG",
        }
    }

    assert not builder._conditional_source_result_is_safe(
        result,
        metric="Tổng số dư tiền và các khoản tương đương tiền cuối năm",
        requested_year=2025,
    )


def test_conditional_source_guard_accepts_contextual_cash_total_only_for_matching_metric():
    builder = _builder_module()
    result = {
        "selection": {
            "source_first_match_mode": "contextual_cash_and_equivalents_total",
            "source_report_year": 2025,
            "row_label": "TỔNG CỘNG",
        }
    }

    assert builder._conditional_source_result_is_safe(
        result,
        metric="Tổng số dư tiền và các khoản tương đương tiền cuối năm",
        requested_year=2025,
    )
    assert not builder._conditional_source_result_is_safe(
        result,
        metric="Tổng nợ phải trả cuối năm",
        requested_year=2025,
    )


def test_temporal_route_recovers_single_ticker_from_cross_entity_family():
    """A one-issuer/two-year comparison may be mislabeled by the planner."""

    builder = _builder_module()
    item = {
        "question": (
            "Biến động giá vốn bán điện của công ty mẹ DNH giữa năm 2017 "
            "và năm 2016 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["DNH"],
            "years": [2016, 2017],
            "scope": "separate",
            "operation_ast": {"op": "subtract", "args": ["x_new", "x_old"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="dnh-2016-cost",
            ticker="DNH",
            year=2016,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", "2016 Triệu đồng"],
            rows=[["Nhãn dòng", "2016 Triệu đồng"], ["Giá vốn bán điện", "100"]],
        ),
        _table(
            uid="dnh-2017-cost",
            ticker="DNH",
            year=2017,
            scope="separate",
            kind="income_statement",
            headers=["Nhãn dòng", "2017 Triệu đồng"],
            rows=[["Nhãn dòng", "2017 Triệu đồng"], ["Giá vốn bán điện", "125"]],
        ),
    ]

    result = builder.source_first_temporal_answer(
        item,
        tables_by_pair={
            (table["ticker"], table["report_year"]): [table]
            for table in tables
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_temporal_cross_entity_v1"
    assert result["family_recovered"] is True
    assert result["answer"] == builder.Decimal("25")
    assert result["metric_variants"] == ["gia von ban dien"]


def test_temporal_route_ignores_note_number_suffixes_between_years():
    builder = _builder_module()
    item = {
        "question": (
            "Tỷ lệ biến động số dư dự phòng phải thu ngắn hạn khó đòi của HBC "
            "giữa năm 2016 và năm 2020 là bao nhiêu %?"
        ),
        "question_plan": {
            "family": "cross_entity_comparison",
            "tickers": ["HBC"],
            "years": [2016, 2020],
            "scope": "separate",
            "operation_ast": {"op": "subtract", "args": ["x_new", "x_old"]},
            "operands": [],
        },
    }
    tables = [
        _table(
            uid="hbc-2016-provision",
            ticker="HBC",
            year=2016,
            scope="separate",
            kind="financial_note",
            headers=["Nhãn dòng", "2016 VND"],
            rows=[
                ["Nhãn dòng", "2016 VND"],
                ["6. Dự phòng phải thu ngắn hạn khó đòi 6, 7, 8", "100"],
            ],
        ),
        _table(
            uid="hbc-2020-provision",
            ticker="HBC",
            year=2020,
            scope="separate",
            kind="financial_note",
            headers=["Nhãn dòng", "2020 VND"],
            rows=[
                ["Nhãn dòng", "2020 VND"],
                ["6. Dự phòng phải thu ngắn hạn khó đòi 6, 8", "80"],
            ],
        ),
    ]

    result = builder.source_first_temporal_answer(
        item,
        tables_by_pair={
            (table["ticker"], table["report_year"]): [table]
            for table in tables
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("-20")
    assert builder._temporal_row_signature(
        "6. Dự phòng phải thu ngắn hạn khó đòi 6, 7, 8"
    ) == builder._temporal_row_signature(
        "6. Dự phòng phải thu ngắn hạn khó đòi 6, 8"
    )


def test_temporal_source_guard_quarantines_unqualified_current_tax_row():
    builder = _builder_module()
    result = {
        "selection": {
            "source_first_match_mode": "exact_contiguous",
            "source_report_year": 2019,
            "row_label": "15. Chi phí thuế TNDN hiện hành",
        }
    }

    assert not builder._temporal_source_result_is_safe(
        result,
        metric="Chi phí thuế TNDN",
        required_metric="Chi phí thuế TNDN",
        question="Tỷ lệ biến động chi phí thuế TNDN của NLG giữa năm 2016 và năm 2019 là bao nhiêu %?",
        requested_year=2019,
    )

    assert builder._temporal_source_result_is_safe(
        result,
        metric="Chi phí thuế TNDN hiện hành",
        required_metric="Chi phí thuế TNDN hiện hành",
        question="Tỷ lệ biến động chi phí thuế TNDN hiện hành của NLG giữa năm 2016 và năm 2019 là bao nhiêu %?",
        requested_year=2019,
    )


def test_temporal_cross_entity_route_keeps_source_priority():
    builder = _builder_module()

    assert builder._route_priority("source_first_temporal_cross_entity_v1") == 92.0
    assert builder._route_priority("source_first_temporal_cross_entity_v1") > builder._route_priority(
        "semantic_cell_heuristic"
    )


def test_reclassified_direct_lane_is_family_based_and_rejects_population_count():
    builder = _builder_module()
    item = {
        "question": "Tổng cộng tài sản của ABC cuối năm 2022 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["ABC"],
            "years": [2022],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }

    assert builder._reclassified_direct_metric(item) == (
        "total_assets",
        "Tổng cộng tài sản",
    )

    population_item = {
        "question": (
            "Có bao nhiêu trong số các ngân hàng A, B và C có lãi thuần dương?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["ABC"],
            "years": [2022],
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    assert builder._reclassified_direct_metric(population_item) is None


def test_reclassified_direct_lane_replays_inferred_consolidated_total():
    builder = _builder_module()
    item = {
        "question": "Tổng cộng tài sản của ABC cuối năm 2022 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["ABC"],
            "years": [2022],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
            "operands": [],
        },
    }
    table = _table(
        uid="abc-total-assets",
        ticker="ABC",
        year=2022,
        scope="consolidated",
        kind="balance_sheet",
        headers=["Mã số", "CHỈ TIÊU", "Thuyết minh", "Số cuối năm VND"],
        rows=[
            ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Số cuối năm VND"],
            ["270", "TỔNG CỘNG TÀI SẢN", "", "2.000.000.000"],
        ],
    )
    result = builder.source_first_reclassified_direct_answer(
        item,
        tables_by_pair={("ABC", 2022): [table]},
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_reclassified_direct_v1"
    assert result["answer"] == builder.Decimal("2")
    assert result["selection"]["reclassified_direct_scope"] == "consolidated"
    assert result["selection"]["reclassified_direct_scope_inferred"] is True
    assert result["selection"]["promotion_allowed"] is False


def test_composed_total_lane_replays_two_same_table_components():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng cộng dự phòng phải trả cuối năm 2020 của Tập đoàn Dệt May "
            "Việt Nam (VGT) là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["VGT"],
            "years": [2020],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    table = _table(
        uid="vgt-provision-components",
        ticker="VGT",
        year=2020,
        scope="consolidated",
        kind="balance_sheet",
        headers=["Mã số", "CHỈ TIÊU", "Thuyết minh", "Số cuối năm VND"],
        rows=[
            ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Số cuối năm VND"],
            ["321", "Dự phòng phải trả ngắn hạn", "", "5.634.013.216"],
            ["322", "Dự phòng phải trả dài hạn", "", "26.953.510.440"],
        ],
    )

    result = builder.source_first_composed_total_answer(
        item,
        tables_by_pair={("VGT", 2020): [table]},
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_composed_total_v1"
    assert result["answer"] == builder.Decimal("32.587523656")
    assert [source["role"] for source in result["sources"]] == [
        "composed_component_1",
        "composed_component_2",
    ]
    assert {source["internal_table_uid"] for source in result["sources"]} == {
        "vgt-provision-components"
    }
    assert result["selection"]["promotion_allowed"] is False


def test_composed_total_lane_rejects_a_single_component_question():
    builder = _builder_module()
    item = {
        "question": "Tổng cộng dự phòng phải trả ngắn hạn của VGT năm 2020 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["VGT"],
            "years": [2020],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }

    assert builder._composed_total_metric_spec(item) is None


def test_reclassified_financial_liability_total_uses_current_maturity_total_column():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng cộng nghĩa vụ nợ tài chính của Tập đoàn Bảo Việt (BVH) "
            "đến ngày 31 tháng 12 năm 2019 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["BVH"],
            "years": [2019],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    table = _table(
        uid="bvh-financial-liability-maturity-2019",
        ticker="BVH",
        year=2019,
        scope="consolidated",
        kind="financial_note",
        headers=[
            "Tại ngày 31 tháng 12 năm 2019",
            "Quá hạn",
            "Không xác định ký hạn",
            "Đến 01 năm",
            "Từ 01 - 05 năm",
            "Trên 05 năm",
            "Đơn vị: triệu đồng Tổng cộng",
        ],
        rows=[
            [
                "Tại ngày 31 tháng 12 năm 2019",
                "Quá hạn",
                "Không xác định ký hạn",
                "Đến 01 năm",
                "Từ 01 - 05 năm",
                "Trên 05 năm",
                "Đơn vị: triệu đồng Tổng cộng",
            ],
            ["NỢ TÀI CHÍNH", "", "", "", "", "", ""],
            ["Nghĩa vụ nợ theo hợp đồng bảo hiểm", "10.641", "-", "11.836.096", "(23.549.138)", "185.755.155", "154.307.993"],
            ["Phải trả hoạt động bảo hiểm gốc", "-", "-", "1.313.143", "-", "-", "1.313.143"],
            ["Phải trả tái bảo hiểm", "-", "-", "1.934.910", "-", "-", "1.934.910"],
            ["Các nghĩa vụ nợ tài chính khác", "-", "-", "16.496.708", "-", "-", "16.496.708"],
            ["Nhận ký quỹ", "-", "-", "222.129", "-", "-", "222.129"],
            ["Khác", "-", "-", "16.274.579", "-", "-", "16.274.579"],
            ["TỔNG CỘNG", "10.641", "-", "11.836.096", "(23.549.138)", "185.755.155", "174.052.754"],
        ],
    )
    table["table_section"] = {
        "kind": "liability",
        "label": "Nợ phải trả",
        "matched_evidence": "phải trả",
    }

    result = builder.source_first_reclassified_direct_answer(
        item,
        tables_by_pair={("BVH", 2019): [table]},
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_reclassified_direct_v1"
    assert result["answer"] == builder.Decimal("174052754")
    assert result["selection"]["source_first_match_mode"] == (
        "contextual_financial_liability_maturity_total"
    )
    assert result["selection"]["period_selection_mode"] == "contextual_total_column"
    assert result["selection"]["row_index"] == 8
    assert result["selection"]["column_index"] == 6
    assert result["selection"]["promotion_allowed"] is False


def test_reclassified_financial_liability_total_rejects_prior_year_header():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng cộng nghĩa vụ nợ tài chính của Tập đoàn Bảo Việt (BVH) "
            "đến ngày 31 tháng 12 năm 2019 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["BVH"],
            "years": [2019],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    table = _table(
        uid="bvh-financial-liability-maturity-prior",
        ticker="BVH",
        year=2019,
        scope="consolidated",
        kind="financial_note",
        headers=[
            "Tại ngày 31 tháng 12 năm 2018",
            "Quá hạn",
            "Không xác định ký hạn",
            "Đến 01 năm",
            "Từ 01 - 05 năm",
            "Trên 05 năm",
            "Đơn vị: triệu đồng Tổng cộng",
        ],
        rows=[
            [
                "Tại ngày 31 tháng 12 năm 2018",
                "Quá hạn",
                "Không xác định ký hạn",
                "Đến 01 năm",
                "Từ 01 - 05 năm",
                "Trên 05 năm",
                "Đơn vị: triệu đồng Tổng cộng",
            ],
            ["TỔNG CỘNG", "14.835", "-", "15.157.812", "(19.614.511)", "148.795.084", "159.333.220"],
        ],
    )

    result = builder.source_first_reclassified_direct_answer(
        item,
        tables_by_pair={("BVH", 2019): [table]},
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is None


def test_reclassified_financial_liability_total_rejects_asset_maturity_table():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng cộng nghĩa vụ nợ tài chính của Tập đoàn Bảo Việt (BVH) "
            "đến ngày 31 tháng 12 năm 2019 là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["BVH"],
            "years": [2019],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    table = _table(
        uid="bvh-financial-asset-maturity-2019",
        ticker="BVH",
        year=2019,
        scope="consolidated",
        kind="financial_note",
        headers=[
            "Tại ngày 31 tháng 12 năm 2019",
            "Quá hạn",
            "Không xác định kỳ hạn",
            "Đến 01 năm",
            "Từ 01 - 05 năm",
            "Trên 05 năm",
            "Tổng cộng",
        ],
        rows=[
            [
                "Tại ngày 31 tháng 12 năm 2019",
                "Quá hạn",
                "Không xác định kỳ hạn",
                "Đến 01 năm",
                "Từ 01 - 05 năm",
                "Trên 05 năm",
                "Tổng cộng",
            ],
            ["TÀI SẢN TÀI CHÍNH", "", "", "", "", "", ""],
            ["Tiền và các khoản tương đương tiền", "-", "-", "4.742.602", "-", "-", "4.742.602"],
            ["TỔNG CỘNG", "73.401", "2.916.856", "81.783.522", "22.559.513", "54.169.486", "161.502.778"],
        ],
    )
    table["context_trace"] = {
        "source_title": (
            "Bảng tóm tắt thời gian đáo hạn theo hợp đồng của các tài sản "
            "tài chính trên cơ sở chưa chiết khấu"
        )
    }

    result = builder.source_first_reclassified_direct_answer(
        item,
        tables_by_pair={("BVH", 2019): [table]},
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is None


def test_multi_entity_threshold_replays_all_aliases_and_requires_scope_invariance():
    builder = _builder_module()
    item = {
        "question": (
            "Có bao nhiêu trong số các ngân hàng TMCP Á Châu, TMCP Quân đội, "
            "TMCP Xuất nhập khẩu Việt Nam và TMCP Đầu tư và Phát triển Việt Nam "
            "có lãi thuần từ hoạt động kinh doanh ngoại hối dương trong năm 2025 "
            "lớn hơn 1 nghìn tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["ACB"],
            "years": [2025],
            "scope": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    values = {
        "ACB": ("1.731.886", "1.731.300"),
        "MBB": ("1.756.922", "1.758.592"),
        "EIB": ("580.096", "580.096"),
        "BID": ("3.791.593", "3.718.008"),
    }
    tables = []
    for ticker, (consolidated, separate) in values.items():
        for scope, value in (("consolidated", consolidated), ("separate", separate)):
            tables.append(
                _table(
                    uid=f"{ticker.lower()}-forex-{scope}",
                    ticker=ticker,
                    year=2025,
                    scope=scope,
                    kind="income_statement",
                    headers=[
                        "Mã số",
                        "CHỈ TIÊU",
                        "Thuyết minh",
                        "Năm 2025 Triệu VND",
                        "Năm 2024 Triệu VND",
                    ],
                    rows=[
                        [
                            "Mã số",
                            "CHỈ TIÊU",
                            "Thuyết minh",
                            "Năm 2025 Triệu VND",
                            "Năm 2024 Triệu VND",
                        ],
                        ["III", "Lãi thuần từ hoạt động kinh doanh ngoại hối", "26", value, "0"],
                    ],
                    source_title=(
                        "Báo cáo kết quả hoạt động kinh doanh "
                        + ("hợp nhất" if scope == "consolidated" else "riêng")
                    ),
                )
            )
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_multi_entity_threshold_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("3")
    assert result["replayed_tickers"] == ["ACB", "MBB", "EIB", "BID"]
    assert result["scope_invariance"]["consolidated"] == result["scope_invariance"]["separate"]
    assert result["scope_invariance"]["consolidated"] == {
        "ACB": True,
        "MBB": True,
        "EIB": False,
        "BID": True,
    }
    assert [source["role"] for source in result["sources"]] == [
        "entity_ACB",
        "entity_MBB",
        "entity_EIB",
        "entity_BID",
    ]
    assert result["tier"] == "source_first_multi_entity_threshold_v1"

    values["EIB"] = ("580.096", "2.000.000")
    divergent_tables = []
    for ticker, (consolidated, separate) in values.items():
        for scope, value in (("consolidated", consolidated), ("separate", separate)):
            divergent_tables.append(
                _table(
                    uid=f"{ticker.lower()}-forex-divergent-{scope}",
                    ticker=ticker,
                    year=2025,
                    scope=scope,
                    kind="income_statement",
                    headers=[
                        "Mã số",
                        "CHỈ TIÊU",
                        "Thuyết minh",
                        "Năm 2025 Triệu VND",
                        "Năm 2024 Triệu VND",
                    ],
                    rows=[
                        [
                            "Mã số",
                            "CHỈ TIÊU",
                            "Thuyết minh",
                            "Năm 2025 Triệu VND",
                            "Năm 2024 Triệu VND",
                        ],
                        ["III", "Lãi thuần từ hoạt động kinh doanh ngoại hối", "26", value, "0"],
                    ],
                )
            )
    divergent_by_pair = {}
    for table in divergent_tables:
        divergent_by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    assert (
        builder.source_first_multi_entity_threshold_answer(
            item,
            tables_by_pair=divergent_by_pair,
            resolver_kwargs={
                "parse_decimal": builder.parse_decimal,
                "candidate_evidence_window": builder.candidate_evidence_window,
                "choose_year_column": builder.choose_year_column,
                "source_multiplier": builder.source_multiplier,
                "requested_divisor": builder.requested_divisor,
                "report_year_neighbor_fallback": None,
            },
        )
        is None
    )


def test_multi_entity_direct_aggregation_replays_each_tax_row_without_ticker_contamination():
    builder = _builder_module()
    item = {
        "question": (
            "Số tiền trung bình thuế và các khoản phải nộp Nhà nước cuối năm "
            "của công ty mẹ SNZ và VIC năm 2019 là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["SNZ", "VIC"],
            "years": [2019],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "mean", "args": ["values"]},
        },
    }
    tables = [
        _table(
            uid="snz-tax-2019",
            ticker="SNZ",
            year=2019,
            scope="separate",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2019 VND"],
            rows=[
                ["Nhãn dòng", "2019 VND"],
                ["3. Thuế và các khoản phải nộp Nhà nước", "15.344.409.381"],
            ],
            source_title="Bảng cân đối kế toán riêng",
        ),
        _table(
            uid="vic-tax-2019",
            ticker="VIC",
            year=2019,
            scope="separate",
            kind="balance_sheet",
            headers=["Nhãn dòng", "2019 VND"],
            rows=[
                ["Nhãn dòng", "2019 VND"],
                ["3. Thuế và các khoản phải nộp Nhà nước", "2.050.099.000.000"],
            ],
            source_title="Bảng cân đối kế toán riêng",
        ),
    ]
    by_pair = {(table["ticker"], table["report_year"]): [table] for table in tables}
    result = builder.source_first_multi_entity_direct_aggregation_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_multi_entity_direct_aggregation_v1"
    assert result["answer"] == builder.Decimal("1032.7217046905")
    assert [source["role"] for source in result["sources"]] == [
        "entity_SNZ",
        "entity_VIC",
    ]
    assert [source["ticker"] for source in result["sources"]] == ["SNZ", "VIC"]
    assert {source["internal_table_uid"] for source in result["sources"]} == {
        "snz-tax-2019",
        "vic-tax-2019",
    }


def test_multi_entity_direct_aggregation_accepts_lc_ocr_aliases_per_issuer():
    builder = _builder_module()
    item = {
        "question": (
            "Giá trị trung bình số dư cam kết L/C tính đến ngày 31/12/2024 "
            "của công ty mẹ ABB và SSB là bao nhiêu triệu đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["ABB", "SSB"],
            "years": [2024],
            "scope": "separate",
            "requested_unit": "million_vnd",
            "operation_ast": {"op": "mean", "args": ["values"]},
        },
    }
    tables = [
        _table(
            uid="abb-lc-2024",
            ticker="ABB",
            year=2024,
            scope="separate",
            kind="balance_sheet",
            headers=["Nhãn dòng", "31/12/2024 Triệu VND"],
            rows=[
                ["Nhãn dòng", "31/12/2024 Triệu VND"],
                ["Cam kết trong nghiệp vụ thư tín dụng", "1.634.376"],
            ],
            source_title="Các chỉ tiêu ngoài báo cáo tình hình tài chính",
        ),
        _table(
            uid="ssb-lc-2024",
            ticker="SSB",
            year=2024,
            scope="separate",
            kind="balance_sheet",
            headers=["Nhãn dòng", "31/12/2024 Triệu VND"],
            rows=[
                ["Nhãn dòng", "31/12/2024 Triệu VND"],
                ["Cam kết trong nghiệp vụ L/C", "2.228.158"],
            ],
            source_title="Các chỉ tiêu ngoài báo cáo tình hình tài chính",
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    result = builder.source_first_multi_entity_direct_aggregation_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["multi_entity_direct_kind"] == "lc_commitment"
    assert result["answer"] == builder.Decimal("1931267")
    assert [source["internal_table_uid"] for source in result["sources"]] == [
        "abb-lc-2024",
        "ssb-lc-2024",
    ]
    assert all(
        source["multi_entity_direct_value_policy"] == "source_signed"
        for source in result["sources"]
    )


def test_multi_entity_direct_aggregation_retries_past_audit_duplicate_for_selling_expense():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng giá trị chi phí bán hàng của công ty mẹ SAB và DBC "
            "vào năm 2017 là mấy nghìn tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["SAB", "DBC"],
            "years": [2017],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "sum", "args": ["values"]},
        },
    }
    tables = [
        _table(
            uid="sab-selling-primary-2017",
            ticker="SAB",
            year=2017,
            scope="separate",
            kind="income_statement",
            headers=["Mã số", "Chỉ tiêu", "2017 VND"],
            rows=[
                ["Mã số", "Chỉ tiêu", "2017 VND"],
                ["25", "Chi phí bán hàng", "(1.446.841.604.384)"],
            ],
            source_title="Báo cáo kết quả hoạt động kinh doanh riêng",
        ),
        _table(
            uid="sab-selling-audit-2017",
            ticker="SAB",
            year=2017,
            scope="separate",
            kind="income_statement",
            headers=["Chỉ tiêu", "Năm nay (Tự lập)", "Năm nay (Kiểm toán)"],
            rows=[
                ["Chỉ tiêu", "Năm nay (Tự lập)", "Năm nay (Kiểm toán)"],
                ["Chi phí bán hàng", "1.408.080.686.788", "1.446.841.604.384"],
            ],
            source_title="Giải trình số liệu tự lập và kiểm toán",
        ),
        _table(
            uid="dbc-selling-primary-2017",
            ticker="DBC",
            year=2017,
            scope="separate",
            kind="income_statement",
            headers=["Mã số", "Chỉ tiêu", "2017 VND"],
            rows=[
                ["Mã số", "Chỉ tiêu", "2017 VND"],
                ["8", "Chi phí bán hàng", "(83.645.537.443)"],
            ],
            source_title="Báo cáo kết quả hoạt động kinh doanh riêng",
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)
    result = builder.source_first_multi_entity_direct_aggregation_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("1.530487141827")
    assert [source["internal_table_uid"] for source in result["sources"]] == [
        "sab-selling-primary-2017",
        "dbc-selling-primary-2017",
    ]
    assert [source["value"] for source in result["sources"]] == [
        builder.Decimal("1.446841604384"),
        builder.Decimal("0.083645537443"),
    ]
    assert all(
        source["multi_entity_direct_value_policy"] == "expense_magnitude_abs"
        for source in result["sources"]
    )


def test_multi_entity_direct_aggregation_fails_closed_when_one_entity_is_missing():
    builder = _builder_module()
    item = {
        "question": (
            "Số tiền trung bình thuế và các khoản phải nộp Nhà nước cuối năm "
            "của công ty mẹ SNZ và VIC năm 2019 là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["SNZ", "VIC"],
            "years": [2019],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "mean", "args": ["values"]},
        },
    }
    table = _table(
        uid="snz-tax-only-2019",
        ticker="SNZ",
        year=2019,
        scope="separate",
        kind="balance_sheet",
        headers=["Nhãn dòng", "2019 VND"],
        rows=[
            ["Nhãn dòng", "2019 VND"],
            ["Thuế và các khoản phải nộp Nhà nước", "15.344.409.381"],
        ],
    )

    assert (
        builder.source_first_multi_entity_direct_aggregation_answer(
            item,
            tables_by_pair={("SNZ", 2019): [table]},
            resolver_kwargs={
                "parse_decimal": builder.parse_decimal,
                "candidate_evidence_window": builder.candidate_evidence_window,
                "choose_year_column": builder.choose_year_column,
                "source_multiplier": builder.source_multiplier,
                "requested_divisor": builder.requested_divisor,
                "report_year_neighbor_fallback": None,
            },
        )
        is None
    )


def test_multi_entity_direct_aggregation_uses_expense_magnitude_for_credit_provision():
    builder = _builder_module()
    item = {
        "question": (
            "Giá trị trung bình của chi phí trích lập dự phòng rủi ro tín dụng "
            "năm 2024 ở mức công ty mẹ MSB, BID và ABB là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["MSB", "BID", "ABB"],
            "years": [2024],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "mean", "args": ["values"]},
        },
    }
    tables = [
        _table(
            uid="msb-provision-2024",
            ticker="MSB",
            year=2024,
            scope="separate",
            kind="income_statement",
            headers=["Chỉ tiêu", "2024 VND"],
            rows=[
                ["Chỉ tiêu", "2024 VND"],
                ["Chi phí dự phòng rủi ro tín dụng", "(1.924.445.000.000)"],
            ],
            source_title="Báo cáo kết quả hoạt động kinh doanh riêng",
        ),
        _table(
            uid="bid-provision-2024",
            ticker="BID",
            year=2024,
            scope="separate",
            kind="income_statement",
            headers=["Chỉ tiêu", "2024 VND"],
            rows=[
                ["Chỉ tiêu", "2024 VND"],
                ["X. Chi phí dự phòng rủi ro tín dụng", "(20.606.172.000.000)"],
            ],
            source_title="Báo cáo kết quả hoạt động kinh doanh riêng",
        ),
        _table(
            uid="abb-provision-2024",
            ticker="ABB",
            year=2024,
            scope="separate",
            kind="income_statement",
            headers=["Chỉ tiêu", "2024 VND"],
            rows=[
                ["Chỉ tiêu", "2024 VND"],
                ["X Chi phí dự phòng rủi ro tín dụng", "(1.411.791.000.000)"],
            ],
            source_title="Báo cáo kết quả hoạt động kinh doanh riêng",
        ),
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    result = builder.source_first_multi_entity_direct_aggregation_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal(
        "7980.8026666666666666666666666666666667"
    )
    assert result["diagnostics"]["value_policy"] == "expense_magnitude_abs"


def test_multi_entity_direct_aggregation_infers_sum_and_parent_company_cash_flow_column():
    builder = _builder_module()
    item = {
        "question": (
            "Tổng lưu chuyển tiền thuần từ hoạt động kinh doanh năm 2015 của "
            "CTCP Masan High-Tech Materials công ty mẹ, CTCP Tập đoàn Hòa Phát "
            "công ty mẹ, CTCP Nhựa An Phát Xanh công ty mẹ và CTCP - Tổng công ty "
            "Phân bón Dầu khí Cà Mau công ty mẹ là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["MSR", "HPG", "AAA", "DCM"],
            "years": [2015],
            "scope": "separate",
            "requested_unit": "billion_vnd",
            # The corpus planner currently mislabels this simple total as
            # count.  The route may infer sum only from the explicit wording.
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }
    tables = [
        _table(
            uid="msr-cfo-2015-split",
            ticker="MSR",
            year=2015,
            scope="separate",
            kind="cash_flow_statement",
            headers=[
                "Nhãn dòng",
                "Mã số",
                "Tập đoàn",
                "2014Nghìn VND",
                "Công ty",
                "2014Nghìn VND",
            ],
            rows=[
                ["", "Mã số", "Tập đoàn", "", "Công ty", ""],
                [
                    "",
                    "",
                    "2015Nghìn VND",
                    "2014Nghìn VND",
                    "2015Nghìn VND",
                    "2014Nghìn VND",
                ],
                ["LƯU CHUYỂN TIỀN TỪ HOẠT ĐỘNG KINH DOANH", "", "", "", "", ""],
                [
                    "Lưu chuyển tiền thuần từhoạt động kinh doanh",
                    "20",
                    "41.706.084",
                    "(538.487.539)",
                    "(417.067.012)",
                    "4.873.904",
                ],
            ],
            source_title="Báo cáo lưu chuyển tiền tệ riêng",
        ),
        _table(
            uid="hpg-cfo-2015",
            ticker="HPG",
            year=2015,
            scope="separate",
            kind="cash_flow_statement",
            headers=["Nhãn dòng", "Mã số", "2015 VND", "2014 VND"],
            rows=[
                ["Nhãn dòng", "Mã số", "2015 VND", "2014 VND"],
                ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", "(45.061.179.652)", "-"],
            ],
        ),
        _table(
            uid="aaa-cfo-2015",
            ticker="AAA",
            year=2015,
            scope="separate",
            kind="cash_flow_statement",
            headers=["Nhãn dòng", "Mã số", "2015 VND", "2014 VND"],
            rows=[
                ["Nhãn dòng", "Mã số", "2015 VND", "2014 VND"],
                ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", "29.663.853.542", "-"],
            ],
        ),
        _table(
            uid="dcm-cfo-2015",
            ticker="DCM",
            year=2015,
            scope="separate",
            kind="cash_flow_statement",
            headers=["Nhãn dòng", "Mã số", "2015 VND", "2014 VND"],
            rows=[
                ["Nhãn dòng", "Mã số", "2015 VND", "2014 VND"],
                ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", "326.395.298.043", "-"],
            ],
        ),
    ]
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    result = builder.source_first_multi_entity_direct_aggregation_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["operation"] == "sum"
    assert result["answer"] == builder.Decimal("-106.069040067")
    msr = next(source for source in result["sources"] if source["ticker"] == "MSR")
    assert msr["column_index"] == 4
    assert msr["raw_value_decimal"] == "-417067012"


def test_multi_entity_conditional_count_replays_positive_cash_flow_per_issuer():
    builder = _builder_module()
    item = {
        "question": (
            "Có bao nhiêu công ty trong số công ty mẹ CTCP Tập đoàn Hoa Sen, "
            "công ty mẹ CTCP Tập đoàn Hòa Phát và công ty mẹ CTCP Masan "
            "High-Tech Materials có lưu chuyển tiền thuần từ hoạt động kinh "
            "doanh dương trong năm 2022?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HSG", "HPG", "MSR"],
            "years": [2022],
            "scope": "separate",
            "operation_ast": {"op": "plan_required", "args": []},
        },
    }
    tables = [
        _table(
            uid="hsg-cfo-2022",
            ticker="HSG",
            year=2022,
            scope="separate",
            kind="cash_flow_statement",
            headers=["Nhãn dòng", "2022 VND", "2021 VND"],
            rows=[
                ["Nhãn dòng", "2022 VND", "2021 VND"],
                [
                    "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
                    "1.071.767.875.098",
                    "369.579.964.422",
                ],
            ],
        ),
        _table(
            uid="hpg-cfo-2022",
            ticker="HPG",
            year=2022,
            scope="separate",
            kind="cash_flow_statement",
            headers=["Nhãn dòng", "2022 VND", "2021 VND"],
            rows=[
                ["Nhãn dòng", "2022 VND", "2021 VND"],
                [
                    "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
                    "(761.380.984.482)",
                    "(611.601.249.012)",
                ],
            ],
        ),
        _table(
            uid="msr-cfo-2022",
            ticker="MSR",
            year=2022,
            scope="separate",
            kind="cash_flow_statement",
            headers=["Nhãn dòng", "2022 Nghìn VND", "2021 Nghìn VND"],
            rows=[
                ["Nhãn dòng", "2022 Nghìn VND", "2021 Nghìn VND"],
                [
                    "Lưu chuyển tiền thuần từ hoạt độngkinh doanh",
                    "(259.020.090)",
                    "(300.715.703)",
                ],
            ],
        ),
    ]
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_multi_entity_conditional_count_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_multi_entity_conditional_count_v1"
    assert result["operation"] == "count_positive"
    assert result["answer"] == builder.Decimal("1")
    assert result["positive_tickers"] == ["HSG"]
    assert [source["ticker"] for source in result["sources"]] == [
        "HSG",
        "HPG",
        "MSR",
    ]
    assert [source["multi_entity_conditional_predicate_passed"] for source in result["sources"]] == [
        True,
        False,
        False,
    ]


def test_multi_entity_conditional_count_rejects_threshold_variant():
    builder = _builder_module()
    item = {
        "question": (
            "Có bao nhiêu công ty mẹ HSG, HPG và MSR có lưu chuyển tiền thuần "
            "từ hoạt động kinh doanh dương lớn hơn 1 tỷ đồng trong năm 2022?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["HSG", "HPG", "MSR"],
            "years": [2022],
            "scope": "separate",
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
        },
    }

    assert builder._multi_entity_conditional_count_spec(item) is None


def test_multi_entity_conditional_count_accepts_compiled_conditional_family():
    builder = _builder_module()
    item = {
        "question": (
            "Có bao nhiêu công ty mẹ HSG, HPG và MSR có lưu chuyển tiền thuần "
            "từ hoạt động kinh doanh dương trong năm 2022?"
        ),
        "question_plan": {
            "family": "conditional_analytical",
            "tickers": ["HSG", "HPG", "MSR"],
            "years": [2022],
            "scope": "separate",
            "operation_ast": {"op": "plan_required", "args": []},
        },
    }

    assert builder._multi_entity_conditional_count_spec(item) == (
        "operating_cash_flow",
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
        "cash_flow_statement",
        "flow",
    )


def test_ticker_alias_registry_preserves_trailing_digit_tickers():
    builder = _builder_module()
    aliases = builder.load_ticker_aliases(
        Path(__file__).resolve().parents[2] / "data/ViFinQA/code_stock.csv"
    )

    assert aliases["ht1"] == "HT1"
    assert aliases["ctcp xi măng vicem hà tiên"] == "HT1"
    assert builder.extract_tickers(
        "CTCP Xi Măng Vicem Hà Tiên và CTCP Tập Đoàn PC1", aliases
    ) == ["HT1", "PC1"]


def test_multi_entity_ratio_selector_rejects_incomplete_recovered_issuer_list():
    builder = _builder_module()
    item = {
        "question": (
            "Trong năm 2019, trong số CTCP Lọc hóa dầu Bình Sơn, Tập đoàn "
            "Xăng dầu Việt Nam và Tổng CTCP Vận tải Dầu khí, công ty có tỷ lệ "
            "nợ phải trả trên vốn chủ sở hữu cao nhất có tỷ lệ giữa tổng lợi "
            "nhuận kế toán trước thuế cộng chi phí lãi vay và chi phí lãi vay "
            "là bao nhiêu lần?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["PLX"],
            "years": [2019],
            "scope": None,
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }

    assert builder._multi_entity_ratio_selector_spec(item) is not None
    assert builder._multi_entity_selector_tickers(item) is None


def test_multi_entity_selector_replays_unique_winner_then_output_metric():
    builder = _builder_module()
    item = {
        "question": (
            "Trong năm 2023, tổng chi phí thuế thu nhập doanh nghiệp hiện hành "
            "của doanh nghiệp có tổng vốn chủ sở hữu cuối năm cao nhất trong "
            "BSR và PLX là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["BSR", "PLX"],
            "years": [2023],
            "scope": None,
            "requested_unit": "billion_vnd",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    tables = []
    for scope in ("consolidated", "separate"):
        tables.extend(
            [
                _table(
                    uid=f"bsr-equity-{scope}",
                    ticker="BSR",
                    year=2023,
                    scope=scope,
                    kind="balance_sheet",
                    headers=["Nhãn dòng", "Số cuối năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "2023 VND"],
                        ["D. VỐN CHỦ SỞ HỮU", "1.000.000.000.000"],
                    ],
                ),
                _table(
                    uid=f"plx-equity-{scope}",
                    ticker="PLX",
                    year=2023,
                    scope=scope,
                    kind="balance_sheet",
                    headers=["Nhãn dòng", "Số cuối năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "2023 VND"],
                        ["D. VỐN CHỦ SỞ HỮU", "2.000.000.000.000"],
                    ],
                ),
                _table(
                    uid=f"bsr-tax-{scope}",
                    ticker="BSR",
                    year=2023,
                    scope=scope,
                    kind="income_statement",
                    headers=["Nhãn dòng", "Năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "2023 VND"],
                        [
                            "Chi phí thuế thu nhập doanh nghiệp hiện hành",
                            "10.000.000.000",
                        ],
                    ],
                ),
                _table(
                    uid=f"plx-tax-{scope}",
                    ticker="PLX",
                    year=2023,
                    scope=scope,
                    kind="income_statement",
                    headers=["Nhãn dòng", "Năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "2023 VND"],
                        [
                            "Chi phí thuế thu nhập doanh nghiệp hiện hành",
                            "20.000.000.000",
                        ],
                    ],
                ),
            ]
        )
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_multi_entity_selector_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("20")
    assert result["selected_ticker"] == "PLX"
    assert result["scope_invariance"]["winner"] == {
        "consolidated": "PLX",
        "separate": "PLX",
    }
    assert [source["role"] for source in result["sources"]] == [
        "selector_BSR",
        "selector_PLX",
        "selected_output",
    ]
    assert result["sources"][-1]["source_first_scope"] == "consolidated"


def test_multi_entity_selector_rejects_scope_dependent_selector_winner():
    builder = _builder_module()
    item = {
        "question": (
            "Trong năm 2023, tổng chi phí thuế thu nhập doanh nghiệp hiện hành "
            "của doanh nghiệp có tổng vốn chủ sở hữu cuối năm cao nhất trong "
            "BSR và PLX là bao nhiêu tỷ đồng?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["BSR", "PLX"],
            "years": [2023],
            "scope": None,
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    tables = []
    for scope, bsr_equity, plx_equity in (
        ("consolidated", "2.000.000.000.000", "1.000.000.000.000"),
        ("separate", "1.000.000.000.000", "2.000.000.000.000"),
    ):
        for ticker, equity in (("BSR", bsr_equity), ("PLX", plx_equity)):
            tables.append(
                _table(
                    uid=f"{ticker}-equity-{scope}",
                    ticker=ticker,
                    year=2023,
                    scope=scope,
                    kind="balance_sheet",
                    headers=["Nhãn dòng", "Số cuối năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "2023 VND"],
                        ["D. VỐN CHỦ SỞ HỮU", equity],
                    ],
                )
            )
            tables.append(
                _table(
                    uid=f"{ticker}-tax-{scope}",
                    ticker=ticker,
                    year=2023,
                    scope=scope,
                    kind="income_statement",
                    headers=["Nhãn dòng", "Năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "2023 VND"],
                        [
                            "Chi phí thuế thu nhập doanh nghiệp hiện hành",
                            "10.000.000.000",
                        ],
                    ],
                )
            )
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    assert (
        builder.source_first_multi_entity_selector_answer(
            item,
            tables_by_pair=by_pair,
            resolver_kwargs={
                "parse_decimal": builder.parse_decimal,
                "candidate_evidence_window": builder.candidate_evidence_window,
                "choose_year_column": builder.choose_year_column,
                "source_multiplier": builder.source_multiplier,
                "requested_divisor": builder.requested_divisor,
                "report_year_neighbor_fallback": None,
            },
        )
        is None
    )


def test_multi_entity_ratio_selector_replays_debt_equity_then_interest_coverage():
    builder = _builder_module()
    item = {
        "question": (
            "Năm 2023, trong nhóm BSR và PLX, công ty có hệ số nợ phải trả "
            "trên vốn chủ sở hữu cao nhất có hệ số khả năng thanh toán lãi vay "
            "là bao nhiêu lần?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["BSR", "PLX"],
            "years": [2023],
            "scope": None,
            "requested_unit": "times",
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    values = {
        "BSR": ("2.000.000.000.000", "4.000.000.000.000", "20.000.000.000", "4.000.000.000"),
        "PLX": ("3.000.000.000.000", "2.000.000.000.000", "30.000.000.000", "5.000.000.000"),
    }
    tables = []
    for scope in ("consolidated", "separate"):
        for ticker, (debt, equity, profit, interest) in values.items():
            tables.append(
                _table(
                    uid=f"{ticker}-ratio-balance-{scope}",
                    ticker=ticker,
                    year=2023,
                    scope=scope,
                    kind="balance_sheet",
                    headers=["Nhãn dòng", "Mã số", "Thuyết minh", "Số cuối năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "Mã số", "Thuyết minh", "Số cuối năm 2023 VND"],
                        ["C. NỢ PHẢI TRẢ", "300", "", debt],
                        ["D. VỐN CHỦ SỞ HỮU", "400", "", equity],
                    ],
                )
            )
            tables.append(
                _table(
                    uid=f"{ticker}-ratio-income-{scope}",
                    ticker=ticker,
                    year=2023,
                    scope=scope,
                    kind="income_statement",
                    headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm 2023 VND"],
                    rows=[
                        ["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm 2023 VND"],
                        ["Lợi nhuận kế toán trước thuế (50=30+40)", "50", "", profit],
                        ["Chi phí lãi vay", "23", "", interest],
                    ],
                )
            )
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    result = builder.source_first_multi_entity_ratio_selector_answer(
        item,
        tables_by_pair=by_pair,
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["tier"] == "source_first_multi_entity_ratio_selector_v1"
    assert result["answer"] == builder.Decimal("7")
    assert result["selected_ticker"] == "PLX"
    assert result["scope_invariance"]["winner"] == {
        "consolidated": "PLX",
        "separate": "PLX",
    }
    assert result["scope_invariance"]["output_values"]["consolidated"] == "7"
    assert result["scope_invariance"]["output_values"]["separate"] == "7"
    assert [source["role"] for source in result["sources"]] == [
        "selector_debt_BSR",
        "selector_equity_BSR",
        "selector_debt_PLX",
        "selector_equity_PLX",
        "selected_output_profit_before_tax",
        "selected_output_interest_expense",
    ]


def test_multi_entity_ratio_selector_rejects_scope_dependent_winner():
    builder = _builder_module()
    item = {
        "question": (
            "Năm 2023, trong nhóm BSR và PLX, công ty có tỷ lệ nợ phải trả "
            "trên vốn chủ sở hữu cao nhất có tỷ lệ tổng của lợi nhuận trước "
            "thuế và chi phí lãi vay trên chi phí lãi vay là bao nhiêu lần?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["BSR", "PLX"],
            "years": [2023],
            "scope": None,
            "operation_ast": {"op": "max", "args": ["values"]},
        },
    }
    tables = []
    selector_values = {
        "consolidated": {"BSR": ("2.000.000.000.000", "1.000.000.000.000"), "PLX": ("3.000.000.000.000", "2.000.000.000.000")},
        "separate": {"BSR": ("1.000.000.000.000", "2.000.000.000.000"), "PLX": ("4.000.000.000.000", "2.000.000.000.000")},
    }
    for scope, issuer_values in selector_values.items():
        for ticker, (debt, equity) in issuer_values.items():
            tables.append(
                _table(
                    uid=f"{ticker}-ratio-flip-balance-{scope}",
                    ticker=ticker,
                    year=2023,
                    scope=scope,
                    kind="balance_sheet",
                    headers=["Nhãn dòng", "Mã số", "Thuyết minh", "Số cuối năm 2023 VND"],
                    rows=[
                        ["Nhãn dòng", "Mã số", "Thuyết minh", "Số cuối năm 2023 VND"],
                        ["C. NỢ PHẢI TRẢ", "300", "", debt],
                        ["D. VỐN CHỦ SỞ HỮU", "400", "", equity],
                    ],
                )
            )
            tables.append(
                _table(
                    uid=f"{ticker}-ratio-flip-income-{scope}",
                    ticker=ticker,
                    year=2023,
                    scope=scope,
                    kind="income_statement",
                    headers=["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm 2023 VND"],
                    rows=[
                        ["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm 2023 VND"],
                        ["Lợi nhuận kế toán trước thuế", "50", "", "30.000.000.000"],
                        ["Chi phí lãi vay", "23", "", "5.000.000.000"],
                    ],
                )
            )
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    assert (
        builder.source_first_multi_entity_ratio_selector_answer(
            item,
            tables_by_pair=by_pair,
            resolver_kwargs={
                "parse_decimal": builder.parse_decimal,
                "candidate_evidence_window": builder.candidate_evidence_window,
                "choose_year_column": builder.choose_year_column,
                "source_multiplier": builder.source_multiplier,
                "requested_divisor": builder.requested_divisor,
                "report_year_neighbor_fallback": None,
            },
        )
        is None
    )


def test_multi_entity_share_threshold_replays_ocr_variants_for_parent_scope():
    builder = _builder_module()
    item = {
        "question": (
            "Có bao nhiêu công ty trong số CTCP Phát triển Bất động sản Văn Phú "
            "(VPI) công ty mẹ, CTCP Đầu tư Nam Long (NLG) công ty mẹ, "
            "CTCP Bluemarq Group (DXG) công ty mẹ và Tổng Công ty cổ phần "
            "Phát triển Khu Công nghiệp (SNZ) công ty mẹ có số lượng cổ phiếu "
            "đang lưu hành vượt 350 triệu cổ phiếu vào cuối năm 2021?"
        ),
        "question_plan": {
            "family": "conditional_analytical",
            "tickers": ["VPI"],
            "years": [2021],
            "scope": "separate",
            "operation_ast": {"op": "plan_required", "args": []},
        },
    }
    rows_by_ticker = {
        "VPI": ["Cổ phiếu đang lưu hành", "219.999.780", "199.999.900"],
        "NLG": [
            "Cổ phiếu đang lưu hànhCổ phiếu phổ thông",
            "382.940.013",
            "275.270.660",
        ],
        "DXG": [
            "Cổ phiếu đang lưu hànhCổ phiếu phổ thông",
            "596.025.562",
            "518.287.181",
        ],
        "SNZ": [
            "Số lượng cổ phiếu phổ thông đang lưu hành",
            "376.491.800",
            "376.491.800",
        ],
    }
    tables = [
        _table(
            uid=f"{ticker}-share-threshold",
            ticker=ticker,
            year=2021,
            scope="separate",
            kind="financial_note_detail",
            headers=["Nhãn dòng", "Số cuối năm(Cổ phiếu)", "Số đầu năm(Cổ phiếu)"],
            rows=[
                ["Nhãn dòng", "Số cuối năm(Cổ phiếu)", "Số đầu năm(Cổ phiếu)"],
                rows_by_ticker[ticker],
            ],
            source_title="Bảng chi tiết cổ phiếu",
        )
        for ticker in rows_by_ticker
    ]

    result = builder.source_first_multi_entity_share_threshold_answer(
        item,
        tables_by_pair={
            (table["ticker"], table["report_year"]): [table]
            for table in tables
        },
        resolver_kwargs={
            "parse_decimal": builder.parse_decimal,
            "candidate_evidence_window": builder.candidate_evidence_window,
            "choose_year_column": builder.choose_year_column,
            "source_multiplier": builder.source_multiplier,
            "requested_divisor": builder.requested_divisor,
            "report_year_neighbor_fallback": None,
        },
    )

    assert result is not None
    assert result["answer"] == builder.Decimal("3")
    assert result["positive_tickers"] == ["NLG", "DXG", "SNZ"]
    assert result["requested_scope"] == "separate"
    assert result["scope_invariance"] == {
        "separate": {"VPI": False, "NLG": True, "DXG": True, "SNZ": True}
    }
    assert [source["role"] for source in result["sources"]] == [
        "entity_VPI",
        "entity_NLG",
        "entity_DXG",
        "entity_SNZ",
    ]
    assert result["tier"] == "source_first_multi_entity_share_threshold_v1"


def test_multi_entity_share_threshold_rejects_scope_dependent_predicate():
    builder = _builder_module()
    item = {
        "question": (
            "Có bao nhiêu công ty trong số CTCP Phát triển Bất động sản Văn Phú "
            "(VPI) và CTCP Đầu tư Nam Long (NLG) có số lượng cổ phiếu đang "
            "lưu hành vượt 350 triệu cổ phiếu vào cuối năm 2021?"
        ),
        "question_plan": {
            "family": "conditional_analytical",
            "tickers": ["VPI", "NLG"],
            "years": [2021],
            "scope": None,
            "operation_ast": {"op": "plan_required", "args": []},
        },
    }
    values_by_scope = {
        "consolidated": {"VPI": "400.000.000", "NLG": "300.000.000"},
        "separate": {"VPI": "200.000.000", "NLG": "400.000.000"},
    }
    tables = []
    for scope, values in values_by_scope.items():
        for ticker, value in values.items():
            tables.append(
                _table(
                    uid=f"{ticker}-share-flip-{scope}",
                    ticker=ticker,
                    year=2021,
                    scope=scope,
                    kind="financial_note_detail",
                    headers=["Nhãn dòng", "Số cuối năm(Cổ phiếu)", "Số đầu năm(Cổ phiếu)"],
                    rows=[
                        ["Nhãn dòng", "Số cuối năm(Cổ phiếu)", "Số đầu năm(Cổ phiếu)"],
                        ["Cổ phiếu đang lưu hành", value, value],
                    ],
                    source_title="Bảng chi tiết cổ phiếu",
                )
            )
    by_pair = {}
    for table in tables:
        by_pair.setdefault((table["ticker"], table["report_year"]), []).append(table)

    assert (
        builder.source_first_multi_entity_share_threshold_answer(
            item,
            tables_by_pair=by_pair,
            resolver_kwargs={
                "parse_decimal": builder.parse_decimal,
                "candidate_evidence_window": builder.candidate_evidence_window,
                "choose_year_column": builder.choose_year_column,
                "source_multiplier": builder.source_multiplier,
                "requested_divisor": builder.requested_divisor,
                "report_year_neighbor_fallback": None,
            },
        )
        is None
    )


def test_source_first_feedback_provision_requires_selected_row_or_metric_column():
    builder = _builder_module()
    provision_item = {
        "question": "Chi phí dự phòng của ABC trong năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "separate",
            "operands": [{"metric": "Chi phí dự phòng ABC"}],
        },
    }
    parent_only = _table(
        uid="provision-parent-only",
        ticker="ABC",
        year=2024,
        scope="separate",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "2024 VND"],
        rows=[
            ["Nhãn dòng", "2024 VND"],
            ["Dự phòng", ""],
            ["Chi phí", "100"],
        ],
    )
    assert _feedback_reason(
        builder,
        provision_item,
        parent_only,
        row_index=2,
        row_text="Chi phí",
        column_index=1,
        target_text="chi phí dự phòng",
    ) == "PROVISION_ROW_REQUIRED"

    transposed = _table(
        uid="provision-metric-column",
        ticker="ABC",
        year=2024,
        scope="separate",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "Dự phòng", "Khác"],
        rows=[
            ["Nhãn dòng", "Dự phòng", "Khác"],
            ["Chi phí", "100", "200"],
        ],
    )
    assert _feedback_reason(
        builder,
        provision_item,
        transposed,
        row_index=1,
        row_text="Chi phí",
        column_index=1,
        target_text="chi phí dự phòng",
    ) is None

    non_provision_item = {
        **provision_item,
        "question": "Chi phí nhân viên của ABC trong năm 2024 là bao nhiêu tỷ đồng?",
    }
    assert _feedback_reason(
        builder,
        non_provision_item,
        parent_only,
        row_index=2,
        row_text="Chi phí dự phòng",
        column_index=1,
        target_text="chi phí nhân viên",
    ) == "PROVISION_ROW_CONFLICT"


def test_source_first_feedback_total_requires_binding_but_keeps_named_wrappers():
    builder = _builder_module()
    total_item = {
        "question": "Tổng tài sản của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Tổng tài sản ABC"}],
        },
    }
    plain_parent = _table(
        uid="plain-parent-total",
        ticker="ABC",
        year=2024,
        scope="consolidated",
        kind="balance_sheet",
        headers=["Nhãn dòng", "2024 VND"],
        rows=[
            ["Nhãn dòng", "2024 VND"],
            ["Tài sản", "100"],
        ],
    )
    assert _feedback_reason(
        builder,
        total_item,
        plain_parent,
        row_index=1,
        row_text="Tài sản",
        column_index=1,
        target_text="tài sản",
    ) == "TOTAL_ROW_NOT_BOUND"

    explicit_total = deepcopy(plain_parent)
    explicit_total["rows"][1][0] = "TỔNG TÀI SẢN"
    assert _feedback_reason(
        builder,
        total_item,
        explicit_total,
        row_index=1,
        row_text="TỔNG TÀI SẢN",
        column_index=1,
        target_text="tổng tài sản",
    ) is None

    component_item = {
        "question": "Tổng dư nợ cho vay khách hàng của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Tổng dư nợ cho vay khách hàng ABC"}],
        },
    }
    assert _feedback_reason(
        builder,
        component_item,
        plain_parent,
        row_index=1,
        row_text="Cho vay khách hàng",
        column_index=1,
        target_text="tổng dư nợ cho vay khách hàng",
    ) == "TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING"

    wrapper_item = {
        "question": "Tổng số dư tiền và các khoản tương đương tiền của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [
                {"metric": "Tổng số dư tiền và các khoản tương đương tiền ABC cuối năm"}
            ],
        },
    }
    assert _feedback_reason(
        builder,
        wrapper_item,
        plain_parent,
        row_index=1,
        row_text="Tiền và các khoản tương đương tiền",
        column_index=1,
        target_text="tổng số dư tiền và các khoản tương đương tiền",
    ) is None


def test_source_first_feedback_total_rejects_component_section_total():
    builder = _builder_module()
    item = {
        "question": "Tổng dư nợ cho vay khách hàng của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Tổng dư nợ cho vay khách hàng ABC"}],
        },
    }
    table = _table(
        uid="component-section-total",
        ticker="ABC",
        year=2024,
        scope="consolidated",
        kind="financial_note_detail",
        headers=["Nhãn dòng", "2024 VND"],
        rows=[
            ["Nhãn dòng", "2024 VND"],
            ["Tiền gửi", ""],
            ["TỔNG CỘNG", "100"],
        ],
    )
    assert _feedback_reason(
        builder,
        item,
        table,
        row_index=2,
        row_text="TỔNG CỘNG",
        column_index=1,
        target_text="tổng dư nợ cho vay khách hàng",
    ) == "TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING"


def test_source_first_feedback_direction_is_symmetric_and_preserves_receivable_alias():
    builder = _builder_module()
    borrowing_item = {
        "question": "Số dư vay ngắn hạn của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Vay ngắn hạn ABC"}],
        },
    }
    lending_item = {
        "question": "Số dư cho vay khách hàng của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Cho vay khách hàng ABC"}],
        },
    }
    table = _table(
        uid="direction-contract",
        ticker="ABC",
        year=2024,
        scope="consolidated",
        kind="balance_sheet",
        headers=["Nhãn dòng", "2024 VND"],
        rows=[["Nhãn dòng", "2024 VND"], ["Khoản vay", "100"]],
    )
    assert _feedback_reason(
        builder,
        borrowing_item,
        table,
        row_index=1,
        row_text="Cho vay khách hàng",
        column_index=1,
        target_text="vay ngắn hạn",
    ) == "LENDING_BORROWING_DIRECTION_CONFLICT"
    assert _feedback_reason(
        builder,
        lending_item,
        table,
        row_index=1,
        row_text="Vay ngắn hạn",
        column_index=1,
        target_text="cho vay khách hàng",
    ) == "LENDING_BORROWING_DIRECTION_CONFLICT"
    assert _feedback_reason(
        builder,
        lending_item,
        table,
        row_index=1,
        row_text="Vay và cho vay",
        column_index=1,
        target_text="cho vay khách hàng",
    ) == "LENDING_BORROWING_DIRECTION_CONFLICT"

    receivable_item = {
        "question": "Tổng cho vay dài hạn của công ty mẹ ABC đến ngày 31/12/2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "separate",
            "operands": [{"metric": "Tổng cho vay dài hạn ABC 31/12/"}],
        },
    }
    assert _feedback_reason(
        builder,
        receivable_item,
        table,
        row_index=1,
        row_text="Phải thu về cho vay dài hạn",
        column_index=1,
        target_text="cho vay dài hạn",
    ) is None


def test_source_first_feedback_cash_equivalents_and_term_deposits_are_distinct():
    builder = _builder_module()
    cash_item = {
        "question": "Tổng số dư tiền và các khoản tương đương tiền của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Tổng số dư tiền và các khoản tương đương tiền ABC"}],
        },
    }
    deposit_item = {
        "question": "Số dư tiền gửi của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Tiền gửi ABC"}],
        },
    }
    term_item = {
        "question": "Số dư tiền gửi có kỳ hạn của ABC cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["ABC"],
            "years": [2024],
            "scope": "consolidated",
            "operands": [{"metric": "Tiền gửi có kỳ hạn ABC"}],
        },
    }
    table = _table(
        uid="cash-deposit-contract",
        ticker="ABC",
        year=2024,
        scope="consolidated",
        kind="balance_sheet",
        headers=["Nhãn dòng", "2024 VND"],
        rows=[["Nhãn dòng", "2024 VND"], ["Dòng", "100"]],
    )
    assert _feedback_reason(
        builder,
        cash_item,
        table,
        row_index=1,
        row_text="Tiền gửi có kỳ hạn",
        column_index=1,
        target_text="tổng số dư tiền và các khoản tương đương tiền",
    ) == "CASH_TERM_DEPOSIT_CONFLICT"
    assert _feedback_reason(
        builder,
        deposit_item,
        table,
        row_index=1,
        row_text="Tiền và các khoản tương đương tiền",
        column_index=1,
        target_text="tiền gửi",
    ) == "CASH_TERM_DEPOSIT_CONFLICT"
    assert _feedback_reason(
        builder,
        term_item,
        table,
        row_index=1,
        row_text="Tiền gửi",
        column_index=1,
        target_text="tiền gửi có kỳ hạn",
    ) == "CASH_TERM_DEPOSIT_CONFLICT"
    assert _feedback_reason(
        builder,
        term_item,
        table,
        row_index=1,
        row_text="Tiền gửi có kỳ hạn",
        column_index=1,
        target_text="tiền gửi có kỳ hạn",
    ) is None
    assert _feedback_reason(
        builder,
        cash_item,
        table,
        row_index=1,
        row_text="Tiền và các khoản tương đương tiền",
        column_index=1,
        target_text="tổng số dư tiền và các khoản tương đương tiền",
    ) is None


def test_source_first_feedback_related_party_requires_question_qualifier():
    builder = _builder_module()
    unqualified = {
        "question": "Số dư tiền gửi của BID cuối năm 2024 là bao nhiêu tỷ đồng?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["BID"],
            "years": [2024],
            "scope": "separate",
            "operands": [{"metric": "Tiền gửi tại Công ty X BID"}],
        },
    }
    qualified = {
        **unqualified,
        "question": "Số dư tiền gửi tại Công ty X của BID cuối năm 2024 là bao nhiêu tỷ đồng?",
    }
    table = _table(
        uid="related-party-contract",
        ticker="BID",
        year=2024,
        scope="separate",
        kind="related_party_schedule",
        headers=["Bên liên quan", "2024 VND"],
        rows=[
            ["Bên liên quan", "2024 VND"],
            ["Công ty X", "100"],
        ],
        source_title="Chi tiết số dư với các bên liên quan",
    )
    assert _feedback_reason(
        builder,
        unqualified,
        table,
        row_index=1,
        row_text="Công ty X",
        column_index=1,
        target_text="tiền gửi tại công ty x",
        qualifier_phrases=("cong ty x",),
    ) == "UNQUALIFIED_RELATED_PARTY_TABLE"
    assert _feedback_reason(
        builder,
        qualified,
        table,
        row_index=1,
        row_text="Công ty X",
        column_index=1,
        target_text="tiền gửi tại công ty x",
        qualifier_phrases=("cong ty x",),
    ) is None


def _interest_threshold_contract_item():
    return {
        "question": (
            "Căn cứ số liệu năm 2016 của công ty mẹ AAA, công ty mẹ NKG, "
            "công ty mẹ DCM và công ty mẹ DPM, tổng số công ty có phát sinh "
            "chi phí lãi vay nhiều hơn 100 tỷ là bao nhiêu?"
        ),
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["AAA", "NKG", "DCM", "DPM"],
            "years": [2016],
            "scope": "separate",
            "requested_unit": None,
            "operation_ast": {"op": "count", "args": ["filtered_values"]},
            "operands": [],
        },
    }


def _interest_threshold_contract_tables(*, row_label, headers):
    return [
        _table(
            uid=f"{ticker.lower()}-interest-contract",
            ticker=ticker,
            year=2016,
            scope="separate",
            kind="income_statement",
            headers=headers,
            rows=[
                list(headers),
                [row_label, "100.000.000.000"],
            ],
            source_title="Báo cáo kết quả hoạt động kinh doanh",
        )
        for ticker in ("AAA", "NKG", "DCM", "DPM")
    ]


def test_multi_entity_interest_threshold_rejects_missing_source_unit():
    builder = _builder_module()
    tables = _interest_threshold_contract_tables(
        row_label="Chi phí lãi vay",
        headers=["Chỉ tiêu", "Năm 2016"],
    )
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    assert (
        builder.source_first_multi_entity_interest_threshold_answer(
            _interest_threshold_contract_item(),
            tables_by_pair=by_pair,
            resolver_kwargs={
                "parse_decimal": builder.parse_decimal,
                "candidate_evidence_window": builder.candidate_evidence_window,
                "choose_year_column": builder.choose_year_column,
                "source_multiplier": builder.source_multiplier,
                "requested_divisor": builder.requested_divisor,
                "report_year_neighbor_fallback": None,
            },
        )
        is None
    )


def test_multi_entity_interest_threshold_rejects_interest_income_row():
    builder = _builder_module()
    tables = _interest_threshold_contract_tables(
        row_label="Lãi tiền gửi",
        headers=["Chỉ tiêu", "Năm 2016 VND"],
    )
    by_pair = {
        (table["ticker"], table["report_year"]): [table]
        for table in tables
    }
    assert (
        builder.source_first_multi_entity_interest_threshold_answer(
            _interest_threshold_contract_item(),
            tables_by_pair=by_pair,
            resolver_kwargs={
                "parse_decimal": builder.parse_decimal,
                "candidate_evidence_window": builder.candidate_evidence_window,
                "choose_year_column": builder.choose_year_column,
                "source_multiplier": builder.source_multiplier,
                "requested_divisor": builder.requested_divisor,
                "report_year_neighbor_fallback": None,
            },
        )
        is None
    )
