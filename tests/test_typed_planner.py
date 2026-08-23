from finance_query.typed_planner import build_typed_operand_plan


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


def test_period_extremum_is_decomposed_but_not_executed_as_numeric_maximum() -> None:
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
    assert row["decomposition_status"] == "typed_non_executable"
    assert row["operation_ast"]["op"] == "arg_extreme_period"


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
