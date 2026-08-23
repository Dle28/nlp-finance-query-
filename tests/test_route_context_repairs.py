from finance_query.route_context_repairs import literal_repair_candidates


def test_literal_scope_candidate_requires_one_unambiguous_scope() -> None:
    values = literal_repair_candidates("Lợi nhuận theo báo cáo tài chính riêng năm 2024", "scope")
    assert [value["value"] for value in values] == ["separate"]
    assert literal_repair_candidates(
        "So sánh báo cáo tài chính riêng và báo cáo hợp nhất", "scope"
    ) == []


def test_parent_role_does_not_imply_separate_scope() -> None:
    assert literal_repair_candidates("Lợi nhuận của công ty mẹ ABC năm 2024", "scope") == []


def test_literal_operation_candidates_preserve_question_spans() -> None:
    question = "Chênh lệch trung bình là bao nhiêu %, làm tròn 2 chữ số?"
    values = literal_repair_candidates(question, "controlled_operation_contract")
    assert {value["value"] for value in values} == {
        "subtract_or_difference", "average_or_median", "ratio_or_percent", "requested_rounding"
    }
    assert all(question[value["char_start"]:value["char_end"]] == value["literal_text"] for value in values)


def test_literal_entity_and_year_are_candidates_not_decisions() -> None:
    question = "Công ty (HPG) năm 2024"
    assert [value["value"] for value in literal_repair_candidates(question, "entity")] == ["HPG"]
    assert [value["value"] for value in literal_repair_candidates(question, "year")] == [2024]
    assert literal_repair_candidates(question, "literal_metric_or_concept_candidate") == []


def test_absolute_year_difference_does_not_mint_growth() -> None:
    question = "Chênh lệch dòng tiền năm 2022 so với năm 2018 là bao nhiêu tỷ đồng?"
    values = literal_repair_candidates(question, "controlled_operation_contract")
    assert "subtract_or_difference" in {value["value"] for value in values}
    assert "year_over_year_growth" not in {value["value"] for value in values}


def test_relative_year_comparison_can_mint_growth() -> None:
    question = "Doanh thu năm 2024 tăng bao nhiêu % so với năm 2023?"
    values = literal_repair_candidates(question, "controlled_operation_contract")
    assert "year_over_year_growth" in {value["value"] for value in values}


def test_among_literal_requires_company_population_evidence() -> None:
    years = "Trong số các năm 2016, 2017 và 2024, năm nào cao nhất?"
    companies = "Trong số DPM, HT1 và HPG, công ty nào có lợi nhuận cao nhất?"
    assert "multi_company_population" not in {
        value["value"] for value in literal_repair_candidates(years, "controlled_operation_contract")
    }
    assert "multi_company_population" in {
        value["value"] for value in literal_repair_candidates(companies, "controlled_operation_contract")
    }


def test_year_population_is_not_overridden_by_later_ticker_like_tokens() -> None:
    question = (
        "Trong số các năm 2016, 2017, 2024 và 2025, chi phí thuế TNDN "
        "của Công ty mẹ HAG năm nào cao nhất?"
    )
    assert "multi_company_population" not in {
        value["value"] for value in literal_repair_candidates(question, "controlled_operation_contract")
    }
