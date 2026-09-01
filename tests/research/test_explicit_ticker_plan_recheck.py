from __future__ import annotations

from finance_query.research.explicit_ticker_plan_recheck import _is_target


def test_explicit_ticker_overlay_accepts_only_a_single_complete_direct_lookup() -> None:
    base = {"decomposition_status": "abstain"}
    target = {
        "decomposition_status": "complete",
        "effective_family": "direct_lookup",
        "route": "existing_typed_plan",
        "reason_codes": ["EXPLICIT_SOURCE_TICKER_RESOLVED"],
        "operation_ast": {"op": "lookup", "args": ["x0"]},
        "operands": [{"operand_id": "x0"}],
        "entities": ["PC1"],
        "years": [2023],
    }
    assert _is_target(base, target)
    assert not _is_target(base, {**target, "entities": ["PC1", "HT1"]})
    assert not _is_target(base, {**target, "route": "reported_value_lookup"})
