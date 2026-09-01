from finance_query.e2e.question_compiler import build_typed_operand_plan
from finance_query.e2e.core.report_entities import resolve_explicit_question_ticker


def test_explicit_source_ticker_resolution_requires_one_delimited_uppercase_token() -> None:
    aliases = [
        {"ticker": "PC1", "document_id": "PC1_financial_statements_2023_consolidated"},
        {"ticker": "HT1", "document_id": "HT1_financial_statements_2023_consolidated"},
    ]
    resolved = resolve_explicit_question_ticker("Số dư của PC1 cuối năm 2023 là bao nhiêu?", aliases)
    assert resolved is not None and resolved["ticker"] == "PC1"
    assert resolve_explicit_question_ticker("Số dư của pc1 cuối năm 2023 là bao nhiêu?", aliases) is None
    assert resolve_explicit_question_ticker("So sánh PC1 và HT1", aliases) is None


def test_explicit_single_entity_advisory_is_routed_out_of_numeric_planner() -> None:
    row = build_typed_operand_plan(
        {
            "id": 900001,
            "question": "Có nên mua cổ phiếu VNM không?",
            "question_plan": {
                "family": "direct_lookup",
                "tickers": ["VNM"],
                "years": [],
                "operands": [],
            },
        }
    )
    assert row["decomposition_status"] == "abstain"
    assert row["route"] == "explicit_advisory_abstention"
    assert row["effective_family"] == "out_of_scope_advisory"
    assert row["operation_ast"] == {"op": "abstain"}
    assert row["reason_codes"] == [
        "EXPLICIT_SINGLE_ENTITY_ADVISORY_INTENT",
        "MISSING_OPERANDS",
    ]


def test_advisory_wording_with_multiple_entities_remains_fail_closed_generic() -> None:
    row = build_typed_operand_plan(
        {
            "id": 900002,
            "question": "Có nên mua VNM hay FPT không?",
            "question_plan": {
                "family": "cross_entity_comparison",
                "tickers": ["VNM", "FPT"],
                "years": [],
                "operands": [],
            },
        }
    )
    assert row["route"] != "explicit_advisory_abstention"


def _item(question: str, plan: dict) -> dict:
    return {"id": 1, "question": question, "question_plan": plan}


def test_existing_explicit_operand_becomes_typed_complete() -> None:
    row = build_typed_operand_plan(
        _item(
            "Doanh thu của HPG năm 2022 là bao nhiêu?",
            {
                "family": "direct_lookup",
                "tickers": ["HPG"],
                "years": [2022],
                "scope": "consolidated",
                "operands": [{"operand_id": "x0", "metric": "Doanh thu", "period": 2022}],
                "operation_ast": {"op": "lookup", "args": ["x0"]},
            },
        )
    )
    assert row["decomposition_status"] == "complete"
    assert row["operands"][0]["entity"] == "HPG"
    assert row["operands"][0]["grounding_contract"]["exact_raw_cell"] is True
    assert row["operands"][0]["temporal_contract"]["flow_or_stock"] == "UNRESOLVED_MUST_BIND"
    assert row["submission_eligible"] is False


def test_controlled_formula_has_typed_operands_but_abstains_without_entity() -> None:
    row = build_typed_operand_plan(
        _item(
            "Hệ số thanh toán hiện hành năm 2022 là bao nhiêu lần?",
            {"family": "ratio_or_derived", "tickers": [], "years": [2022], "operands": []},
        )
    )
    assert row["route"] == "controlled_formula_template"
    assert row["decomposition_status"] == "abstain"
    assert "MISSING_ENTITY" in row["reason_codes"]


def test_source_title_alias_resolves_a_single_entity_without_collapsing_scope() -> None:
    row = build_typed_operand_plan(
        _item(
            "Lợi nhuận sau thuế của CTCP Chứng khoán FPT năm 2023 là bao nhiêu tỷ đồng?",
            {
                "family": "direct_lookup",
                # ``FPT`` is part of the issuer name, not an additional issuer.
                "tickers": ["FTS", "FPT"],
                "years": [2023],
                "operands": [{"operand_id": "x0", "metric": "Lợi nhuận sau thuế", "period": 2023}],
                "operation_ast": {"op": "lookup", "args": ["x0"]},
            },
        ),
        report_entity_aliases=[
            {
                "ticker": "FTS",
                "canonical_entity": "chung khoan fpt",
                "source_entity": "Công ty Cổ phần Chứng khoán FPT",
                "document_id": "FTS_2023",
            }
        ],
    )
    assert row["decomposition_status"] == "complete"
    assert row["entities"] == ["FTS"]
    assert row["operands"][0]["entity"] == "FTS"
    assert row["source_title_entity_resolution"]["ticker"] == "FTS"
    assert row["source_title_entity_resolution"]["scope_inferred"] is False


def test_source_title_alias_does_not_collapse_a_cross_entity_program() -> None:
    row = build_typed_operand_plan(
        _item(
            "Chênh lệch doanh thu thuần giữa HPG và HSG trong năm 2022 là bao nhiêu?",
            {
                "family": "cross_entity_comparison",
                "tickers": ["HPG", "HSG"],
                "years": [2022],
                "operands": [],
            },
        ),
        report_entity_aliases=[
            {
                "ticker": "HPG",
                "canonical_entity": "hoa phat",
                "source_entity": "Công ty Cổ phần Tập đoàn Hòa Phát",
                "document_id": "HPG_2022",
            }
        ],
    )
    assert row["entities"] == ["HPG", "HSG"]
    assert row["source_title_entity_resolution"] is None


def test_unknown_complex_structure_abstains_without_inventing_operand() -> None:
    row = build_typed_operand_plan(
        _item(
            "Nếu điều kiện tùy ý xảy ra thì kết quả là gì?",
            {"family": "conditional_analytical", "tickers": [], "years": [], "operands": []},
        )
    )
    assert row["decomposition_status"] == "abstain"
    assert row["operands"] == []
    assert row["operation_ast"] == {"op": "abstain"}


def test_staged_formula_is_typed_but_not_marked_executable() -> None:
    row = build_typed_operand_plan(
        _item(
            "Trong nhóm HPG, HSG, MSR và NKG, xét các công ty có hệ số thanh toán nhanh năm 2022 thấp hơn trung vị của nhóm. Công ty có mức thay đổi biên lợi nhuận gộp cao nhất từ năm 2022 sang năm 2023 có hệ số khả năng thanh toán lãi vay năm 2023 là bao nhiêu lần?",
            {
                "family": "conditional_analytical",
                "tickers": ["HPG", "HSG", "MSR", "NKG"],
                "years": [2022, 2023],
                "operands": [],
            },
        )
    )
    assert row["route"] == "controlled_formula_template"
    assert row["decomposition_status"] == "typed_non_executable"
    assert row["operation_ast"]["op"] == "staged_program"
    assert len(row["operands"]) > 10


def test_leading_target_year_cfo_screening_plan_is_staged_not_executable() -> None:
    row = build_typed_operand_plan(
        _item(
            "Năm 2024, trong các công ty GEE, GEX và SAM có lưu chuyển tiền "
            "thuần từ hoạt động kinh doanh dương trong cả ba năm 2022, 2023 "
            "và 2024, tỷ lệ lợi nhuận sau thuế trên doanh thu thuần cao nhất "
            "là bao nhiêu %?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["GEE", "GEX", "SAM"],
                "years": [2022, 2023, 2024],
                "operands": [],
            },
        )
    )
    assert row["route"] == "controlled_formula_template"
    assert row["formula_id"] == "cfo_positive_multiyear_max_net_margin"
    assert row["decomposition_status"] == "typed_non_executable"
    assert len(row["operands"]) == 15
    assert {operand["stage_id"] for operand in row["operands"]} == {
        "cfo_positive_filter",
        "net_margin_rank",
    }


def test_debt_to_equity_selector_has_one_exact_leaf_set_per_entity() -> None:
    row = build_typed_operand_plan(
        _item(
            "Năm 2019, trong nhóm BSR, PLX và PVT, công ty có hệ số nợ phải "
            "trả trên vốn chủ sở hữu cao nhất có hệ số khả năng thanh toán lãi "
            "vay là bao nhiêu lần?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["BSR", "PLX", "PVT"],
                "years": [2019],
                "operands": [],
            },
        )
    )
    assert row["route"] == "controlled_formula_template"
    assert row["formula_id"] == "debt_to_equity_argmax_interest_coverage"
    assert row["decomposition_status"] == "typed_non_executable"
    assert len(row["operands"]) == 12
    assert {operand["entity"] for operand in row["operands"]} == {"BSR", "PLX", "PVT"}
    assert {operand["stage_id"] for operand in row["operands"]} == {
        "debt_to_equity_rank",
        "interest_coverage_output",
    }


def test_positive_operating_profit_selector_has_explicit_target_leaves() -> None:
    row = build_typed_operand_plan(
        _item(
            "Năm 2017, trong nhóm BSR, PLX và PVT có lợi nhuận thuần từ hoạt "
            "động kinh doanh dương, tại công ty có tỷ lệ lưu chuyển tiền thuần "
            "từ hoạt động kinh doanh trên lợi nhuận thuần từ hoạt động kinh doanh "
            "thấp nhất, tỷ lệ lợi nhuận sau thuế trên doanh thu thuần là bao nhiêu %?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["BSR", "PLX", "PVT"],
                "years": [2017],
                "operands": [],
            },
        )
    )
    assert row["route"] == "controlled_formula_template"
    assert row["formula_id"] == "positive_operating_profit_argmin_cfo_ratio_net_margin"
    assert row["decomposition_status"] == "typed_non_executable"
    assert len(row["operands"]) == 12
    assert {operand["stage_id"] for operand in row["operands"]} == {
        "operating_profit_positive_filter",
        "cfo_to_operating_profit_argmin",
        "net_margin_output",
    }


def test_two_entity_plain_difference_has_one_exact_leaf_per_entity() -> None:
    row = build_typed_operand_plan(
        _item(
            "Chênh lệch doanh thu thuần giữa HPG và HSG trong năm 2022 là bao nhiêu tỷ đồng?",
            {
                "family": "cross_entity_comparison",
                "tickers": ["HPG", "HSG"],
                "years": [2022],
                "operands": [],
            },
        )
    )
    assert row["route"] == "simple_cross_entity_comparison"
    assert row["decomposition_status"] == "complete"
    assert row["operation_ast"]["op"] == "absolute_difference"
    assert [operand["entity"] for operand in row["operands"]] == ["HPG", "HSG"]
    assert {tuple(operand["years"]) for operand in row["operands"]} == {(2022,)}


def test_plain_mean_across_explicit_years_has_one_leaf_per_year() -> None:
    row = build_typed_operand_plan(
        _item(
            "Chi phí bán hàng bình quân của HPG qua các năm 2021, 2022 và 2023 là bao nhiêu tỷ đồng?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["HPG"],
                "years": [2021, 2022, 2023],
                "operands": [],
            },
        )
    )
    assert row["route"] == "simple_aggregation"
    assert row["decomposition_status"] == "complete"
    assert row["operation_ast"] == {"op": "mean", "args": ["x0", "x1", "x2"]}
    assert [operand["years"] for operand in row["operands"]] == [[2021], [2022], [2023]]


def test_period_extremum_compiles_to_grounded_identity_selector() -> None:
    row = build_typed_operand_plan(
        _item(
            "HPG có doanh thu thuần cao nhất vào năm nào trong các năm 2021, 2022 và 2023?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["HPG"],
                "years": [2021, 2022, 2023],
                "operands": [],
            },
        )
    )
    assert row["route"] == "simple_period_extremum"
    assert row["decomposition_status"] == "complete"
    assert row["operation_ast"] == {
        "op": "arg_extreme_period",
        "direction": "max",
        "args": ["x0", "x1", "x2"],
    }
    assert "EXECUTOR_COMPILATION_REQUIRED" not in row["reason_codes"]


def test_filter_phrase_keeps_an_otherwise_plain_mean_abstained() -> None:
    row = build_typed_operand_plan(
        _item(
            "Trong các năm 2021, 2022 và 2023 của HPG, xét các năm có doanh thu thuần dương, bình quân chi phí bán hàng là bao nhiêu tỷ đồng?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["HPG"],
                "years": [2021, 2022, 2023],
                "operands": [],
            },
        )
    )
    assert row["decomposition_status"] == "abstain"
    assert row["operands"] == []


def test_selector_then_different_target_metric_stays_abstained() -> None:
    row = build_typed_operand_plan(
        _item(
            "Trong giai đoạn 2021-2023, vào năm HPG có doanh thu thuần cao nhất, hệ số thanh toán hiện hành là bao nhiêu lần?",
            {
                "family": "conditional_analytical",
                "tickers": ["HPG"],
                "years": [2021, 2022, 2023],
                "operands": [],
            },
        )
    )
    assert row["decomposition_status"] == "abstain"
    assert row["operands"] == []


def test_two_period_difference_is_not_misread_as_a_sum_from_a_total_row_label() -> None:
    row = build_typed_operand_plan(
        _item(
            "Chênh lệch tổng dư nợ vay ngắn hạn của HPG giữa năm 2023 và năm 2022 là bao nhiêu tỷ đồng?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["HPG"],
                "years": [2022, 2023],
                "operands": [],
            },
        )
    )
    assert row["route"] == "simple_temporal_comparison"
    assert row["operation_ast"] == {"op": "subtract", "args": ["x1", "x0"]}


def test_resolved_single_issuer_allows_a_disclosed_total_row_with_title_ticker_noise() -> None:
    row = build_typed_operand_plan(
        _item(
            "Tổng cộng tài sản của CTCP Tập đoàn GELEX (GEX) cuối năm 2021 là bao nhiêu trăm tỷ đồng?",
            {
                # ``GELEX`` is an all-caps issuer word and ``GEX`` is the
                # independently resolved ticker.  The reclassification must
                # not depend on treating both textual tokens as two issuers.
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["GEX"],
                "years": [2021],
                "operands": [],
            },
        )
    )
    assert row["effective_family"] == "direct_lookup"
    assert row["decomposition_status"] == "complete"
    assert row["route"] == "reported_value_lookup"
    assert row["operation_ast"] == {"op": "lookup", "args": ["x0"]}
    assert row["operands"][0]["metric_hints"] == ["Tổng tài sản"]


def test_resolved_single_issuer_allows_disclosed_ownership_row_not_ratio_inference() -> None:
    row = build_typed_operand_plan(
        _item(
            "Tỷ lệ sở hữu của công ty mẹ Tập đoàn Xăng Dầu Việt Nam (PLX) tại Công ty TNHH Hóa chất PTN vào cuối năm 2016 là bao nhiêu phần trăm?",
            {
                "family": "ratio_or_derived",
                "tickers": ["PLX"],
                "years": [2016],
                "scope": "separate",
                "operands": [],
            },
        )
    )
    assert row["effective_family"] == "direct_lookup"
    assert row["decomposition_status"] == "complete"
    assert row["operation_ast"] == {"op": "lookup", "args": ["x0"]}
    assert row["operands"][0]["metric_hints"] == ["Tỷ lệ sở hữu"]


def test_controlled_percentage_formula_compiles_with_explicit_chronological_order() -> None:
    row = build_typed_operand_plan(
        _item(
            "Tốc độ tăng trưởng doanh thu thuần của HPG từ năm 2021 đến năm 2023 là bao nhiêu phần trăm?",
            {
                "family": "temporal_change",
                "tickers": ["HPG"],
                "years": [2021, 2023],
                "operands": [],
            },
        )
    )
    assert row["formula_id"] == "percentage_change"
    assert row["decomposition_status"] == "complete"
    assert row["operation_ast"] == {"op": "percentage_change", "args": ["x_new", "x_old"]}


def test_two_period_growth_uses_percentage_change_not_sum() -> None:
    row = build_typed_operand_plan(
        _item(
            "Tổng dư nợ vay ngắn hạn của HPG năm 2023 tăng so với năm 2022 là bao nhiêu phần trăm?",
            {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["HPG"],
                "years": [2022, 2023],
                "operands": [],
            },
        )
    )
    assert row["route"] == "simple_temporal_comparison"
    assert row["operation_ast"] == {"op": "percentage_change", "args": ["x1", "x0"]}


def test_ratio_with_total_as_its_denominator_uses_its_controlled_formula() -> None:
    row = build_typed_operand_plan(
        _item(
            "Tỷ trọng tiền và các khoản tương đương tiền trên tổng tài sản của HPG năm 2022 là bao nhiêu phần trăm?",
            {
                "family": "ratio_or_derived",
                "tickers": ["HPG"],
                "years": [2022],
                "operands": [],
            },
        )
    )
    assert row["route"] == "controlled_formula_template"
    assert row["operation_ast"]["op"] == "divide"
