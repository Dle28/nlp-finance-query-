import hashlib
import json

from finance_query.answer_certificates import (
    compile_abstention_certificate,
    compile_answer_certificate,
    temporal_contract_for_operand,
)
from finance_query.evidence_binding import PASS, build_evidence_binding


def _plan() -> dict:
    return {
        "operands": [
            {
                "operand_id": "numerator",
                "required": True,
                "temporal_contract": temporal_contract_for_operand(
                    years=[2022], scope="consolidated", requested_unit="VND"
                ),
            },
            {
                "operand_id": "denominator",
                "required": True,
                "temporal_contract": temporal_contract_for_operand(
                    years=[2022], scope="consolidated", requested_unit="VND"
                ),
            },
        ],
        "operation_ast": {"op": "divide", "args": ["numerator", "denominator"]},
        "formula_compatibility": {
            "formula_id": "simple_ratio_v1",
            "operand_constraints": {
                operand_id: {
                    "period_grains": ["fiscal_year"],
                    "flow_or_stock": ["flow"],
                    "comparative_bases": ["current"],
                    "semantic_units": ["monetary"],
                    "revision_policies": ["latest_valid"],
                }
                for operand_id in ("numerator", "denominator")
            },
            "cross_operand_rules": [
                {"kind": "same", "field": "entity", "operands": ["numerator", "denominator"]},
                {"kind": "same", "field": "scope", "operands": ["numerator", "denominator"]},
                {"kind": "same", "field": "normalized_currency", "operands": ["numerator", "denominator"]},
            ],
        },
    }


def _binding(operand_id: str, *, binding_id: str) -> dict:
    _ = binding_id  # IDs are content hashes; callers cannot choose them.
    document_uid = f"document-{operand_id}"
    table_uid = f"table-{operand_id}"
    digest = "a" * 64

    def cell(row_index: int, column_index: int) -> dict:
        return {
            "kind": "raw_cell",
            "document_uid": document_uid,
            "internal_table_uid": table_uid,
            "row_index": row_index,
            "column_index": column_index,
            "raw_text_sha256": digest,
        }

    document_anchor = {
        "kind": "document_metadata",
        "document_uid": document_uid,
        "raw_text_sha256": digest,
    }
    return build_evidence_binding(
        operand_id=operand_id,
        document_uid=document_uid,
        internal_table_uid=table_uid,
        source_integrity={"status": PASS, "value_cell": cell(2, 3)},
        variable_binding={
            "status": PASS,
            "variable_id": "net_revenue",
            "raw_row_label": "Doanh thu thuần",
            "recognition_method": "exact_source_alias",
            "source_anchors": [cell(2, 0)],
        },
        period_binding={
            "status": PASS,
            "raw_period_label": "Năm 2022",
            "period_years": [2022],
            "period_grain": "fiscal_year",
            "flow_or_stock": "flow",
            "start_date": "2022-01-01",
            "end_date": "2022-12-31",
            "as_of_date": None,
            "fiscal_year": 2022,
            "fiscal_quarter": None,
            "comparative_basis": "current",
            "source_anchors": [cell(0, 3)],
        },
        unit_binding={
            "status": PASS,
            "raw_unit_label": "Đồng",
            "normalized_currency": "VND",
            "scale": "1",
            "semantic_unit": "monetary",
            "resolution_level": "table",
            "conversion_status": "NOT_REQUIRED",
            "source_anchors": [cell(0, 3)],
        },
        entity_scope_binding={
            "entity_status": PASS,
            "scope_status": PASS,
            "entity": "AAA",
            "scope": "consolidated",
            "source_anchors": [document_anchor],
        },
        entity_role_binding={
            "status": PASS,
            "role": "parent",
            "recognition_method": "human_verified_document_role_v1",
            "source_anchors": [document_anchor],
        },
        revision_binding={
            "status": PASS,
            "revision_policy": "latest_valid",
            "audit_status": "audited",
            "revision_status": "original",
            "publication_date": "2023-03-31",
            "effective_date": "2023-03-31",
            "effective_version": "original-audited-v1",
            "source_document_uid": document_uid,
            "supersedes_document_uid": None,
            "source_anchors": [document_anchor],
        },
        binding_lineage={
            "document_sha256": "b" * 64,
            "table_sha256": "c" * 64,
            "raw_cell_sha256": digest,
            "header_evidence_sha256": hashlib.sha256(
                json.dumps(
                    {
                        "period_source_anchors": [cell(0, 3)],
                        "unit_source_anchors": [cell(0, 3)],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "binding_schema_version": 2,
            "resolver_version": "evidence-binding-resolver-v1",
        },
    )


def _alternative_checks() -> list[dict]:
    return [
        {
            "dimension": dimension,
            "status": "REJECTED",
            "rejection_code": "CONFLICTS_WITH_EXACT_HEADER",
            "alternative_binding_id": f"wrong-{dimension}",
        }
        for dimension in ("period", "scope", "unit")
    ]


def _operation_sha(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(plan["operation_ast"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_complete_answer_certificate_is_campaign_only_not_a_promotion() -> None:
    plan = _plan()
    execution = {
        "status": "PASS",
        "answer_decimal": "1.25",
        "operation_ast_sha256": _operation_sha(plan),
    }
    certificate = compile_answer_certificate(
        question_id=1,
        binding_plan=plan,
        operand_bindings=[_binding("numerator", binding_id="b1"), _binding("denominator", binding_id="b2")],
        execution=execution,
        alternative_checks=_alternative_checks(),
    )
    assert certificate["status"] == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
    assert certificate["abstain_reason_codes"] == []
    assert certificate["training_eligible"] is False
    assert certificate["promotion_allowed"] is False
    assert certificate["serving_eligible"] is False


def test_missing_unit_counterfactual_forces_abstention_even_when_arithmetic_passes() -> None:
    plan = _plan()
    execution = {
        "status": "PASS",
        "answer_decimal": "1.25",
        "operation_ast_sha256": _operation_sha(plan),
    }
    checks = [item for item in _alternative_checks() if item["dimension"] != "unit"]
    certificate = compile_answer_certificate(
        question_id=2,
        binding_plan=plan,
        operand_bindings=[_binding("numerator", binding_id="b1"), _binding("denominator", binding_id="b2")],
        execution=execution,
        alternative_checks=checks,
    )
    assert certificate["status"] == "ABSTAIN"
    assert "COUNTERFACTUAL_UNCHECKED:unit" in certificate["abstain_reason_codes"]


def test_exhausted_hash_bound_candidate_set_satisfies_campaign_counterfactual() -> None:
    plan = _plan()
    checks = [
        {
            "dimension": dimension,
            "status": "EXHAUSTED",
            "rejection_code": "NO_ALTERNATIVE_IN_HASH_BOUND_EXACT_BINDING_SET",
            "alternative_binding_id": "none-in-bounded-set",
            "candidate_count": 2,
            "candidate_set_sha256": "d" * 64,
            "enumeration_complete": True,
        }
        for dimension in ("period", "scope", "unit")
    ]
    certificate = compile_answer_certificate(
        question_id=2,
        binding_plan=plan,
        operand_bindings=[_binding("numerator", binding_id="b1"), _binding("denominator", binding_id="b2")],
        execution={
            "status": "PASS",
            "answer_decimal": "1.25",
            "operation_ast_sha256": _operation_sha(plan),
        },
        alternative_checks=checks,
    )
    assert certificate["status"] == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"


def test_explicit_entity_role_requires_its_own_counterfactual_dimension() -> None:
    plan = _plan()
    for operand in plan["operands"]:
        operand["temporal_contract"]["requested_entity_role"] = "parent"
    checks = _alternative_checks()
    execution = {
        "status": "PASS",
        "answer_decimal": "1.25",
        "operation_ast_sha256": _operation_sha(plan),
    }
    blocked = compile_answer_certificate(
        question_id=3,
        binding_plan=plan,
        operand_bindings=[_binding("numerator", binding_id="b1"), _binding("denominator", binding_id="b2")],
        execution=execution,
        alternative_checks=checks,
    )
    assert "COUNTERFACTUAL_UNCHECKED:entity_role" in blocked["abstain_reason_codes"]

    checks.append({
        "dimension": "entity_role",
        "status": "EXHAUSTED",
        "rejection_code": "NO_ALTERNATIVE_IN_HASH_BOUND_EXACT_BINDING_SET",
        "alternative_binding_id": "none-in-bounded-set",
        "candidate_count": 2,
        "candidate_set_sha256": "e" * 64,
        "enumeration_complete": True,
    })
    complete = compile_answer_certificate(
        question_id=3,
        binding_plan=plan,
        operand_bindings=[_binding("numerator", binding_id="b1"), _binding("denominator", binding_id="b2")],
        execution=execution,
        alternative_checks=checks,
    )
    assert complete["status"] == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"


def test_no_executable_stage_emits_a_hash_bound_abstention_certificate() -> None:
    certificate = compile_abstention_certificate(
        question_id=3,
        reason_codes=["NO_UNIQUE_EXECUTABLE_STAGE"],
    )
    assert certificate["status"] == "ABSTAIN"
    assert certificate["binding_receipts"] == []
    assert certificate["training_eligible"] is False
