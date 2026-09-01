"""Contract-first integration tests for whole-question candidate selection.

This file intentionally does not implement ``CandidatePlanSet`` or
``AnswerLevelSelector``.  The parent integration is expected to provide the
following small adapter at
``finance_query.e2e.core.candidate_plan_selector``::

    CandidatePlanSet(
        question_id: int,
        question_contract: Mapping[str, Any],
        plans: Sequence[Mapping[str, Any]],
        baseline_plan: Mapping[str, Any] | None = None,
    )
    AnswerLevelSelector().select(plan_set) -> Mapping[str, Any]

The ``question_contract`` is the compiler-facing whole-question contract. It
contains ``operation_ast``, ``required_operand_ids``, ``required_periods``,
``required_entities``, ``required_scope``, ``required_unit`` and, when
needed, ``required_output_unit``.  Each plan is a source-bound candidate
mapping containing at least:

* ``plan_id`` and ``question_id``;
* ``operation_ast`` and the complete ``operands`` list;
* ``replay_answer_decimal`` and ``replay_status``;
* ``semantic_completeness`` and ``verification_status``;
* period/entity/scope/unit metadata and source coordinates/hash; and
* ``route_family``/``route_priority`` for diagnostic tie-breaking only.

The selector result is also a mapping with ``selected_plan_id``,
``selected_answer_decimal``, ``decision``, ``reason_codes`` and an
``authority`` mapping.  The authority mapping must not authorize an answer,
evidence, training, promotion or release.  This contract deliberately keeps
the tests independent of any Question-ID exception table.

The new-module import is deferred until a selector test runs.  That lets the
existing compiler/executor/certificate regression tests execute while making
the missing parent integration an explicit test failure rather than silently
skipping the coverage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from importlib import import_module
from typing import Any

import pytest

from finance_query.e2e.core.answer_certificates import compile_abstention_certificate
from finance_query.e2e.core.grounded_authorization import (
    AUTHORIZATION_CONTRACT,
    _strict_answer_view,
)
from finance_query.e2e.decimal_executor import execute_ast, validate_operation_ast
from finance_query.e2e.question_compiler import build_typed_operand_plan


pytestmark = pytest.mark.e2e

_SELECTOR_MODULE = "finance_query.e2e.core.candidate_plan_selector"
_SELECTOR_EXPORTS = ("CandidatePlanSet", "AnswerLevelSelector")

_selector_import_error: Exception | None = None
try:
    _selector_module = import_module(_SELECTOR_MODULE)
except Exception as error:  # pragma: no cover - exercised by parent integration
    _selector_module = None
    _selector_import_error = error


def _selector_api() -> tuple[type[Any], type[Any]]:
    """Return the parent API or fail with an actionable integration blocker."""

    if _selector_import_error is not None:
        pytest.fail(
            "BLOCKED: parent integration module is unavailable: "
            f"import {_SELECTOR_MODULE!r} failed with "
            f"{type(_selector_import_error).__name__}: {_selector_import_error}. "
            "Add the module and the two documented exports; do not skip this test."
        )
    assert _selector_module is not None
    missing = [name for name in _SELECTOR_EXPORTS if not hasattr(_selector_module, name)]
    if missing:
        pytest.fail(
            "BLOCKED: parent integration API is incomplete in "
            f"{_SELECTOR_MODULE}: missing {', '.join(missing)}. "
            "Expected CandidatePlanSet and AnswerLevelSelector."
        )
    return (
        getattr(_selector_module, "CandidatePlanSet"),
        getattr(_selector_module, "AnswerLevelSelector"),
    )


def _operand(
    operand_id: str,
    *,
    entity: str = "AAA",
    period: int,
    value: str,
    scope: str = "consolidated",
    unit: str = "million_vnd",
    row_index: int = 1,
    column_index: int = 2,
) -> dict[str, Any]:
    table_uid = f"table-{entity}-{period}-{scope}"
    source_uid = f"source-{entity}-{period}-{scope}"
    return {
        "operand_id": operand_id,
        "entity": entity,
        "ticker": entity,
        "period": period,
        "years": [period],
        "scope": scope,
        "unit": unit,
        "raw_value": value,
        "source_uid": source_uid,
        "table_uid": table_uid,
        "source": {
            "source_uid": source_uid,
            "table_uid": table_uid,
            "row_index": row_index,
            "column_index": column_index,
            "source_hash": "a" * 64,
        },
    }


def _plan(
    question_id: int,
    plan_id: str,
    *,
    operation_ast: Mapping[str, Any],
    operands: Sequence[Mapping[str, Any]],
    answer: str | None,
    route_family: str,
    route_priority: float = 0.0,
    semantic_completeness: str = "COMPLETE",
    verification_status: str = "PARTIAL",
    replay_status: str = "PASS",
    filter_passed: bool = True,
) -> dict[str, Any]:
    operand_rows = [dict(operand) for operand in operands]
    periods = sorted(
        {
            int(operand["period"])
            for operand in operand_rows
            if isinstance(operand.get("period"), int)
        }
    )
    entities = sorted(
        {
            str(operand["entity"])
            for operand in operand_rows
            if str(operand.get("entity") or "").strip()
        }
    )
    scopes = sorted(
        {
            str(operand["scope"])
            for operand in operand_rows
            if str(operand.get("scope") or "").strip()
        }
    )
    units = sorted(
        {
            str(operand["unit"])
            for operand in operand_rows
            if str(operand.get("unit") or "").strip()
        }
    )
    return {
        "question_id": question_id,
        "plan_id": plan_id,
        "operation_ast": dict(operation_ast),
        "operands": operand_rows,
        "replay_answer_decimal": answer,
        "replay_status": replay_status,
        "semantic_completeness": semantic_completeness,
        "operand_completeness": (
            "COMPLETE" if semantic_completeness == "COMPLETE" else "INCOMPLETE"
        ),
        "verification_status": verification_status,
        "route_family": route_family,
        "route_priority": route_priority,
        "filter_passed": filter_passed,
        "candidate_status": "SURVIVED_FILTER" if filter_passed else "REJECTED",
        "periods": periods,
        "entities": entities,
        "scope": scopes[0] if len(scopes) == 1 else None,
        "unit": units[0] if len(units) == 1 else None,
        "source_coordinates": [
            {
                "table_uid": operand.get("table_uid"),
                "row_index": (operand.get("source") or {}).get("row_index"),
                "column_index": (operand.get("source") or {}).get("column_index"),
            }
            for operand in operand_rows
        ],
        "source_hash": "a" * 64,
    }


def _question_contract(
    *,
    operation_ast: Mapping[str, Any],
    operand_ids: Sequence[str],
    periods: Sequence[int],
    entities: Sequence[str],
    scope: str,
    unit: str,
    output_unit: str | None = None,
    selector_mode: str | None = None,
) -> dict[str, Any]:
    contract = {
        "operation_ast": dict(operation_ast),
        "required_operations": [str(operation_ast.get("op") or "")],
        "required_operand_ids": list(operand_ids),
        "required_operand_count": len(operand_ids),
        "required_periods": list(periods),
        "required_entities": list(entities),
        "required_scope": scope,
        "required_unit": unit,
    }
    if output_unit is not None:
        contract["required_output_unit"] = output_unit
    if selector_mode is not None:
        contract["selector_mode"] = selector_mode
    return contract


def _select(
    *,
    question_id: int,
    question_contract: Mapping[str, Any],
    plans: Sequence[Mapping[str, Any]],
    baseline_plan: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    candidate_plan_set_type, selector_type = _selector_api()
    plan_set = candidate_plan_set_type(
        question_id=question_id,
        question_contract=dict(question_contract),
        plans=[dict(plan) for plan in plans],
        baseline_plan=dict(baseline_plan) if baseline_plan is not None else None,
    )
    result = selector_type().select(plan_set)
    if not isinstance(result, Mapping):
        pytest.fail(
            "BLOCKED: AnswerLevelSelector.select must return a mapping with "
            "selected_plan_id, selected_answer_decimal, decision, reason_codes "
            "and authority."
        )
    return result


def _assert_result_shape(result: Mapping[str, Any]) -> None:
    assert isinstance(result.get("selected_plan_id"), (str, type(None)))
    assert "selected_answer_decimal" in result
    assert isinstance(result.get("decision"), str)
    assert isinstance(result.get("reason_codes"), list)
    assert isinstance(result.get("authority"), Mapping)


def _assert_non_authorizing(result: Mapping[str, Any]) -> None:
    _assert_result_shape(result)
    authority = result["authority"]
    for key in (
        "answer_authorized",
        "strict_answer_authorized",
        "evidence_authorized",
        "training_eligible",
        "promotion_allowed",
        "release_authorized",
    ):
        assert authority.get(key) is False, f"selector upgraded authority at {key}"


def _compiled_multi_operand_plan() -> dict[str, Any]:
    return build_typed_operand_plan(
        {
            "id": 501,
            "question": (
                "Tổng doanh thu của AAA qua các năm 2021, 2022 và 2023 "
                "là bao nhiêu triệu đồng?"
            ),
            "question_plan": {
                "family": "multi_entity_or_period_aggregation",
                "tickers": ["AAA"],
                "years": [2021, 2022, 2023],
                "scope": "consolidated",
                "requested_unit": "million_vnd",
                "operands": [
                    {"operand_id": "x0", "metric": "Doanh thu", "ticker": "AAA", "period": 2021},
                    {"operand_id": "x1", "metric": "Doanh thu", "ticker": "AAA", "period": 2022},
                    {"operand_id": "x2", "metric": "Doanh thu", "ticker": "AAA", "period": 2023},
                ],
                "operation_ast": {"op": "sum", "args": ["x0", "x1", "x2"]},
            },
        }
    )


def test_existing_compiler_and_decimal_executor_replay_a_multi_operand_ast() -> None:
    typed_plan = _compiled_multi_operand_plan()

    assert typed_plan["decomposition_status"] == "complete"
    assert typed_plan["operation_ast"] == {"op": "sum", "args": ["x0", "x1", "x2"]}
    assert len(typed_plan["operands"]) == 3
    assert validate_operation_ast(typed_plan["operation_ast"]) == []
    assert execute_ast(
        typed_plan["operation_ast"],
        {"x0": Decimal("100"), "x1": Decimal("150"), "x2": Decimal("200")},
    ) == Decimal("450")


def test_composed_arithmetic_does_not_let_one_semantic_cell_beat_a_complete_plan() -> None:
    typed_plan = _compiled_multi_operand_plan()
    ast = typed_plan["operation_ast"]
    operands = [
        _operand("x0", period=2021, value="100"),
        _operand("x1", period=2022, value="150"),
        _operand("x2", period=2023, value="200"),
    ]
    contract = _question_contract(
        operation_ast=ast,
        operand_ids=["x0", "x1", "x2"],
        periods=[2021, 2022, 2023],
        entities=["AAA"],
        scope="consolidated",
        unit="million_vnd",
    )
    heuristic = _plan(
        501,
        "single-cell-heuristic",
        operation_ast={"op": "lookup", "args": ["x0"]},
        operands=operands[:1],
        answer="999",
        route_family="semantic_cell_heuristic",
        route_priority=1000.0,
        semantic_completeness="PARTIAL",
    )
    composed = _plan(
        501,
        "complete-three-operand-sum",
        operation_ast=ast,
        operands=operands,
        answer="450",
        route_family="program_ast",
        route_priority=1.0,
    )

    result = _select(question_id=501, question_contract=contract, plans=[heuristic, composed])

    _assert_non_authorizing(result)
    assert result["selected_plan_id"] == "complete-three-operand-sum"
    assert result["selected_answer_decimal"] == "450"
    assert "single-cell-heuristic" in result.get("rejected_plan_ids", [])


def test_temporal_comparison_preserves_period_order_sign_and_source_unit() -> None:
    question_id = 502
    ast = {"op": "percentage_change", "args": ["x_new", "x_old"]}
    old = _operand("x_old", period=2023, value="100")
    new = _operand("x_new", period=2024, value="120")
    contract = _question_contract(
        operation_ast=ast,
        operand_ids=["x_old", "x_new"],
        periods=[2023, 2024],
        entities=["AAA"],
        scope="consolidated",
        unit="million_vnd",
        output_unit="percent",
    )
    correct = _plan(
        question_id,
        "temporal-new-minus-old",
        operation_ast=ast,
        operands=[old, new],
        answer="20",
        route_family="temporal_program_ast",
        route_priority=1.0,
    )
    wrong_sign = _plan(
        question_id,
        "temporal-reversed-sign",
        operation_ast={"op": "percentage_change", "args": ["x_old", "x_new"]},
        operands=[old, new],
        answer="-16.6666666666666666666666666667",
        route_family="high_priority_route",
        route_priority=1000.0,
    )
    wrong_period_unit = _plan(
        question_id,
        "temporal-wrong-period-unit",
        operation_ast=ast,
        operands=[
            _operand("x_old", period=2022, value="80", unit="billion_vnd"),
            _operand("x_new", period=2024, value="120", unit="billion_vnd"),
        ],
        answer="50",
        route_family="high_priority_route",
        route_priority=2000.0,
    )

    result = _select(
        question_id=question_id,
        question_contract=contract,
        plans=[wrong_sign, wrong_period_unit, correct],
    )

    _assert_non_authorizing(result)
    assert result["selected_plan_id"] == "temporal-new-minus-old"
    assert result["selected_answer_decimal"] == "20"
    assert {"temporal-reversed-sign", "temporal-wrong-period-unit"}.issubset(
        set(result.get("rejected_plan_ids", []))
    )


@pytest.mark.parametrize("direction, expected_period", [("max", "2022"), ("min", "2021")])
def test_argmax_and_argmin_are_bound_to_the_question_scope_and_periods(
    direction: str,
    expected_period: str,
) -> None:
    question_id = 503 if direction == "max" else 504
    periods = [2021, 2022, 2023]
    operand_ids = [f"x{period}" for period in periods]
    ast = {
        "op": "arg_extreme_period",
        "direction": direction,
        "args": operand_ids,
    }
    exact_operands = [
        _operand(
            operand_id,
            period=period,
            value={2021: "10", 2022: "30", 2023: "20"}[period],
            scope="separate",
        )
        for operand_id, period in zip(operand_ids, periods)
    ]
    contract = _question_contract(
        operation_ast=ast,
        operand_ids=operand_ids,
        periods=periods,
        entities=["AAA"],
        scope="separate",
        unit="million_vnd",
        output_unit="period",
        selector_mode=f"arg{direction}",
    )
    exact = _plan(
        question_id,
        f"arg-{direction}-exact",
        operation_ast=ast,
        operands=exact_operands,
        answer=expected_period,
        route_family="arg_extreme_period_program",
        route_priority=1.0,
    )
    wrong_scope = _plan(
        question_id,
        f"arg-{direction}-consolidated-leak",
        operation_ast=ast,
        operands=[
            _operand(
                operand_id,
                period=period,
                value={2021: "10", 2022: "30", 2023: "20"}[period],
                scope="consolidated",
            )
            for operand_id, period in zip(operand_ids, periods)
        ],
        answer="2023",
        route_family="high_priority_arg_route",
        route_priority=1000.0,
    )
    missing_period = _plan(
        question_id,
        f"arg-{direction}-missing-period",
        operation_ast=ast,
        operands=exact_operands[:2],
        answer="2022",
        route_family="high_priority_arg_route",
        route_priority=2000.0,
    )

    result = _select(
        question_id=question_id,
        question_contract=contract,
        plans=[wrong_scope, missing_period, exact],
    )

    _assert_non_authorizing(result)
    assert result["selected_plan_id"] == f"arg-{direction}-exact"
    assert result["selected_answer_decimal"] == expected_period
    assert {
        f"arg-{direction}-consolidated-leak",
        f"arg-{direction}-missing-period",
    }.issubset(set(result.get("rejected_plan_ids", [])))


def test_plan_missing_an_operand_is_rejected_even_when_its_ast_shape_is_valid() -> None:
    question_id = 505
    ast = {"op": "sum", "args": ["x0", "x1", "x2"]}
    contract = _question_contract(
        operation_ast=ast,
        operand_ids=["x0", "x1", "x2"],
        periods=[2021, 2022, 2023],
        entities=["AAA"],
        scope="consolidated",
        unit="million_vnd",
    )
    incomplete = _plan(
        question_id,
        "missing-x2",
        operation_ast=ast,
        operands=[
            _operand("x0", period=2021, value="100"),
            _operand("x1", period=2022, value="150"),
        ],
        answer="250",
        route_family="program_ast",
        route_priority=1000.0,
    )

    result = _select(question_id=question_id, question_contract=contract, plans=[incomplete])

    _assert_non_authorizing(result)
    assert result["selected_plan_id"] is None
    assert result["selected_answer_decimal"] is None
    assert result["decision"] in {"ABSTAIN", "NO_ELIGIBLE_PLAN"}
    assert "missing-x2" in result.get("rejected_plan_ids", [])
    assert any("OPERAND" in str(code).upper() for code in result["reason_codes"])


def test_baseline_candidate_is_kept_when_every_new_plan_fails() -> None:
    question_id = 506
    ast = {"op": "lookup", "args": ["x0"]}
    baseline = _plan(
        question_id,
        "baseline-v11",
        operation_ast=ast,
        operands=[_operand("x0", period=2023, value="17")],
        answer="17",
        route_family="baseline_v11",
        route_priority=0.0,
    )
    failed_new = _plan(
        question_id,
        "new-route-failed-replay",
        operation_ast=ast,
        operands=[_operand("x0", period=2023, value="17")],
        answer="999",
        route_family="new_route",
        route_priority=10000.0,
        verification_status="REJECTED",
        replay_status="FAIL",
        filter_passed=False,
    )
    contract = _question_contract(
        operation_ast=ast,
        operand_ids=["x0"],
        periods=[2023],
        entities=["AAA"],
        scope="consolidated",
        unit="million_vnd",
    )

    result = _select(
        question_id=question_id,
        question_contract=contract,
        plans=[failed_new],
        baseline_plan=baseline,
    )

    _assert_non_authorizing(result)
    assert result["selected_plan_id"] == "baseline-v11"
    assert result["selected_answer_decimal"] == "17"
    assert result["decision"] == "BASELINE_FALLBACK"


def test_selector_result_and_certificate_keep_candidate_out_of_strict_authority() -> None:
    question_id = 507
    ast = {"op": "lookup", "args": ["x0"]}
    plan = _plan(
        question_id,
        "best-effort-only",
        operation_ast=ast,
        operands=[_operand("x0", period=2023, value="42.75")],
        answer="42.75",
        route_family="source_first_candidate",
    )
    contract = _question_contract(
        operation_ast=ast,
        operand_ids=["x0"],
        periods=[2023],
        entities=["AAA"],
        scope="consolidated",
        unit="million_vnd",
    )
    result = _select(question_id=question_id, question_contract=contract, plans=[plan])

    _assert_non_authorizing(result)
    certificate = compile_abstention_certificate(
        question_id=question_id,
        reason_codes=["BINDING_NOT_FULLY_ELIGIBLE"],
        binding_plan={"operation_ast": ast, "operands": [{"operand_id": "x0"}]},
        best_candidate={
            "candidate_id": result["selected_plan_id"],
            "answer_decimal": result["selected_answer_decimal"],
            "filter_status": "SURVIVED_FILTER",
            "filter_passed": True,
        },
    )
    strict_view = _strict_answer_view(certificate)

    assert certificate["status"] == "ABSTAIN"
    assert certificate["answer_authorized"] is False
    assert certificate["answer_status"] == "PREDICTED_CANDIDATE"
    assert strict_view["answer"] is None
    assert strict_view["answer_decimal"] is None
    assert strict_view["strict_answer_decimal"] is None
    assert strict_view["best_effort_candidate_decimal"] == "42.75"
    assert strict_view["strict_answer_authorized"] is False
    assert strict_view["release_authorized"] is False
    assert strict_view["training_eligible"] is False
    assert strict_view["promotion_allowed"] is False


def test_answer_level_selection_is_invariant_to_question_id_and_has_no_allowlist() -> None:
    ast = {"op": "lookup", "args": ["x0"]}
    contract = _question_contract(
        operation_ast=ast,
        operand_ids=["x0"],
        periods=[2023],
        entities=["AAA"],
        scope="consolidated",
        unit="million_vnd",
    )

    def select_for(question_id: int) -> Mapping[str, Any]:
        return _select(
            question_id=question_id,
            question_contract=contract,
            plans=[
                _plan(
                    question_id,
                    "same-semantic-plan",
                    operation_ast=ast,
                    operands=[_operand("x0", period=2023, value="12")],
                    answer="12",
                    route_family="source_first_candidate",
                )
            ],
        )

    first = select_for(508)
    second = select_for(999908)

    _assert_non_authorizing(first)
    _assert_non_authorizing(second)
    assert first["selected_plan_id"] == second["selected_plan_id"] == "same-semantic-plan"
    assert first["selected_answer_decimal"] == second["selected_answer_decimal"] == "12"

    def keys(value: object) -> set[str]:
        if isinstance(value, Mapping):
            return set(value) | {key for nested in value.values() for key in keys(nested)}
        if isinstance(value, list):
            return {key for nested in value for key in keys(nested)}
        return set()

    assert {"question_id_allowlist", "qid_allowlist", "question_id_exceptions"}.isdisjoint(
        keys(first)
    )


def test_existing_answer_certificate_policy_remains_fail_closed_without_selector() -> None:
    certificate = compile_abstention_certificate(
        question_id=509,
        reason_codes=["NO_EXECUTABLE_STAGE"],
        execution={"status": "route_incomplete"},
        best_candidate={
            "candidate_id": "candidate-509",
            "answer_decimal": "123.45",
            "filter_status": "SURVIVED_FILTER",
            "filter_passed": True,
        },
    )
    strict_view = _strict_answer_view(certificate)

    assert certificate["status"] == "ABSTAIN"
    assert certificate["answer_status"] == "PREDICTED_CANDIDATE"
    assert certificate["answer_authorized"] is False
    assert strict_view["answer"] is None
    assert strict_view["answer_decimal"] is None
    assert strict_view["strict_answer_decimal"] is None
    assert strict_view["best_effort_candidate_decimal"] == "123.45"
    assert strict_view["answer_channel"] == "BEST_EFFORT_CANDIDATE"
    assert strict_view["strict_answer_authorized"] is False
    assert strict_view["release_authorized"] is False
    assert strict_view["submission_eligible"] is False
    assert strict_view["training_eligible"] is False
    assert strict_view["promotion_allowed"] is False

    assert AUTHORIZATION_CONTRACT["authoritative_answer_requires_complete_certificate"] is True
    assert AUTHORIZATION_CONTRACT["best_effort_candidate_authority"] is False
    assert AUTHORIZATION_CONTRACT["strict_answer_authorized_by_candidate"] is False
    assert AUTHORIZATION_CONTRACT["release_authorized"] is False
    assert AUTHORIZATION_CONTRACT["training_eligible"] is False
    assert AUTHORIZATION_CONTRACT["promotion_allowed"] is False
