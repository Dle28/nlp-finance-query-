"""Typed financial binding and answer-or-best-candidate contracts.

This module converts an already-selected finite operand plan, exact source
bindings, an executor trace, and counterfactual rejection receipts into an
answer output or an explicit authorization abstention.  When authorization is
incomplete but a separately selected candidate has survived its filters, the
candidate is still exposed as a best-effort prediction.  It never promotes
labels or a model checkpoint.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping

from .evidence_binding import (
    BINDING_FIELDS,
    NOT_APPLICABLE,
    PASS,
    validate_evidence_binding,
    validate_formula_compatibility,
)


ANSWER_CERTIFICATE_PROTOCOL = "vifinqa_answer_certificate_v1"
ANSWER_CERTIFICATE_SCHEMA_VERSION = 1

_REQUIRED_TEMPORAL_FIELDS = ("period_years", "period_grain", "flow_or_stock", "scope", "source_unit")


class AnswerCertificateError(ValueError):
    """A plan, binding, execution trace, or rejection receipt is malformed."""


def _sha(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_REJECTED_CANDIDATE_STATUSES = {
    "ABSTAIN",
    "FILTER_REJECTED",
    "NO_CANDIDATE",
    "QUARANTINED",
    "REJECTED",
}


def _best_candidate_prediction(
    candidate: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalize a selector-approved candidate without making it authoritative.

    The selector must do the semantic ranking/filtering upstream.  This helper
    only enforces the final safety boundary: a candidate must contain a finite
    numeric answer and must not explicitly report that it failed its filter.
    """
    if candidate is None:
        return None
    raw_answer = candidate.get(
        "answer",
        candidate.get("answer_decimal", candidate.get("value")),
    )
    if raw_answer is None:
        return None
    try:
        answer = Decimal(str(raw_answer))
    except (InvalidOperation, ValueError):
        return None
    if not answer.is_finite():
        return None
    if candidate.get("filter_passed") is False or candidate.get("survived_filter") is False:
        return None
    candidate_status = str(
        candidate.get("filter_status")
        or candidate.get("candidate_status")
        or "SURVIVED_FILTER"
    ).strip().upper()
    if candidate_status in _REJECTED_CANDIDATE_STATUSES:
        return None
    candidate_id = str(
        candidate.get("candidate_id")
        or candidate.get("binding_id")
        or candidate.get("stage_id")
        or ""
    ).strip()
    if not candidate_id:
        candidate_id = _sha(
            {
                key: value
                for key, value in candidate.items()
                if key not in {"answer", "answer_decimal", "value"}
            }
        )
    normalized: dict[str, Any] = {
        "candidate_id": candidate_id,
        "answer_decimal": format(answer, "f"),
        "filter_status": candidate_status,
        "selection_method": str(
            candidate.get("selection_method") or "best_surviving_candidate"
        ),
    }
    for source_key in ("filter_score", "score", "rank", "candidate_rank"):
        if candidate.get(source_key) is not None:
            normalized["filter_score" if source_key in {"filter_score", "score"} else source_key] = candidate.get(source_key)
            if source_key in {"filter_score", "score"}:
                break
    source = candidate.get("source", candidate.get("sources"))
    if source is not None:
        normalized["source"] = source
    for key in (
        "internal_table_uid",
        "document_id",
        "row_index",
        "column_index",
        "tier",
    ):
        if candidate.get(key) is not None:
            normalized[key] = candidate.get(key)
    return normalized


def temporal_contract_for_operand(
    *,
    years: object,
    scope: object,
    requested_unit: object,
    entity: object = None,
    entity_role: object = None,
) -> dict[str, Any]:
    """Declare what a later exact binding must prove, without inferring it.

    A question's year list does not establish whether the value is a stock,
    flow, fiscal-year total, YTD figure, restatement, or comparative column.
    Those fields are deliberately unresolved here and mandatory at binding.
    """
    normalized_years: list[int] = []
    for value in years if isinstance(years, list) else []:
        try:
            normalized_years.append(int(value))
        except (TypeError, ValueError):
            continue
    return {
        "question_years": sorted(set(normalized_years)),
        "requested_entity": str(entity).strip() or None if entity is not None else None,
        "requested_entity_role": str(entity_role).strip() or None if entity_role is not None else None,
        "requested_scope": str(scope).strip() or None if scope is not None else None,
        "requested_output_unit": str(requested_unit).strip() or None if requested_unit is not None else None,
        "period_grain": "UNRESOLVED_MUST_BIND",
        "flow_or_stock": "UNRESOLVED_MUST_BIND",
        "as_of_date": None,
        "comparative_basis": "UNRESOLVED_MUST_BIND",
        "restatement_status": "UNRESOLVED_MUST_BIND",
        "fiscal_calendar": "UNRESOLVED_MUST_BIND",
        "required_binding_fields": list(_REQUIRED_TEMPORAL_FIELDS),
    }


def _as_mapping_by_id(value: object, *, id_field: str, label: str) -> dict[str, Mapping[str, Any]]:
    rows = value.values() if isinstance(value, Mapping) else value
    if not isinstance(rows, (list, tuple)) and not isinstance(value, Mapping):
        raise AnswerCertificateError(f"{label} must be a mapping or list")
    result: dict[str, Mapping[str, Any]] = {}
    iterable = rows if not isinstance(rows, Mapping) else rows.values()
    for item in iterable:
        if not isinstance(item, Mapping):
            raise AnswerCertificateError(f"{label} entry is not an object")
        identifier = str(item.get(id_field) or "")
        if not identifier or identifier in result:
            raise AnswerCertificateError(f"{label} has missing or duplicate {id_field}")
        result[identifier] = item
    return result


def _binding_errors(operand: Mapping[str, Any], binding: Mapping[str, Any]) -> list[str]:
    errors = list(validate_evidence_binding(binding))
    if str(binding.get("binding_status") or "") != "BOUND" or binding.get("operand_eligible") is not True:
        errors.append("BINDING_NOT_FULLY_ELIGIBLE")
    field_statuses = binding.get("field_statuses") or {}
    requirements = binding.get("requirements") or {}
    required_fields = set(requirements.get("required_fields") or BINDING_FIELDS)
    for field in BINDING_FIELDS:
        status_key = f"{field}_status"
        status = str(field_statuses.get(status_key) or "")
        if status == PASS:
            continue
        if field not in required_fields and status == NOT_APPLICABLE:
            continue
        errors.append(f"BINDING_FIELD_NOT_PASS:{status_key}")
    temporal = operand.get("temporal_contract") or {}
    expected_variable_id = str(operand.get("expected_variable_id") or "")
    bound_variable_id = str((binding.get("variable_binding") or {}).get("variable_id") or "")
    if expected_variable_id and bound_variable_id != expected_variable_id:
        errors.append("VARIABLE_MISMATCH")
    period = dict(binding.get("period_binding") or {})
    unit = dict(binding.get("unit_binding") or {})
    entity_scope = dict(binding.get("entity_scope_binding") or {})
    entity_role = dict(binding.get("entity_role_binding") or {})
    requested_years = {int(value) for value in temporal.get("question_years") or []}
    bound_years: set[int] = set()
    for value in period.get("period_years") or []:
        try:
            bound_years.add(int(value))
        except (TypeError, ValueError):
            errors.append("BOUND_PERIOD_YEAR_INVALID")
    if requested_years and not requested_years.intersection(bound_years):
        errors.append("PERIOD_YEAR_MISMATCH")
    requested_entity = temporal.get("requested_entity")
    if requested_entity and str(entity_scope.get("entity") or "") != str(requested_entity):
        errors.append("ENTITY_MISMATCH")
    requested_entity_role = temporal.get("requested_entity_role")
    if requested_entity_role and str(entity_role.get("role") or "") != str(requested_entity_role):
        errors.append("ENTITY_ROLE_MISMATCH")
    requested_scope = temporal.get("requested_scope")
    if requested_scope and str(entity_scope.get("scope") or "") != str(requested_scope):
        errors.append("SCOPE_MISMATCH")
    requested_output_unit = temporal.get("requested_output_unit")
    source_unit = str(unit.get("normalized_currency") or unit.get("semantic_unit") or "")
    if (
        requested_output_unit
        and source_unit != str(requested_output_unit)
        and str(unit.get("conversion_status") or "") != "PASS"
    ):
        errors.append("UNIT_CONVERSION_UNVERIFIED")
    return sorted(set(errors))


def _execution_errors(plan: Mapping[str, Any], execution: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(execution.get("status") or "") not in {"PASS", "execution_replay_ready"}:
        errors.append("EXECUTION_NOT_PASSED")
    try:
        Decimal(str(execution.get("answer_decimal")))
    except (InvalidOperation, ValueError):
        errors.append("ANSWER_DECIMAL_INVALID")
    expected_ast_sha = _sha(plan.get("operation_ast") or {})
    if str(execution.get("operation_ast_sha256") or "") != expected_ast_sha:
        errors.append("EXECUTION_PLAN_HASH_MISMATCH")
    return errors


def _counterfactual_errors(
    *,
    alternative_checks: object,
    required_dimensions: set[str],
) -> list[str]:
    if not isinstance(alternative_checks, list):
        return ["COUNTERFACTUAL_CHECKS_MISSING"]
    rejected: set[str] = set()
    errors: list[str] = []
    for item in alternative_checks:
        if not isinstance(item, Mapping):
            errors.append("COUNTERFACTUAL_CHECK_NOT_OBJECT")
            continue
        dimension = str(item.get("dimension") or "")
        if dimension not in required_dimensions:
            errors.append("COUNTERFACTUAL_DIMENSION_INVALID")
            continue
        status = str(item.get("status") or "")
        if status == "EXHAUSTED":
            if item.get("enumeration_complete") is not True:
                errors.append(f"COUNTERFACTUAL_ENUMERATION_INCOMPLETE:{dimension}")
                continue
            try:
                candidate_count = int(item.get("candidate_count"))
            except (TypeError, ValueError):
                candidate_count = 0
            candidate_set_sha256 = str(item.get("candidate_set_sha256") or "")
            if candidate_count < 1 or len(candidate_set_sha256) != 64:
                errors.append(f"COUNTERFACTUAL_ENUMERATION_INVALID:{dimension}")
                continue
            if not str(item.get("rejection_code") or ""):
                errors.append(f"COUNTERFACTUAL_NO_REASON:{dimension}")
                continue
            rejected.add(dimension)
            continue
        if status != "REJECTED":
            errors.append(f"COUNTERFACTUAL_NOT_REJECTED:{dimension}")
            continue
        if not str(item.get("rejection_code") or ""):
            errors.append(f"COUNTERFACTUAL_NO_REASON:{dimension}")
            continue
        if not str(item.get("alternative_binding_id") or ""):
            errors.append(f"COUNTERFACTUAL_NO_ALTERNATIVE:{dimension}")
            continue
        rejected.add(dimension)
    errors.extend(f"COUNTERFACTUAL_UNCHECKED:{dimension}" for dimension in sorted(required_dimensions - rejected))
    return sorted(set(errors))


def compile_answer_certificate(
    *,
    question_id: object,
    binding_plan: Mapping[str, Any],
    operand_bindings: object,
    execution: Mapping[str, Any],
    alternative_checks: object,
    best_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile an answer certificate or an authorization abstention.

    This only verifies a *bounded candidate universe*.  It therefore records
    counterfactual rejection receipt IDs but does not claim that no alternative
    could exist elsewhere in the corpus.  If the certificate is incomplete,
    ``best_candidate`` can keep a selector-approved numeric prediction in the
    answer channel while the top-level ``status`` remains ``ABSTAIN``.
    """
    if question_id is None or str(question_id) == "":
        raise AnswerCertificateError("question_id is required")
    operands = _as_mapping_by_id(binding_plan.get("operands"), id_field="operand_id", label="plan operands")
    if not operands:
        raise AnswerCertificateError("binding plan must contain operands")
    bindings = _as_mapping_by_id(operand_bindings, id_field="operand_id", label="operand bindings")
    errors: list[str] = []
    binding_receipts: list[dict[str, Any]] = []
    required_dimensions = {"period", "scope", "unit"}
    if any(
        bool((operand.get("temporal_contract") or {}).get("requested_entity_role"))
        for operand in operands.values()
        if bool(operand.get("required", True))
    ):
        required_dimensions.add("entity_role")
    for operand_id, operand in operands.items():
        if not bool(operand.get("required", True)):
            continue
        binding = bindings.get(operand_id)
        if binding is None:
            errors.append(f"MISSING_OPERAND_BINDING:{operand_id}")
            continue
        binding_errors = _binding_errors(operand, binding)
        errors.extend(f"{operand_id}:{error}" for error in binding_errors)
        binding_receipts.append(
            {
                "operand_id": operand_id,
                "binding_id": str(binding.get("binding_id") or ""),
                "document_uid": binding.get("document_uid"),
                "internal_table_uid": binding.get("internal_table_uid"),
                "source_value_cell": (binding.get("source_integrity") or {}).get("value_cell"),
                "field_statuses": binding.get("field_statuses"),
                "period": binding.get("period_binding"),
                "unit": binding.get("unit_binding"),
                "entity_scope": binding.get("entity_scope_binding"),
                "entity_role": binding.get("entity_role_binding"),
                "revision": binding.get("revision_binding"),
            }
        )
    extra_bindings = sorted(set(bindings) - set(operands))
    errors.extend(f"UNPLANNED_OPERAND_BINDING:{operand_id}" for operand_id in extra_bindings)
    required_operand_bindings = {
        operand_id: bindings[operand_id]
        for operand_id, operand in operands.items()
        if bool(operand.get("required", True)) and operand_id in bindings
    }
    errors.extend(
        validate_formula_compatibility(
            required_operand_bindings,
            binding_plan.get("formula_compatibility"),
        )
    )
    errors.extend(_execution_errors(binding_plan, execution))
    errors.extend(_counterfactual_errors(
        alternative_checks=alternative_checks,
        required_dimensions=required_dimensions,
    ))
    candidate_prediction = _best_candidate_prediction(best_candidate)
    status = "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY" if not errors else "ABSTAIN"
    answer_available = not errors or candidate_prediction is not None
    answer_status = "ANSWER" if not errors else (
        "PREDICTED_CANDIDATE" if candidate_prediction is not None else "ABSTAIN"
    )
    payload = {
        "schema_version": ANSWER_CERTIFICATE_SCHEMA_VERSION,
        "protocol": ANSWER_CERTIFICATE_PROTOCOL,
        "question_id": question_id,
        "binding_plan_sha256": _sha(dict(binding_plan)),
        "operation_ast_sha256": _sha(binding_plan.get("operation_ast") or {}),
        "answer": (
            str(execution.get("answer_decimal"))
            if not errors
            else candidate_prediction["answer_decimal"]
            if candidate_prediction is not None
            else None
        ),
        "answer_status": answer_status,
        "prediction_status": "AUTHORIZED_ANSWER" if not errors else (
            "UNCERTAIN_CANDIDATE" if candidate_prediction is not None else "NO_PREDICTION"
        ),
        "answer_available": answer_available,
        "answer_authorized": not errors,
        "candidate_prediction": candidate_prediction,
        "binding_receipts": binding_receipts,
        "execution_receipt": {
            "status": execution.get("status"),
            "answer_decimal": str(execution.get("answer_decimal")) if not errors else None,
            "candidate_answer_decimal": (
                candidate_prediction["answer_decimal"]
                if errors and candidate_prediction is not None
                else None
            ),
            "operation_ast_sha256": execution.get("operation_ast_sha256"),
        },
        "counterfactual_checks": alternative_checks if isinstance(alternative_checks, list) else [],
        "candidate_universe": "bounded_by_hash_bound_candidate_set",
        "global_uniqueness_proven": False,
        "status": status,
        "abstain_reason_codes": sorted(set(errors)),
        "training_eligible": False,
        "promotion_allowed": False,
        "serving_eligible": answer_available,
        "next_gate": "campaign_review_and_release_policy" if not errors else "repair_or_expand_candidate_set",
    }
    return {"answer_certificate_id": _sha(payload), **payload}


def compile_abstention_certificate(
    *,
    question_id: object,
    reason_codes: object,
    binding_plan: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    best_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit a certificate when authorization is incomplete.

    This is deliberately separate from :func:`compile_answer_certificate`:
    an empty candidate set is still a reason to abstain, but a selector-approved
    best candidate is retained as a best-effort prediction.  Thus ``answer`` is
    ``None`` only when no finite candidate survives the filter.
    """
    if question_id is None or str(question_id) == "":
        raise AnswerCertificateError("question_id is required")
    codes = sorted(
        {
            str(value).strip()
            for value in reason_codes if isinstance(reason_codes, (list, tuple, set))
            if str(value).strip()
        }
    )
    if not codes:
        raise AnswerCertificateError("reason_codes are required for an abstention")
    plan = dict(binding_plan or {})
    execution_receipt = dict(execution or {})
    candidate_prediction = _best_candidate_prediction(best_candidate)
    answer_available = candidate_prediction is not None
    payload = {
        "schema_version": ANSWER_CERTIFICATE_SCHEMA_VERSION,
        "protocol": ANSWER_CERTIFICATE_PROTOCOL,
        "question_id": question_id,
        "binding_plan_sha256": _sha(plan),
        "operation_ast_sha256": _sha(plan.get("operation_ast") or {}),
        "answer": candidate_prediction["answer_decimal"] if candidate_prediction else None,
        "answer_status": "PREDICTED_CANDIDATE" if answer_available else "ABSTAIN",
        "prediction_status": "UNCERTAIN_CANDIDATE" if answer_available else "NO_PREDICTION",
        "answer_available": answer_available,
        "answer_authorized": False,
        "candidate_prediction": candidate_prediction,
        "binding_receipts": [],
        "execution_receipt": {
            "status": execution_receipt.get("status"),
            "answer_decimal": None,
            "candidate_answer_decimal": (
                candidate_prediction["answer_decimal"] if candidate_prediction else None
            ),
            "operation_ast_sha256": execution_receipt.get("operation_ast_sha256"),
        },
        "counterfactual_checks": [],
        "candidate_universe": "bounded_by_hash_bound_candidate_set",
        "global_uniqueness_proven": False,
        "status": "ABSTAIN",
        "abstain_reason_codes": codes,
        "training_eligible": False,
        "promotion_allowed": False,
        "serving_eligible": answer_available,
        "next_gate": "repair_or_expand_candidate_set",
    }
    return {"answer_certificate_id": _sha(payload), **payload}
