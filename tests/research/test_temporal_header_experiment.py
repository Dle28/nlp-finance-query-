from __future__ import annotations

from finance_query.research.temporal_header_experiment import (
    header_temporal_gold,
    question_only_temporal,
    temporal_header_prediction,
)


def test_header_gold_requires_one_explicit_anchor() -> None:
    assert header_temporal_gold([["Years Ended December 31,"], ["2023", "2022"]]) == "duration"
    assert header_temporal_gold([["As of December 31,"], ["2023", "2022"]]) == "instant"
    assert header_temporal_gold([["2023", "2022"]]) is None
    assert header_temporal_gold([["As of dates for years ended December 31"]]) is None


def test_question_only_parser_abstains_on_bare_year() -> None:
    assert question_only_temporal("What was revenue in 2023?") is None
    assert question_only_temporal("What was revenue for the year ended 2023?") == "duration"
    assert question_only_temporal("What were assets as of 2023?") == "instant"


def test_header_backoff_does_not_override_explicit_question() -> None:
    duration_table = [["Years Ended December 31,"], ["2023", "2022"]]
    assert temporal_header_prediction("What was revenue in 2023?", duration_table) == "duration"
    assert temporal_header_prediction("What were assets as of 2023?", duration_table) == "instant"
    assert temporal_header_prediction(
        "What was revenue in 2023?", duration_table, allow_duration=False
    ) is None
