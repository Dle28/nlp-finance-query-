from __future__ import annotations

from finance_query.research.explicit_ticker_semantic_source_probe import _effective_operand


def test_semantic_probe_applies_a_rule_table_contract_without_mutating_the_plan_operand() -> None:
    original = {"ticker": "PC1", "allowed_table_functions": [], "years": [2023]}
    revised = _effective_operand(original, {"allowed_table_functions": ("balance_sheet",)})
    assert revised["allowed_table_functions"] == ["balance_sheet"]
    assert original["allowed_table_functions"] == []
