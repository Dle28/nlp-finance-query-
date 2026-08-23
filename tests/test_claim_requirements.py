from __future__ import annotations

from finance_query.claim_requirements import (
    build_claim_requirement_set,
    build_semantic_coverage_certificate,
    build_temporal_semantics,
    classify_composed_blocker,
    classify_route_blocker,
    validate_v13_partition,
)


def _route(**overrides):
    row = {
        "question_id": 211,
        "question": "Tổng lợi nhuận kế toán trước thuế của công ty mẹ DLG năm 2022 là bao nhiêu?",
        "route_status": "route_complete",
        "route_v1_status": "concept_lookup_candidate",
        "question_context": {
            "entities": ["DLG"],
            "entity_role": "parent",
            "scope": "separate",
            "years": [2022],
        },
        "requested_output_unit": {"kind": "currency", "unit": "vnd", "source": "literal_question"},
        "required_operations": ["reported_value"],
        "missing_operations": [],
        "reason_codes": [],
    }
    row.update(overrides)
    return row


def _complete_certificate(question_id: int = 211):
    return {
        "question_id": question_id,
        "authorization_status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
        "answer_certificate": {
            "status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
            "answer_certificate_id": "cert-1",
            "operation_ast_sha256": "a" * 64,
            "binding_receipts": [
                {
                    "binding_id": "binding-1",
                    "field_statuses": {
                        "entity_status": "PASS",
                        "entity_role_status": "PASS",
                        "scope_status": "PASS",
                        "variable_status": "PASS",
                        "period_status": "PASS",
                        "unit_status": "PASS",
                        "source_integrity_status": "PASS",
                    },
                }
            ],
        },
    }


def test_requirement_schema_keeps_identity_role_and_scope_separate() -> None:
    result = build_claim_requirement_set(_route())
    requirements = {item["dimension"]: item for item in result["requirements"]}
    assert requirements["entity.identity"]["expected"] == ["DLG"]
    assert requirements["entity.role"]["expected"] == "parent"
    assert requirements["reporting.scope"]["expected"] == "separate"
    assert requirements["accounting.basis"]["expected"] == "before_tax"
    assert requirements["source.truth_tier"]["applicability"] == "NOT_CHECKED"
    assert result["requirement_set_completeness"] == "CLAIM_COMPLETENESS_UNESTABLISHED"


def test_internal_pass_does_not_self_certify_claim_completeness() -> None:
    requirement_set = build_claim_requirement_set(
        _route(question="Chỉ tiêu của công ty mẹ DLG năm 2022 là bao nhiêu?")
    )
    result = build_semantic_coverage_certificate(requirement_set, _complete_certificate())
    assert result["internal_coverage_status"] == "INTERNALLY_COMPLETE"
    assert result["claim_completeness_status"] == "CLAIM_COMPLETENESS_UNESTABLISHED"
    assert result["claim_complete"] is False
    assert result["release_effect"] == "NONE_V13_SHADOW_ONLY"


def test_explicit_unmodeled_dimension_breaks_internal_completeness() -> None:
    requirement_set = build_claim_requirement_set(_route())
    result = build_semantic_coverage_certificate(requirement_set, _complete_certificate())
    checks = {item["dimension"]: item["status"] for item in result["proof_obligations"]}
    assert checks["accounting.basis"] == "UNRESOLVED"
    assert result["internal_coverage_status"] == "INTERNAL_COVERAGE_INCOMPLETE"


def test_composed_taxonomy_distinguishes_formula_and_operand_gaps() -> None:
    formula = classify_composed_blocker(
        _route(route_status="composed_execution_required", missing_operations=["subtract_or_difference", "stage_output_dependency"]),
        {"stages": []},
        {"authorization_status": "ABSTAIN", "answer_certificate": {}},
    )
    operand = classify_composed_blocker(
        _route(route_status="composed_execution_required", missing_operations=["reported_value", "stage_output_dependency"]),
        {"stages": []},
        {"authorization_status": "ABSTAIN", "answer_certificate": {}},
    )
    assert formula and formula["primary_blocker"] == "FORMULA_DEFINITION_INCOMPLETE"
    assert formula["secondary_blockers"] == ["OPERAND_SET_INCOMPLETE"]
    assert operand and operand["primary_blocker"] == "OPERAND_SET_INCOMPLETE"


def test_route_taxonomy_does_not_claim_verified_cause() -> None:
    result = classify_route_blocker(
        _route(route_status="route_incomplete", route_v1_status="abstain")
    )
    assert result and result["primary_blocker"] == "EVIDENCE_EXISTS_RETRIEVAL_MISSED_OR_UNESTABLISHED"
    assert result["causal_attribution_verified"] is False


def test_temporal_object_is_typed_but_source_expression_stays_unresolved() -> None:
    period = {
        "question_id": 211,
        "packet_status": "packet_blocked",
        "stages": [{"required_operands": [{"period_type": "duration", "period_column_candidates": []}]}],
    }
    result = build_temporal_semantics(_route(), period, {"authorization_status": "ABSTAIN"})
    assert result
    assert result["required_temporal_object"] == {
        "kind": "duration",
        "start": "2022-01-01",
        "end": "2022-12-31",
        "role": "current_duration",
        "requested_years": [2022],
    }
    assert result["source_expression"] is None
    assert result["proof_status"] == "UNRESOLVED"


def test_first_blocker_partition_is_disjoint_and_complete() -> None:
    counts = validate_v13_partition(
        question_ids={1, 2, 3, 4},
        composed_rows=[{"question_id": 1}],
        route_rows=[{"question_id": 2}],
        temporal_rows=[{"question_id": 3}],
        coverage_rows=[
            {"question_id": 1, "v12_certificate_status": "ABSTAIN"},
            {"question_id": 2, "v12_certificate_status": "ABSTAIN"},
            {"question_id": 3, "v12_certificate_status": "ABSTAIN"},
            {"question_id": 4, "v12_certificate_status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"},
        ],
    )
    assert counts == {"composed": 1, "route": 1, "temporal": 1, "v12_complete_shadowed": 1}
