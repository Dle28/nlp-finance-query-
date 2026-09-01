from __future__ import annotations

from finance_query.research.document_corpus_round2 import (
    _bounded_lowest_add,
    _retrieval_query,
    _select_lowest,
    canonical_asset_line,
    numeric_token_set,
)


def test_canonical_asset_line_is_key_order_independent() -> None:
    assert canonical_asset_line({"b": 2, "a": 1}) == canonical_asset_line({"a": 1, "b": 2})


def test_numeric_token_set_preserves_financial_forms() -> None:
    tokens = numeric_token_set("2024 (1.234.567) 12,5%")
    assert "2024" in tokens
    assert "(1.234.567)" in tokens
    assert "12,5%" in tokens


def test_retrieval_query_is_bounded_and_removes_common_words() -> None:
    query = _retrieval_query("Tổng doanh thu thuần của công ty trong năm")
    assert query is not None
    assert '"doanh"' in query
    assert '"thu"' in query
    assert '"của"' not in query


def test_bounded_selection_keeps_only_lowest_scores() -> None:
    candidates = []
    for score in (9, 2, 7, 1, 5):
        _bounded_lowest_add(
            candidates,
            score=score,
            uid=f"u{score}",
            row={"score": score},
            limit=3,
        )
    assert [row["score"] for row in _select_lowest(candidates, 3)] == [1, 2, 5]
