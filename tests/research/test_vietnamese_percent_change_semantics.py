from __future__ import annotations
from finance_query.research.vietnamese_percent_change_semantics import candidate_semantic,gold_relative_percent_change
def test_narrow_percent_change_requires_phrase_two_years_and_one_ticker()->None:
    assert candidate_semantic("Tính phần trăm thay đổi EPS của VNM từ năm 2022 đến năm 2023.")=="relative_percentage_change"
    assert candidate_semantic("EPS của VNM năm 2023 là bao nhiêu?") is None
    assert candidate_semantic("Tính phần trăm thay đổi EPS từ năm 2022 đến năm 2023.",resolved_company_count=0) is None
def test_percentage_points_and_share_ratios_are_not_relative_change()->None:
    assert candidate_semantic("ROE của VNM thay đổi bao nhiêu điểm phần trăm từ năm 2022 đến năm 2023?") is None
    assert candidate_semantic("LNST của VNM chiếm bao nhiêu phần trăm doanh thu năm 2023?") is None
def test_gold_semantic_requires_subtract_then_final_divide()->None:
    assert gold_relative_percent_change("subtract(120, 100), divide(#0, 100)") is True
    assert gold_relative_percent_change("divide(120, 100)") is False
    assert gold_relative_percent_change("subtract(20, 10)") is False
