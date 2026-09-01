from __future__ import annotations

from copy import deepcopy

from finance_query.e2e.core.answer_level_selector import (
    AnswerLevelSelector,
    select_answer_level,
)


def _plan(
    candidate_id: str,
    *,
    op: str = "lookup",
    operand_ids: tuple[str, ...] = ("x0",),
    route_priority: float = 0.0,
    answer: str = "10",
    is_baseline: bool = False,
    semantic: object = "COMPLETE",
    period: int = 2023,
    entity: str = "VNM",
    scope: str = "separate",
    unit: str = "VND",
    source_hash: str | None = "source-sha",
    replay_status: str = "PASS",
    verification_status: str = "PARTIAL",
) -> dict[str, object]:
    operands = [
        {
            "operand_id": operand_id,
            "internal_table_uid": f"table-{operand_id}",
            "row_index": index,
            "column_index": 1,
            "period": period,
            "entity": entity,
            "scope": scope,
            "unit": unit,
            **({"source_hash": source_hash} if source_hash is not None else {}),
        }
        for index, operand_id in enumerate(operand_ids)
    ]
    ast: dict[str, object]
    if op == "lookup":
        ast = {"op": op, "args": [operand_ids[0]]}
    else:
        ast = {"op": op, "args": list(operand_ids)}
    return {
        "candidate_id": candidate_id,
        "is_baseline": is_baseline,
        "operation_ast": ast,
        "operands": operands,
        "answer_decimal": answer,
        "replay_answer_decimal": answer,
        "replay_status": replay_status,
        "semantic_completeness": semantic,
        "expected_period": period,
        "expected_entity": entity,
        "expected_scope": scope,
        "expected_unit": unit,
        "verification_status": verification_status,
        "route_priority": route_priority,
    }


def test_route_priority_is_only_a_late_tie_breaker() -> None:
    composed = _plan(
        "composed",
        op="subtract",
        operand_ids=("x0", "x1"),
        route_priority=1.0,
    )
    heuristic = _plan(
        "semantic-heuristic",
        op="semantic_cell_heuristic",
        operand_ids=("x0",),
        route_priority=999.0,
        semantic="PARTIAL",
    )

    result = select_answer_level([heuristic, composed])

    assert result["status"] == "SELECTED"
    assert result["selected_candidate_id"] == "composed"
    assert result["answer_channel"] == "BEST_EFFORT_CANDIDATE"
    assert result["candidate_only"] is True


def test_composed_plan_beats_semantic_heuristic_even_when_route_is_lower() -> None:
    heuristic = _plan(
        "heuristic",
        op="semantic_cell_heuristic",
        operand_ids=("x0",),
        route_priority=1000.0,
        semantic="PARTIAL",
    )
    composed = _plan(
        "composed",
        op="add",
        operand_ids=("x0", "x1"),
        route_priority=0.1,
        answer="30",
    )

    result = AnswerLevelSelector().select([heuristic, composed])

    assert result["selected_candidate_id"] == "composed"
    assert "PLAN_COMPLETE" in result["selection_reason_codes"]


def test_baseline_survives_when_new_candidate_is_incomplete() -> None:
    baseline = _plan(
        "baseline-v11",
        is_baseline=True,
        op="semantic_cell_heuristic",
        semantic=None,
        source_hash=None,
        replay_status=None,
        route_priority=1.0,
    )
    incomplete = {
        "candidate_id": "new-incomplete",
        "operation_ast": {"op": "add", "args": ["x0"]},
        "operands": [],
        "answer_decimal": "99",
        "semantic_completeness": "PARTIAL",
        "replay_status": "PASS",
        "route_priority": 999.0,
    }

    result = select_answer_level([incomplete, baseline])

    assert result["selected_candidate_id"] == "baseline-v11"
    assert result["selected_plan_fallback"] is True
    assert "BASELINE_RETAINED" in result["selection_reason_codes"]


def test_invalid_period_scope_and_unit_are_rejected() -> None:
    invalid_period = _plan("bad-period", period=2023)
    invalid_period["operands"][0]["period"] = 2022  # type: ignore[index]
    invalid_scope = _plan("bad-scope")
    invalid_scope["operands"][0]["scope"] = "consolidated"  # type: ignore[index]
    invalid_unit = _plan("bad-unit")
    invalid_unit["operands"][0]["unit"] = "USD"  # type: ignore[index]

    result = select_answer_level([invalid_period, invalid_scope, invalid_unit])

    assert result["status"] == "ABSTAIN"
    assert result["selected_plan"] is None
    reasons = {
        reason
        for rejected in result["rejected_plans"]
        for reason in rejected["reason_codes"]
    }
    assert {"PERIOD_MISMATCH", "SCOPE_MISMATCH", "UNIT_MISMATCH"} <= reasons


def test_replay_mismatch_is_fail_closed() -> None:
    mismatched = _plan("replay-mismatch", answer="10", replay_status="PASS")
    mismatched["replay_answer_decimal"] = "11"

    result = select_answer_level([mismatched])

    assert result["status"] == "ABSTAIN"
    assert result["selected_candidate_id"] is None
    rejected = result["rejected_plans"][0]
    assert "REPLAY_MISMATCH" in rejected["reason_codes"]


def test_duplicate_candidate_ids_abstain_the_whole_pool() -> None:
    first = _plan("same")
    second = _plan("same", answer="20")

    result = select_answer_level([first, second])

    assert result["status"] == "ABSTAIN"
    assert "DUPLICATE_CANDIDATE_ID" in result["selection_reason_codes"]
    assert result["selected_candidate_id"] is None


def test_deterministic_tie_is_independent_of_input_order_and_is_not_authority() -> None:
    left = _plan("candidate-a", route_priority=7.0)
    right = _plan("candidate-b", route_priority=7.0)
    original_left = deepcopy(left)
    original_right = deepcopy(right)

    first = select_answer_level([right, left])
    second = select_answer_level([left, right])

    assert first["selected_candidate_id"] == "candidate-a"
    assert second["selected_candidate_id"] == "candidate-a"
    assert first["selected_plan"]["candidate_id"] == "candidate-a"
    assert first["may_authorize_answer"] is False
    assert first["answer_authority"] is False
    assert first["release_authorized"] is False
    assert left == original_left
    assert right == original_right


def test_candidate_pool_is_bounded_without_dropping_an_explicit_baseline() -> None:
    baseline = _plan(
        "baseline-v11",
        is_baseline=True,
        route_priority=-100.0,
    )
    candidates = [_plan(f"candidate-{index}", route_priority=100.0) for index in range(4)]

    result = select_answer_level([baseline, *candidates], max_candidates=2)

    assert result["status"] == "SELECTED"
    assert result["considered_candidate_count"] == 2
    assert result["truncated_candidate_count"] == 3
