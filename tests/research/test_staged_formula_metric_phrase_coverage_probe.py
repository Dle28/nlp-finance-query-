from __future__ import annotations

from finance_query.research.staged_formula_metric_phrase_coverage_probe import _asset_matches_operand


def test_coverage_scan_keeps_ticker_year_and_declared_statement_type() -> None:
    operand = {
        "ticker": "PVT",
        "years": [2019],
        "allowed_table_functions": ["balance_sheet"],
    }
    matching = {"ticker": "PVT", "report_year": 2019, "table_function": {"kind": "balance_sheet"}}
    assert _asset_matches_operand(matching, operand)
    assert not _asset_matches_operand({**matching, "ticker": "PLX"}, operand)
    assert not _asset_matches_operand({**matching, "report_year": 2018}, operand)
    assert not _asset_matches_operand({**matching, "table_function": {"kind": "financial_note"}}, operand)


def test_coverage_scan_maps_financial_note_to_the_typed_contract_name() -> None:
    operand = {
        "ticker": "PVT",
        "years": [2019],
        "allowed_table_functions": ["financial_note_detail"],
    }
    assert _asset_matches_operand(
        {"ticker": "PVT", "report_year": 2019, "table_function": {"kind": "financial_note"}}, operand
    )
