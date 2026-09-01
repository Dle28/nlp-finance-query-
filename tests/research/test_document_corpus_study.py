from __future__ import annotations

from finance_query.research.document_corpus_study import (
    _table_features,
    document_kind,
    freeze_ticker_split,
    quantile,
)


def test_document_kind_preserves_special_forms() -> None:
    assert document_kind("AAA_financial_statements_2024_consolidated", "consolidated") == "consolidated"
    assert document_kind("ACV_financial_statements_2022_aggregated", "unknown") == "aggregated"
    assert document_kind("MCH_2024_financial_statement_explanations_1", "unknown") == "explanation_fragment"
    assert document_kind("HDB_financial_statements_2023_separate_2", "unknown") == "numbered_fragment"


def test_table_features_keep_context_and_table_signals_separate() -> None:
    features = _table_features(
        {
            "report_year": 2024,
            "context_before": "Đơn vị: triệu đồng Bảng cân đối kế toán",
            "headers": ["Chỉ tiêu", "2024", "2023"],
            "rows": [["Doanh thu", "100", "90"], ["Lợi nhuận", "10", "8"]],
        }
    )
    assert features["numeric_usable"]
    assert features["context_unit"]
    assert features["table_unit"] is False
    assert features["current_and_prior_year"]
    assert features["core_statement"]


def test_ticker_split_is_deterministic_and_disjoint() -> None:
    tickers = [f"T{index:03d}" for index in range(100)]
    first = freeze_ticker_split(tickers, "seed")
    second = freeze_ticker_split(reversed(tickers), "seed")
    assert first == second
    assert list(map(len, first.values())) == [20, 30, 50]
    assert len(set(first["discovery"]) & set(first["untouched_evaluation"])) == 0


def test_quantile_interpolates() -> None:
    assert quantile([1, 2, 3, 4, 5], 0.95) == 4.8
