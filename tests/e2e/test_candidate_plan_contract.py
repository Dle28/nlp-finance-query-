from __future__ import annotations

from decimal import Decimal
import json

import pytest

from finance_query.e2e.core.candidate_plan_contract import (
    CandidateOperand,
    CandidatePlan,
    CandidatePlanSet,
    CandidatePlanValidationError,
)


SOURCE_HASH = "a" * 64


def _operand(operand_id: str = "x0", *, raw_value: object = "10") -> CandidateOperand:
    return CandidateOperand(
        operand_id=operand_id,
        source="document-1",
        table="table-1",
        row=2,
        column=3,
        period=2024,
        entity="AAA",
        scope="separate",
        unit="VND",
        raw_value=raw_value,
    )


def _plan(*, question_id: int | str | None = 1, status: str = "CANDIDATE") -> CandidatePlan:
    return CandidatePlan(
        question_id=question_id,
        operation_ast={"op": "add", "args": ["x0", "x1"]},
        operands=[_operand("x0"), _operand("x1", raw_value="2")],
        pandas_query="table == 'table-1'",
        replay_answer_decimal=Decimal("12"),
        source_hash=SOURCE_HASH,
        semantic_completeness=1.0,
        route_family="composed_arithmetic",
        verification_status=status,
    )


def test_valid_plan_serializes_as_candidate_without_authority() -> None:
    plan = _plan()
    payload = plan.to_dict()

    assert tuple(plan.operands)[0].source_uid == "document-1"
    assert tuple(plan.operands)[0].table_uid == "table-1"
    assert payload["replay_answer_decimal"] == "12"
    assert payload["candidate_only"] is True
    assert payload["answer_authority"] is False
    assert payload["release_authorized"] is False
    assert plan.is_verified is False
    assert plan.candidate_only is True


def test_argmax_period_metadata_must_cover_typed_operands() -> None:
    plan = CandidatePlan(
        question_id=1,
        operation_ast={
            "op": "argmax",
            "over_periods": [2023, 2024],
            "select_metric": "doanh thu",
        },
        operands=[
            CandidateOperand(
                operand_id="period_2023",
                source="document-1",
                table="table-1",
                row=2,
                column=2,
                period=2023,
                entity="AAA",
                scope="separate",
                unit="VND",
                raw_value="9",
            ),
            CandidateOperand(
                operand_id="period_2024",
                source="document-1",
                table="table-1",
                row=2,
                column=3,
                period=2024,
                entity="AAA",
                scope="separate",
                unit="VND",
                raw_value="10",
            ),
        ],
        pandas_query="table == 'table-1'",
        replay_answer_decimal=Decimal("2024"),
        source_hash=SOURCE_HASH,
        semantic_completeness=1.0,
        route_family="argmax",
    )

    assert plan.operation_ast["op"] == "argmax"


def test_missing_operand_is_rejected_fail_closed() -> None:
    with pytest.raises(CandidatePlanValidationError, match="at least one operand"):
        CandidatePlan(
            question_id=1,
            operation_ast={"op": "lookup", "args": ["x0"]},
            operands=[],
            pandas_query="table == 'table-1'",
            replay_answer_decimal=Decimal("10"),
            source_hash=SOURCE_HASH,
            semantic_completeness=1.0,
            route_family="direct_lookup",
        )


def test_ast_missing_is_rejected_fail_closed() -> None:
    with pytest.raises(CandidatePlanValidationError, match="operation_ast"):
        CandidatePlan(
            question_id=1,
            operation_ast=None,
            operands=[_operand()],
            pandas_query="table == 'table-1'",
            replay_answer_decimal=Decimal("10"),
            source_hash=SOURCE_HASH,
            semantic_completeness=1.0,
            route_family="direct_lookup",
        )


def test_duplicate_operand_ids_are_rejected() -> None:
    with pytest.raises(CandidatePlanValidationError, match="duplicate operand_id=x0"):
        CandidatePlan(
            question_id=1,
            operation_ast={"op": "add", "args": ["x0"]},
            operands=[_operand("x0"), _operand("x0", raw_value="2")],
            pandas_query="table == 'table-1'",
            replay_answer_decimal=Decimal("12"),
            source_hash=SOURCE_HASH,
            semantic_completeness=1.0,
            route_family="direct_lookup",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("replay_answer_decimal", Decimal("NaN")),
        ("replay_answer_decimal", Decimal("Infinity")),
        ("semantic_completeness", float("nan")),
    ],
)
def test_non_finite_decimals_are_rejected(field: str, value: object) -> None:
    kwargs = {
        "question_id": 1,
        "operation_ast": {"op": "lookup", "args": ["x0"]},
        "operands": [_operand()],
        "pandas_query": "table == 'table-1'",
        "replay_answer_decimal": Decimal("10"),
        "source_hash": SOURCE_HASH,
        "semantic_completeness": 1.0,
        "route_family": "direct_lookup",
    }
    kwargs[field] = value
    with pytest.raises(CandidatePlanValidationError, match="finite"):
        CandidatePlan(**kwargs)


def test_non_finite_operand_raw_value_is_rejected() -> None:
    with pytest.raises(CandidatePlanValidationError, match="finite"):
        _operand(raw_value=Decimal("-Infinity"))


def test_stable_serialization_round_trips_and_ignores_mapping_insertion_order() -> None:
    plan = _plan(question_id="Q-1")
    payload = plan.to_dict()
    payload["operation_ast"] = {"args": ["x0", "x1"], "op": "add"}
    round_trip = CandidatePlan.from_dict(payload)

    assert plan.stable_json() == round_trip.stable_json()
    assert plan.to_json() == plan.stable_json()
    assert plan.fingerprint() == round_trip.fingerprint()
    assert json.loads(plan.stable_json())["question_id"] == "Q-1"


def test_candidate_plan_set_is_bounded_deduplicated_and_fallback_is_last() -> None:
    complete = _plan(question_id=1)
    duplicate_with_different_tracking_id = _plan(question_id=999)
    partial = CandidatePlan(
        question_id=2,
        operation_ast={"op": "lookup", "args": ["x0"]},
        operands=[_operand()],
        pandas_query="table == 'table-1'",
        replay_answer_decimal=None,
        source_hash=SOURCE_HASH,
        semantic_completeness=0.4,
        route_family="source_first",
        verification_status="PARTIAL",
    )
    fallback = CandidatePlan.fallback(question_id=3, replay_answer_decimal=Decimal("0"))

    plan_set = CandidatePlanSet(
        [fallback, partial, duplicate_with_different_tracking_id, complete],
        max_plans=3,
    )

    assert len(plan_set) == 3
    assert plan_set.best() is not None
    assert plan_set.best().dedup_key() == complete.dedup_key()
    assert [plan.route_family for plan in plan_set] == [
        "composed_arithmetic",
        "source_first",
        "fallback",
    ]
    assert plan_set.to_dict()["answer_authority"] is False


def test_fallback_candidate_is_explicit_and_cannot_claim_source_or_verified_state() -> None:
    fallback = CandidatePlan.fallback(question_id=42, replay_answer_decimal="7")

    assert fallback.operands == ()
    assert fallback.operation_ast == {"op": "fallback", "args": []}
    assert fallback.verification_status == "FALLBACK"
    assert fallback.semantic_completeness == 0.0
    assert fallback.source_hash is None
    assert fallback.pandas_query is None
    assert fallback.answer_authority is False

    with pytest.raises(CandidatePlanValidationError, match="authority state"):
        CandidatePlan(
            question_id=42,
            operation_ast={"op": "lookup", "args": ["x0"]},
            operands=[_operand()],
            pandas_query="table == 'table-1'",
            replay_answer_decimal=Decimal("7"),
            source_hash=SOURCE_HASH,
            semantic_completeness=1.0,
            route_family="direct_lookup",
            verification_status="VERIFIED",
        )
