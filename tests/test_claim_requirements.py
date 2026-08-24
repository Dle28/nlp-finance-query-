from __future__ import annotations

from copy import deepcopy

import pytest

from finance_query.claim_requirements import (
    build_claim_requirement_set,
    build_semantic_coverage_certificate,
    build_temporal_semantics,
    claim_temporal_object,
    classify_composed_blocker,
    classify_route_blocker,
    validate_dependency_graph,
    validate_v13_partition,
)


def _route(**overrides):
    row = {
        "question_id": 211,
        "question": "Tổng lợi nhuận kế toán trước thuế của công ty mẹ DLG năm 2022 là bao nhiêu?",
        "route_status": "route_complete",
        "route_v1_status": "concept_lookup_candidate",
        "question_context": {"entities": ["DLG"], "entity_role": "parent", "scope": "separate", "years": [2022]},
        "requested_output_unit": {"kind": "currency", "unit": "vnd", "source": "literal_question"},
        "required_operations": ["reported_value"],
        "covered_operations": ["reported_value"],
        "missing_operations": [],
        "reason_codes": [],
    }
    row.update(overrides)
    return row


def _complete_certificate(question_id: int = 211, *, entity: str = "DLG", source_status: str = "PASS"):
    anchor = {
        "kind": "document_text_line",
        "document_uid": "doc-1",
        "line_number": 1,
        "raw_text_sha256": "b" * 64,
    }
    return {
        "question_id": question_id,
        "authorization_status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
        "answer_certificate": {
            "status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
            "answer_certificate_id": "cert-1",
            "operation_ast_sha256": "a" * 64,
            "binding_receipts": [{
                "binding_id": "a" * 64,
                "source_value_cell": {"kind": "raw_cell", "document_uid": "doc-1", "raw_text_sha256": "c" * 64},
                "entity_scope": {"approval_id": "d" * 64, "entity": entity, "entity_status": "PASS", "scope": "separate", "scope_status": "PASS", "source_anchors": [anchor]},
                "entity_role": {"approval_id": "e" * 64, "role": "parent", "status": "PASS", "source_anchors": [anchor]},
                "period": {"period_years": [2022], "period_grain": "fiscal_year", "start_date": "2022-01-01", "end_date": "2022-12-31", "status": "PASS", "source_anchors": [anchor]},
                "field_statuses": {
                    "entity_status": "PASS", "entity_role_status": "PASS", "scope_status": "PASS",
                    "variable_status": "PASS", "period_status": "PASS", "unit_status": "PASS",
                    "source_integrity_status": source_status,
                },
            }],
        },
    }


def _requirements(result):
    return {item["dimension"]: item for item in result["requirements"]}


def test_schema_separates_identity_role_scope_and_metric_tax_treatment() -> None:
    requirements = _requirements(build_claim_requirement_set(_route()))
    assert requirements["entity.identity"]["expected"] == ["DLG"]
    assert requirements["entity.role"]["expected"] == "parent"
    assert requirements["reporting.scope"]["applicability"] == "NOT_CHECKED"
    assert requirements["metric.tax_treatment"]["expected"] == "before_tax"
    assert requirements["source.truth_tier"]["applicability"] == "NOT_CHECKED"


def test_raw_claim_role_is_required_even_when_context_parser_misses_it() -> None:
    route = _route(question_context={"entities": ["DLG"], "years": [2022]})
    assert _requirements(build_claim_requirement_set(route))["entity.role"]["applicability"] == "REQUIRED"


def test_disagreeing_raw_entity_parse_fails_closed_instead_of_inventing_identity() -> None:
    route = _route(question_context={"entities": ["VSC"], "years": [2022]})
    requirement_set = build_claim_requirement_set(route)
    identity_requirement = _requirements(requirement_set)["entity.identity"]
    assert identity_requirement["applicability"] == "NOT_CHECKED"
    assert identity_requirement["expected"] is None
    assert "AMBIGUOUS_RAW_ENTITY_PARSE_CONTEXT_DISAGREEMENT" in requirement_set["reason_codes"]
    result = build_semantic_coverage_certificate(requirement_set, _complete_certificate(entity="VSC"))
    identity = next(item for item in result["proof_obligations"] if item["dimension"] == "entity.identity")
    assert identity["status"] == "NOT_CHECKED"


def test_legal_name_ticker_collision_is_not_asserted_as_reporting_entity() -> None:
    route = _route(
        question_id=4,
        question="Lợi nhuận sau thuế của CTCP Chứng khoán FPT năm 2023 là bao nhiêu tỷ đồng?",
        question_context={"entities": ["FTS", "FPT"], "years": [2023]},
    )
    requirement_set = build_claim_requirement_set(route)
    identity = _requirements(requirement_set)["entity.identity"]
    assert identity["applicability"] == "NOT_CHECKED"
    assert identity["expected"] is None
    assert identity["requirement_basis"] == "raw_claim_entity_ambiguous_context_disagreement"


def test_entity_without_raw_literal_is_not_self_certified_from_context() -> None:
    route = _route(question="Tổng lợi nhuận kế toán trước thuế năm 2022 là bao nhiêu?")
    assert _requirements(build_claim_requirement_set(route))["entity.identity"]["applicability"] == "NOT_CHECKED"


def test_generic_finance_acronyms_are_not_invented_as_issuers() -> None:
    route = _route(
        question="Lợi nhuận sau thuế TNDN của Ngân hàng TMCP năm 2022 là bao nhiêu?",
        question_context={"entities": ["STB"], "years": [2022]},
    )
    identity = _requirements(build_claim_requirement_set(route))["entity.identity"]
    assert identity["applicability"] == "NOT_CHECKED"
    assert identity["expected"] is None


def test_multi_entity_claim_uses_only_closed_registry_tickers() -> None:
    route = _route(
        question="Trong nhóm CEO, HPX, KBC và TNDN năm 2022, chỉ tiêu là bao nhiêu?",
        question_context={"entities": ["CEO", "HPX", "KBC"], "years": [2022]},
    )
    assert _requirements(build_claim_requirement_set(route))["entity.identity"]["expected"] == ["CEO", "HPX", "KBC"]


def test_absent_role_is_not_silently_not_applicable() -> None:
    route = _route(question="Chỉ tiêu của DLG năm 2022 là bao nhiêu?")
    assert _requirements(build_claim_requirement_set(route))["entity.role"]["applicability"] == "NOT_CHECKED"


@pytest.mark.parametrize(
    "question",
    [
        "Đầu tư vào các công ty con của công ty mẹ HBC cuối năm 2019 là bao nhiêu tỷ đồng?",
        "CTCP Tập đoàn Thủy sản Minh Phú (MPC) (công ty mẹ) có khoản đầu tư vào công ty con bao nhiêu?",
    ],
)
def test_metric_counterparty_subsidiary_phrase_does_not_erase_parent_reporting_role(question: str) -> None:
    route = _route(question=question, question_context={"entities": ["HBC"], "years": [2019]})
    role = _requirements(build_claim_requirement_set(route))["entity.role"]
    assert role["applicability"] == "REQUIRED"
    assert role["expected"] == "parent"


def test_v12_complete_cannot_self_certify_v13_formula_or_internal_completeness() -> None:
    result = build_semantic_coverage_certificate(build_claim_requirement_set(_route()), _complete_certificate())
    checks = {item["dimension"]: item for item in result["proof_obligations"]}
    assert checks["formula.definition"]["status"] == "UNRESOLVED"
    assert checks["operand.set"]["status"] == "UNRESOLVED"
    assert checks["operand.compatibility"]["status"] == "UNRESOLVED"
    assert result["internal_coverage_status"] == "INTERNAL_COVERAGE_INCOMPLETE"
    assert result["claim_completeness_status"] == "CLAIM_COMPLETENESS_UNESTABLISHED"
    assert result["claim_complete"] is False


def test_expected_proposition_mismatch_cannot_inherit_generic_pass() -> None:
    result = build_semantic_coverage_certificate(build_claim_requirement_set(_route()), _complete_certificate(entity="OTHER"))
    identity = next(item for item in result["proof_obligations"] if item["dimension"] == "entity.identity")
    assert identity["status"] == "FAIL"
    assert identity["actual"] == ["OTHER"]


def test_generic_pass_without_typed_anchor_cannot_prove_entity() -> None:
    certificate = _complete_certificate()
    certificate["answer_certificate"]["binding_receipts"][0]["entity_scope"].pop("source_anchors")
    result = build_semantic_coverage_certificate(build_claim_requirement_set(_route()), certificate)
    identity = next(item for item in result["proof_obligations"] if item["dimension"] == "entity.identity")
    assert identity["status"] == "UNRESOLVED"


def test_typed_anchor_from_unrelated_document_cannot_prove_any_proposition() -> None:
    certificate = _complete_certificate()
    receipt = certificate["answer_certificate"]["binding_receipts"][0]
    for section_name in ("entity_scope", "entity_role", "period"):
        receipt[section_name]["source_anchors"][0]["document_uid"] = "unrelated-doc"
    result = build_semantic_coverage_certificate(build_claim_requirement_set(_route()), certificate)
    checks = {item["dimension"]: item for item in result["proof_obligations"]}
    assert checks["source.integrity"]["status"] == "PASS"
    assert checks["entity.identity"]["status"] == "UNRESOLVED"
    assert checks["entity.role"]["status"] == "UNRESOLVED"
    assert checks["temporal.period"]["status"] == "UNRESOLVED"


def test_obligation_id_binds_full_proposition_not_only_dimension() -> None:
    dlg = _requirements(build_claim_requirement_set(_route()))["entity.identity"]
    vsc = _requirements(build_claim_requirement_set(_route(question="Chỉ tiêu của VSC năm 2022 là bao nhiêu?")))["entity.identity"]
    assert dlg["dimension"] == vsc["dimension"]
    assert dlg["expected"] != vsc["expected"]
    assert dlg["obligation_id"] != vsc["obligation_id"]


def test_dependency_not_pass_demotes_child_pass() -> None:
    result = build_semantic_coverage_certificate(
        build_claim_requirement_set(_route()),
        _complete_certificate(source_status="UNRESOLVED"),
    )
    identity = next(item for item in result["proof_obligations"] if item["dimension"] == "entity.identity")
    assert identity["status"] == "UNRESOLVED"


def test_dependency_graph_rejects_missing_node_and_cycle() -> None:
    with pytest.raises(ValueError, match="missing dependencies"):
        validate_dependency_graph([{"dimension": "a", "depends_on": ["b"]}])
    with pytest.raises(ValueError, match="cycle"):
        validate_dependency_graph([
            {"dimension": "a", "depends_on": ["b"]},
            {"dimension": "b", "depends_on": ["a"]},
        ])


def test_temporal_requirement_is_claim_derived_and_preserves_exact_date() -> None:
    exact = claim_temporal_object("Giá trị tại ngày 30/06/2022 là bao nhiêu?", [2022])
    assert exact["kind"] == "instant"
    assert exact["end"] == "2022-06-30"
    assert claim_temporal_object("Doanh thu năm 2022", [2022])["start"] is None


def test_vietnamese_d_stroke_normalizes_for_opening_period_role() -> None:
    result = claim_temporal_object("Giá trị đầu năm 2022 là bao nhiêu?", [2022])
    assert result["kind"] == "instant"
    assert result["role"] == "opening"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Giá trị đến ngày 31 tháng 12 năm 2016 là bao nhiêu?", "2016-12-31"),
        ("Giá trị tính đến ngày 30 tháng 6 năm 2022 là bao nhiêu?", "2022-06-30"),
    ],
)
def test_vietnamese_textual_date_is_an_exact_instant(question: str, expected: str) -> None:
    result = claim_temporal_object(question, [int(expected[:4])])
    assert result["kind"] == "instant"
    assert result["end"] == expected


def test_invalid_calendar_date_fails_closed() -> None:
    result = claim_temporal_object("Giá trị tại ngày 31/02/2022 là bao nhiêu?", [2022])
    assert result["kind"] == "UNRESOLVED"
    assert result["derivation"] == "invalid_exact_date_literal"


def test_raw_year_controls_applicability_and_flags_context_disagreement() -> None:
    missing_context = _route(question_context={"entities": ["DLG"]})
    assert _requirements(build_claim_requirement_set(missing_context))["temporal.period"]["applicability"] == "REQUIRED"
    wrong_context = _route(question_context={"entities": ["DLG"], "years": [2021]})
    result = build_claim_requirement_set(wrong_context)
    assert _requirements(result)["temporal.period"]["expected"]["requested_years"] == [2022]
    assert "CONTEXT_TEMPORAL_DISAGREES_WITH_RAW_CLAIM" in result["reason_codes"]


def test_exact_date_mismatch_cannot_pass_on_year_and_kind_only() -> None:
    route = _route(question="Giá trị của DLG tại ngày 30/06/2022 là bao nhiêu?")
    certificate = deepcopy(_complete_certificate())
    period = certificate["answer_certificate"]["binding_receipts"][0]["period"]
    period.update({"period_grain": "instant", "start_date": None, "end_date": "2022-12-31"})
    result = build_semantic_coverage_certificate(build_claim_requirement_set(route), certificate)
    temporal = next(item for item in result["proof_obligations"] if item["dimension"] == "temporal.period")
    assert temporal["status"] == "FAIL"


def test_period_dates_inconsistent_with_declared_year_are_unresolved_not_pass() -> None:
    certificate = deepcopy(_complete_certificate())
    period = certificate["answer_certificate"]["binding_receipts"][0]["period"]
    period.update({"start_date": "1999-01-01", "end_date": "1999-12-31"})
    result = build_semantic_coverage_certificate(build_claim_requirement_set(_route()), certificate)
    temporal = next(item for item in result["proof_obligations"] if item["dimension"] == "temporal.period")
    assert temporal["status"] == "UNRESOLVED"


def test_missing_role_required_period_date_is_unresolved_not_false_contradiction() -> None:
    route = _route(question="Giá trị đầu năm 2022 của DLG là bao nhiêu?")
    certificate = deepcopy(_complete_certificate())
    period = certificate["answer_certificate"]["binding_receipts"][0]["period"]
    period.update({"period_grain": "instant", "start_date": None, "end_date": None})
    result = build_semantic_coverage_certificate(build_claim_requirement_set(route), certificate)
    temporal = next(item for item in result["proof_obligations"] if item["dimension"] == "temporal.period")
    assert temporal["status"] == "UNRESOLVED"


def test_source_period_packet_cannot_change_claim_requirement() -> None:
    route = _route(question="Giá trị tại ngày 30/06/2022 là bao nhiêu?")
    instant = {"packet_status": "packet_blocked", "stages": [{"required_operands": [{"period_type": "instant", "period_column_candidates": []}]}]}
    duration = {"packet_status": "packet_blocked", "stages": [{"required_operands": [{"period_type": "duration", "period_column_candidates": []}]}]}
    a = build_temporal_semantics(route, instant, {"authorization_status": "ABSTAIN"})
    b = build_temporal_semantics(route, duration, {"authorization_status": "ABSTAIN"})
    assert a and b and a["claim_requirement"] == b["claim_requirement"]
    assert a["kind_match"] is True and b["kind_match"] is False


def test_composed_taxonomy_never_claims_compatibility_evaluated_without_receipt() -> None:
    result = classify_composed_blocker(
        _route(route_status="composed_execution_required", missing_operations=["subtract_or_difference", "stage_output_dependency"]),
        {"stages": [{"required_operands": [{}, {}]}]},
        {"authorization_status": "ABSTAIN"},
    )
    assert result and result["primary_blocker"] == "FORMULA_DEFINITION_INCOMPLETE"
    assert result["planned_operand_count"] == 2
    assert result["compatibility_evaluable"] is True
    assert result["compatibility_evaluated"] is False


def test_route_ratio_gap_is_not_mislabeled_as_table_metric_gap() -> None:
    result = classify_route_blocker(_route(route_status="route_incomplete", missing_operations=["ratio_or_percent"], covered_operations=["reported_value"]))
    assert result and result["primary_blocker"] == "FORMULA_OR_OPERATOR_DEFINITION_UNRESOLVED"


def test_route_unknown_cause_stays_unestablished() -> None:
    result = classify_route_blocker(_route(route_status="route_incomplete", route_v1_status="abstain", missing_operations=[], covered_operations=[]))
    assert result and result["primary_blocker"] == "ROUTE_CAUSE_UNESTABLISHED"
    assert result["causal_attribution_verified"] is False


def test_first_blocker_partition_is_disjoint_and_complete() -> None:
    counts = validate_v13_partition(
        question_ids={1, 2, 3, 4},
        composed_rows=[{"question_id": 1}], route_rows=[{"question_id": 2}], temporal_rows=[{"question_id": 3}],
        coverage_rows=[
            {"question_id": 1, "v12_certificate_status": "ABSTAIN"},
            {"question_id": 2, "v12_certificate_status": "ABSTAIN"},
            {"question_id": 3, "v12_certificate_status": "ABSTAIN"},
            {"question_id": 4, "v12_certificate_status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"},
        ],
    )
    assert counts["v12_complete_shadowed"] == 1
