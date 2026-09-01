from __future__ import annotations
from finance_query.research.advisory_intent_abstention import (
    evaluate_full_reference_population,
    explicit_advisory_intent,
)
def test_exact_advisory_gate()->None:
 assert explicit_advisory_intent("Có nên mua cổ phiếu VNM không?",ticker_count=1)
 assert explicit_advisory_intent("Tôi nên mua cổ phiếu VNM không?",ticker_count=1) is False
 assert explicit_advisory_intent("Có nên phân tích cổ phiếu VNM?",ticker_count=1) is False
def test_company_compatibility_is_fail_closed()->None:
 assert explicit_advisory_intent("Có nên đầu tư VNM?",ticker_count=0) is False
 assert explicit_advisory_intent("Có nên đầu tư VNM hay FPT?",ticker_count=2) is False


def test_full_reference_population_only_scores_intrinsically_applicable_records() -> None:
 records = [
  {"vnfinsqa_id": "1", "question": "Có nên mua cổ phiếu VNM?", "tickers": "VNM", "question_category": "recommendation", "difficulty": "easy", "requires_multi_doc": False},
  {"vnfinsqa_id": "2", "question": "Doanh thu VNM là bao nhiêu?", "tickers": "VNM", "question_category": "factual", "difficulty": "easy", "requires_multi_doc": False},
 ]
 rows, report = evaluate_full_reference_population(records=records, seed="test-seed")
 assert len(rows) == 1
 assert report["record_count"] == 1
 assert report["full_reference_record_count"] == 2
 assert report["arms"]["B_A_plus_explicit_advisory_abstention"]["correct_count"] == 1
