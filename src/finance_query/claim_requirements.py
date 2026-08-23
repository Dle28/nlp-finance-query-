"""V13 claim-requirement and semantic-coverage contracts.

This module is an additive, research-only overlay.  It never mutates a V12
binding or answer certificate and it cannot authorize release, training, or a
submission.  Its purpose is to make the *proof obligations generated from a
claim* auditable, including dimensions that the current generator did not
check.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REQUIREMENT_PROTOCOL = "vifinqa_claim_requirement_set_v1"
COVERAGE_PROTOCOL = "vifinqa_semantic_coverage_certificate_v2"
COMPOSED_TAXONOMY_PROTOCOL = "vifinqa_composed_blocker_taxonomy_v1"
ROUTE_TAXONOMY_PROTOCOL = "vifinqa_route_blocker_taxonomy_v2"
TEMPORAL_PROTOCOL = "vifinqa_temporal_semantics_v1"

PROOF_STATUSES = {"PASS", "FAIL", "UNRESOLVED", "NOT_APPLICABLE", "NOT_CHECKED"}
COMPLETENESS_STATUSES = {
    "INTERNALLY_COMPLETE",
    "INTERNAL_COVERAGE_INCOMPLETE",
    "CLAIM_COMPLETE",
    "CLAIM_COMPLETENESS_UNESTABLISHED",
}


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def index_by_question(rows: Iterable[Mapping[str, Any]], label: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in rows:
        question_id = raw.get("question_id")
        if not isinstance(question_id, int) or isinstance(question_id, bool):
            raise ValueError(f"{label} requires integer question_id")
        if question_id in result:
            raise ValueError(f"{label} has duplicate question_id {question_id}")
        result[question_id] = dict(raw)
    return result


def source_contract() -> dict[str, bool]:
    return {
        "research_only": True,
        "evidence_eligible": False,
        "may_materialize_answer": False,
        "may_execute_formula": False,
        "promotion_allowed": False,
        "training_eligible": False,
        "submission_eligible": False,
        "release_authorized": False,
    }


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(value.casefold().split())


def _requirement(
    dimension: str,
    applicability: str,
    *,
    expected: object = None,
    basis: str,
    depends_on: Sequence[str] = (),
) -> dict[str, Any]:
    if applicability not in {"REQUIRED", "NOT_APPLICABLE", "NOT_CHECKED"}:
        raise ValueError(f"invalid applicability {applicability}")
    return {
        "dimension": dimension,
        "applicability": applicability,
        "expected": expected,
        "requirement_basis": basis,
        "depends_on": list(depends_on),
    }


def _explicit_statement_role(question: str) -> str | None:
    text = _normalized(question)
    rules = (
        ("balance_sheet", ("bang can doi ke toan", "bang can doi")),
        ("income_statement", ("bao cao ket qua kinh doanh", "ket qua hoat dong kinh doanh")),
        ("cash_flow_statement", ("bao cao luu chuyen tien te", "luu chuyen tien te")),
    )
    matches = [role for role, phrases in rules if any(phrase in text for phrase in phrases)]
    return matches[0] if len(matches) == 1 else None


def _explicit_accounting_basis(question: str) -> str | None:
    text = _normalized(question)
    rules = (
        ("before_tax", ("truoc thue",)),
        ("after_tax", ("sau thue",)),
        ("gross", ("loi nhuan gop", "gia tri gop")),
        ("net", ("loi nhuan thuan", "thu nhap thuan", "gia tri thuan")),
    )
    matches = [basis for basis, phrases in rules if any(phrase in text for phrase in phrases)]
    return matches[0] if len(matches) == 1 else None


def build_claim_requirement_set(route: Mapping[str, Any]) -> dict[str, Any]:
    """Generate explicit obligations without claiming the set is exhaustive."""

    question_id = int(route["question_id"])
    question = str(route.get("question") or "")
    context = route.get("question_context") if isinstance(route.get("question_context"), Mapping) else {}
    entities = [str(value) for value in context.get("entities") or [] if str(value)]
    years = [int(value) for value in context.get("years") or [] if isinstance(value, int)]
    requested_unit = route.get("requested_output_unit") if isinstance(route.get("requested_output_unit"), Mapping) else {}
    required_operations = [str(value) for value in route.get("required_operations") or []]
    statement_role = _explicit_statement_role(question)
    accounting_basis = _explicit_accounting_basis(question)
    is_composed = route.get("route_status") == "composed_execution_required"

    requirements = [
        _requirement(
            "entity.identity",
            "REQUIRED" if entities else "NOT_CHECKED",
            expected=entities or None,
            basis="question_context.entities",
            depends_on=("source.integrity",),
        ),
        _requirement(
            "entity.role",
            "REQUIRED" if context.get("entity_role") else "NOT_APPLICABLE",
            expected=context.get("entity_role"),
            basis="explicit_question_literal" if context.get("entity_role") else "no_explicit_role_requirement",
            depends_on=("entity.identity", "source.integrity"),
        ),
        _requirement(
            "reporting.scope",
            "REQUIRED" if context.get("scope") else "NOT_CHECKED",
            expected=context.get("scope"),
            basis="question_context.scope",
            depends_on=("entity.identity", "source.integrity"),
        ),
        _requirement(
            "statement.role",
            "REQUIRED" if statement_role else "NOT_CHECKED",
            expected=statement_role,
            basis="explicit_question_statement_literal" if statement_role else "dimension_unchecked_by_generator",
            depends_on=("source.integrity",),
        ),
        _requirement(
            "variable.metric",
            "REQUIRED",
            expected=None,
            basis="reported_value_or_formula_operand_required",
            depends_on=("source.integrity",),
        ),
        _requirement(
            "temporal.period",
            "REQUIRED" if years else "NOT_CHECKED",
            expected=years or None,
            basis="question_context.years",
            depends_on=("source.integrity",),
        ),
        _requirement(
            "unit.scale",
            "REQUIRED",
            expected={
                "unit": requested_unit.get("unit") or "source_unit",
                "kind": requested_unit.get("kind") or "source_unit",
            },
            basis=str(requested_unit.get("source") or "source_unit_must_be_bound"),
            depends_on=("source.integrity",),
        ),
        _requirement(
            "accounting.basis",
            "REQUIRED" if accounting_basis else "NOT_CHECKED",
            expected=accounting_basis,
            basis="explicit_claim_qualifier" if accounting_basis else "dimension_unchecked_by_generator",
            depends_on=("variable.metric", "source.integrity"),
        ),
        _requirement(
            "formula.definition",
            "REQUIRED",
            expected=required_operations,
            basis="route_operation_requirements_v3",
            depends_on=("variable.metric", "operand.set"),
        ),
        _requirement(
            "operand.set",
            "REQUIRED",
            expected={"composition_required": is_composed},
            basis="route_stage_and_population_requirements_v3",
            depends_on=("variable.metric", "temporal.period"),
        ),
        _requirement(
            "operand.compatibility",
            "REQUIRED",
            expected=None,
            basis="formula_specific_compatibility_required",
            depends_on=("operand.set", "formula.definition"),
        ),
        _requirement(
            "source.integrity",
            "REQUIRED",
            expected=None,
            basis="immutable_exact_source_lineage_required",
        ),
        _requirement(
            "source.truth_tier",
            "NOT_CHECKED",
            expected=["ocr_derived", "visual_source_verified", "source_native"],
            basis="v12_does_not_classify_source_truth_tier",
            depends_on=("source.integrity",),
        ),
    ]
    payload = {
        "schema_version": 1,
        "protocol": REQUIREMENT_PROTOCOL,
        "question_id": question_id,
        "claim": question,
        "generator": {
            "name": "deterministic_question_and_route_rules",
            "version": "v1",
            "independent_completeness_basis": False,
            "definition_version": "claim_requirement_schema_v1",
        },
        "requirements": requirements,
        "dependency_graph": [
            {"requirement": item["dimension"], "depends_on": item["depends_on"]}
            for item in requirements
            if item["depends_on"]
        ],
        "requirement_set_completeness": "CLAIM_COMPLETENESS_UNESTABLISHED",
        "reason_codes": ["NO_INDEPENDENT_REQUIREMENT_UNIVERSE"],
        "source_contract": source_contract(),
    }
    return {**payload, "claim_requirement_set_id": canonical_sha256(payload)}


def _binding_field_statuses(certificate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    answer = certificate.get("answer_certificate") if isinstance(certificate.get("answer_certificate"), Mapping) else {}
    return [
        receipt.get("field_statuses") or {}
        for receipt in answer.get("binding_receipts") or []
        if isinstance(receipt, Mapping)
    ]


def _all_status(receipts: Sequence[Mapping[str, Any]], field: str) -> str:
    if not receipts:
        return "UNRESOLVED"
    values = {str(receipt.get(field) or "UNRESOLVED") for receipt in receipts}
    if values == {"PASS"}:
        return "PASS"
    if "CONFLICT" in values or "FAIL" in values:
        return "FAIL"
    if values.issubset({"PASS", "NOT_APPLICABLE"}) and "PASS" in values:
        return "PASS"
    return "UNRESOLVED"


def build_semantic_coverage_certificate(
    requirement_set: Mapping[str, Any],
    certificate: Mapping[str, Any],
) -> dict[str, Any]:
    answer = certificate.get("answer_certificate") if isinstance(certificate.get("answer_certificate"), Mapping) else {}
    complete = certificate.get("authorization_status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
    receipts = _binding_field_statuses(certificate)
    field_map = {
        "entity.identity": "entity_status",
        "entity.role": "entity_role_status",
        "reporting.scope": "scope_status",
        "variable.metric": "variable_status",
        "temporal.period": "period_status",
        "unit.scale": "unit_status",
        "source.integrity": "source_integrity_status",
    }
    checks: list[dict[str, Any]] = []
    for requirement in requirement_set.get("requirements") or []:
        dimension = str(requirement["dimension"])
        applicability = str(requirement["applicability"])
        if applicability == "NOT_APPLICABLE":
            status = "NOT_APPLICABLE"
            evidence = []
        elif applicability == "NOT_CHECKED":
            status = "NOT_CHECKED"
            evidence = []
        elif dimension in field_map:
            status = _all_status(receipts, field_map[dimension])
            evidence = [
                str(receipt.get("binding_id"))
                for receipt in answer.get("binding_receipts") or []
                if isinstance(receipt, Mapping) and receipt.get("binding_id")
            ]
        elif dimension in {"formula.definition", "operand.set", "operand.compatibility"}:
            status = "PASS" if complete else "UNRESOLVED"
            evidence = [str(answer.get("operation_ast_sha256"))] if complete and answer.get("operation_ast_sha256") else []
        else:
            status = "UNRESOLVED"
            evidence = []
        if status not in PROOF_STATUSES:
            raise ValueError(f"invalid proof status {status}")
        checks.append({
            "dimension": dimension,
            "applicability": applicability,
            "status": status,
            "evidence_refs": evidence,
        })

    required_checks = [item for item in checks if item["applicability"] == "REQUIRED"]
    internally_complete = bool(required_checks) and all(item["status"] == "PASS" for item in required_checks)
    internal_status = "INTERNALLY_COMPLETE" if internally_complete else "INTERNAL_COVERAGE_INCOMPLETE"
    claim_status = "CLAIM_COMPLETENESS_UNESTABLISHED"
    payload = {
        "schema_version": 2,
        "protocol": COVERAGE_PROTOCOL,
        "question_id": int(requirement_set["question_id"]),
        "claim_requirement_set_id": requirement_set["claim_requirement_set_id"],
        "v12_answer_certificate_id": answer.get("answer_certificate_id"),
        "v12_certificate_status": answer.get("status") or "ABSTAIN",
        "proof_obligations": checks,
        "internal_coverage_status": internal_status,
        "claim_completeness_status": claim_status,
        "claim_complete": False,
        "reason_codes": [
            "NO_INDEPENDENT_REQUIREMENT_UNIVERSE",
            *([] if internally_complete else ["REQUIRED_PROOF_OBLIGATION_UNRESOLVED"]),
        ],
        "source_truth_tier": {
            "tier": "EXTRACTED_SOURCE_UNCLASSIFIED",
            "status": "UNRESOLVED",
            "reason_codes": ["V12_SOURCE_TRUTH_TIER_NOT_DECLARED"],
        },
        "release_effect": "NONE_V13_SHADOW_ONLY",
        "source_contract": source_contract(),
    }
    return {**payload, "semantic_coverage_certificate_id": canonical_sha256(payload)}


FORMULA_DEFINITION_OPERATIONS = {
    "subtract_or_difference",
    "ratio_or_percent",
    "average_or_median",
    "min_max_ranking",
    "year_over_year_growth",
    "positive_negative_filter",
    "requested_rounding",
}
OPERAND_SET_OPERATIONS = {
    "reported_value",
    "multi_company_population",
    "multi_year_range",
    "stage_output_dependency",
}


def classify_composed_blocker(
    route: Mapping[str, Any],
    binding: Mapping[str, Any],
    certificate: Mapping[str, Any],
) -> dict[str, Any] | None:
    if route.get("route_status") != "composed_execution_required":
        return None
    if certificate.get("authorization_status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY":
        return None
    missing = {str(value) for value in route.get("missing_operations") or []}
    answer = certificate.get("answer_certificate") if isinstance(certificate.get("answer_certificate"), Mapping) else {}
    reason_codes = [str(value) for value in answer.get("abstain_reason_codes") or []]
    materialized_operands = sum(
        len(stage.get("required_operands") or [])
        for stage in binding.get("stages") or []
        if isinstance(stage, Mapping)
    )
    compatibility_failure = any("FORMULA_COMPATIBILITY" in code for code in reason_codes)
    if materialized_operands and compatibility_failure and not missing:
        primary = "RELATIONAL_INCOMPATIBILITY"
    elif missing.intersection(FORMULA_DEFINITION_OPERATIONS):
        primary = "FORMULA_DEFINITION_INCOMPLETE"
    else:
        primary = "OPERAND_SET_INCOMPLETE"
    secondary = []
    if missing.intersection(OPERAND_SET_OPERATIONS) and primary != "OPERAND_SET_INCOMPLETE":
        secondary.append("OPERAND_SET_INCOMPLETE")
    if missing.intersection(FORMULA_DEFINITION_OPERATIONS) and primary != "FORMULA_DEFINITION_INCOMPLETE":
        secondary.append("FORMULA_DEFINITION_INCOMPLETE")
    if compatibility_failure and primary != "RELATIONAL_INCOMPATIBILITY":
        secondary.append("RELATIONAL_INCOMPATIBILITY_NOT_YET_ACTIONABLE")
    return {
        "schema_version": 1,
        "protocol": COMPOSED_TAXONOMY_PROTOCOL,
        "question_id": int(route["question_id"]),
        "primary_blocker": primary,
        "secondary_blockers": sorted(set(secondary)),
        "missing_operations": sorted(missing),
        "materialized_operand_count": materialized_operands,
        "compatibility_evaluated": bool(materialized_operands),
        "source_contract": source_contract(),
    }


def classify_route_blocker(route: Mapping[str, Any]) -> dict[str, Any] | None:
    if route.get("route_status") != "route_incomplete":
        return None
    missing = {str(value) for value in route.get("missing_operations") or []}
    reasons = {str(value) for value in route.get("reason_codes") or []}
    if route.get("route_v1_status") == "abstain" and not missing:
        primary = "EVIDENCE_EXISTS_RETRIEVAL_MISSED_OR_UNESTABLISHED"
        basis = "route_v1_abstained_without_operation_gap"
    elif "reported_value" in missing:
        primary = "CORRECT_TABLE_OR_METRIC_BINDING_UNRESOLVED"
        basis = "reported_value_operation_uncovered"
    elif any(code.startswith("MISSING_TABLE_ROLE") for code in reasons):
        primary = "EVIDENCE_RETRIEVED_TABLE_ROLE_UNRESOLVED"
        basis = "explicit_missing_table_role_reason"
    elif "stage_output_dependency" in missing:
        primary = "DECOMPOSITION_OMITTED_REQUIRED_SOURCE"
        basis = "stage_output_dependency_uncovered"
    else:
        primary = "CORRECT_TABLE_OR_METRIC_BINDING_UNRESOLVED"
        basis = "route_semantics_insufficient_for_finer_attribution"
    return {
        "schema_version": 2,
        "protocol": ROUTE_TAXONOMY_PROTOCOL,
        "question_id": int(route["question_id"]),
        "primary_blocker": primary,
        "classification_basis": basis,
        "route_reason_codes": sorted(reasons),
        "missing_operations": sorted(missing),
        "causal_attribution_verified": False,
        "reason_codes": ["CAUSE_REQUIRES_SOURCE_SEARCH_AUDIT"],
        "source_contract": source_contract(),
    }


def _temporal_role(question: str, period_type: str) -> tuple[str, str | None]:
    text = _normalized(question)
    if period_type == "duration":
        return "current_duration", None
    if any(phrase in text for phrase in ("dau nam", "dau ky", "so du dau")):
        return "opening", "01-01"
    if any(phrase in text for phrase in ("cuoi nam", "cuoi ky", "31/12", "tai ngay")):
        return "closing", "12-31"
    return "UNRESOLVED", None


def build_temporal_semantics(
    route: Mapping[str, Any],
    period_packet: Mapping[str, Any],
    certificate: Mapping[str, Any],
) -> dict[str, Any] | None:
    if route.get("route_status") != "route_complete":
        return None
    if certificate.get("authorization_status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY":
        return None
    operands = [
        operand
        for stage in period_packet.get("stages") or []
        if isinstance(stage, Mapping)
        for operand in stage.get("required_operands") or []
        if isinstance(operand, Mapping)
    ]
    period_types = sorted({str(item.get("period_type") or "unknown") for item in operands})
    period_type = period_types[0] if len(period_types) == 1 else "unknown"
    context = route.get("question_context") if isinstance(route.get("question_context"), Mapping) else {}
    years = [int(value) for value in context.get("years") or [] if isinstance(value, int)]
    year = years[0] if len(years) == 1 else None
    role, date_suffix = _temporal_role(str(route.get("question") or ""), period_type)
    if period_type == "duration" and year is not None:
        start, end = f"{year:04d}-01-01", f"{year:04d}-12-31"
    elif period_type == "instant" and year is not None and date_suffix:
        start, end = None, f"{year:04d}-{date_suffix}"
    else:
        start = end = None
    candidates = [
        candidate
        for operand in operands
        for candidate in operand.get("period_column_candidates") or []
        if isinstance(candidate, Mapping)
    ]
    source_expressions = sorted({str(item.get("source_label")) for item in candidates if item.get("source_label")})
    reason_codes = sorted({
        str(code)
        for operand in operands
        for code in operand.get("column_candidate_reason_counts") or {}
    })
    if not candidates:
        reason_codes.append("SOURCE_PERIOD_EXPRESSION_NOT_FOUND")
    return {
        "schema_version": 1,
        "protocol": TEMPORAL_PROTOCOL,
        "question_id": int(route["question_id"]),
        "required_temporal_object": {
            "kind": period_type,
            "start": start,
            "end": end,
            "role": role,
            "requested_years": years,
        },
        "source_expression": source_expressions or None,
        "proof_status": "UNRESOLVED",
        "period_packet_status": period_packet.get("packet_status"),
        "reason_codes": sorted(set(reason_codes)),
        "source_contract": source_contract(),
    }


def validate_v13_partition(
    *,
    question_ids: set[int],
    composed_rows: Sequence[Mapping[str, Any]],
    route_rows: Sequence[Mapping[str, Any]],
    temporal_rows: Sequence[Mapping[str, Any]],
    coverage_rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    partitions = {
        "composed": {int(row["question_id"]) for row in composed_rows},
        "route": {int(row["question_id"]) for row in route_rows},
        "temporal": {int(row["question_id"]) for row in temporal_rows},
        "v12_complete_shadowed": {
            int(row["question_id"])
            for row in coverage_rows
            if row.get("v12_certificate_status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
        },
    }
    combined: set[int] = set()
    for name, values in partitions.items():
        overlap = combined.intersection(values)
        if overlap:
            raise ValueError(f"V13 first-blocker partitions overlap at {name}: {sorted(overlap)[:5]}")
        combined.update(values)
    if combined != question_ids:
        missing = sorted(question_ids - combined)
        extra = sorted(combined - question_ids)
        raise ValueError(f"V13 partition mismatch missing={missing[:5]} extra={extra[:5]}")
    return {name: len(values) for name, values in partitions.items()}


def status_counts(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field) or "UNKNOWN") for row in rows).items()))
