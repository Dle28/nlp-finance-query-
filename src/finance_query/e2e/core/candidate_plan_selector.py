"""Whole-question adapter for bounded candidate-plan selection.

The repository has two deliberately separate pieces of this boundary:
``candidate_plan_contract`` describes a typed, source-bound candidate and
``answer_level_selector`` evaluates producer mappings.  This module is the
small pipeline-facing façade that supplies the question contract to the
selector and returns one stable result shape.

The façade is candidate-only.  A selected plan is a prediction that may be
used by the authorised best-effort submission lane; it is never an evidence
certificate, a strict answer, a training label, or a release decision.
Question IDs are retained for traceability and checked for accidental
cross-question mixing, but no Question-ID allowlist or exception table is
consulted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from .answer_level_selector import (
    AnswerLevelSelector as _RawAnswerLevelSelector,
)


CANDIDATE_PLAN_SELECTOR_PROTOCOL = "vifinqa_candidate_plan_selector_v1"
DEFAULT_MAX_PLANS = 64
MAX_MAX_PLANS = 256

_AUTHORITY_KEYS = (
    "answer_authorized",
    "strict_answer_authorized",
    "evidence_authorized",
    "training_eligible",
    "promotion_allowed",
    "release_authorized",
)
_AUTHORITY_STATUSES = {
    "AUTHORIZED",
    "RELEASE_AUTHORIZED",
    "RELEASED",
    "SUBMISSION_READY",
    "VERIFIED",
}


def _text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _token(value: object) -> str:
    return "_".join(_text(value).upper().split())


def _plan_id(plan: Mapping[str, Any]) -> str:
    return _text(plan.get("plan_id") or plan.get("candidate_id"))


def _canonical(value: object) -> str:
    """Serialize JSON-like metadata for an exact, deterministic comparison."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return repr(value)


def _json_safe(value: object) -> object:
    """Convert selector diagnostics to JSON without changing plan semantics."""

    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        text = format(value.normalize(), "f")
        return "0" if text in {"-0", "-0.0"} else text
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested) for nested in value]
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    return value


def _values(value: object, *, period: bool = False) -> set[object]:
    raw_values = (
        value
        if isinstance(value, (list, tuple, set, frozenset))
        else [value]
    )
    result: set[object] = set()
    for raw in raw_values:
        if raw is None or isinstance(raw, bool):
            continue
        if period:
            if isinstance(raw, int):
                result.add(raw)
                continue
            text = _text(raw)
            try:
                result.add(int(text))
                continue
            except ValueError:
                pass
            result.add(text.casefold())
        else:
            text = _text(raw)
            if text:
                result.add(" ".join(text.split()).casefold())
    return result


def _operand_field_values(
    operands: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    *,
    period: bool = False,
) -> set[object]:
    values: set[object] = set()
    for operand in operands:
        for field in fields:
            if field in operand and operand[field] is not None:
                values.update(_values(operand[field], period=period))
                break
    return values


def _operands(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = plan.get("operands")
    if not isinstance(raw, (list, tuple)):
        return []
    return [value for value in raw if isinstance(value, Mapping)]


def _operand_ids(plan: Mapping[str, Any]) -> list[str]:
    return [_text(value.get("operand_id") or value.get("id")) for value in _operands(plan)]


def _contract_reasons(
    *,
    question_id: int,
    contract: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[str]:
    """Check the whole-question dimensions before invoking route ranking."""

    reasons: list[str] = []
    if "question_id" in plan and plan["question_id"] is not None:
        if plan["question_id"] != question_id:
            reasons.append("QUESTION_ID_MISMATCH")

    verification_status = _token(
        plan.get("verification_status")
        or plan.get("verification_class")
        or (plan.get("verification") or {}).get("status")
        if isinstance(plan.get("verification"), Mapping)
        else plan.get("verification_status") or plan.get("verification_class")
    )
    if verification_status in _AUTHORITY_STATUSES:
        reasons.append("AUTHORITY_STATUS_FORBIDDEN")

    expected_ast = contract.get("operation_ast")
    actual_ast = plan.get("operation_ast")
    if isinstance(expected_ast, Mapping):
        if not isinstance(actual_ast, Mapping):
            reasons.append("OPERATION_AST_MISSING")
        elif _canonical(actual_ast) != _canonical(expected_ast):
            # AST equality is intentional here.  For temporal and composed
            # questions, an order change is a different answer, not a route
            # ranking detail.
            reasons.append("OPERATION_AST_MISMATCH")
    else:
        required_operations = _values(contract.get("required_operations"))
        actual_operation = _values(
            (actual_ast or {}).get("op") if isinstance(actual_ast, Mapping) else None
        )
        if required_operations and not actual_operation.intersection(required_operations):
            reasons.append("OPERATION_MISMATCH")

    required_ids = _values(contract.get("required_operand_ids"))
    actual_ids = set(_operand_ids(plan))
    if required_ids and actual_ids != required_ids:
        reasons.append("OPERAND_SET_MISMATCH")
    required_count = contract.get("required_operand_count")
    if isinstance(required_count, int) and not isinstance(required_count, bool):
        if len(actual_ids) != required_count:
            reasons.append("OPERAND_COUNT_MISMATCH")

    expected_periods = _values(
        contract.get("required_periods", contract.get("required_years")),
        period=True,
    )
    actual_periods = _operand_field_values(
        _operands(plan),
        ("period", "year", "period_year", "fiscal_year", "report_year", "years"),
        period=True,
    )
    if expected_periods and actual_periods != expected_periods:
        reasons.append("PERIOD_MISMATCH")

    expected_entities = _values(
        contract.get("required_entities", contract.get("required_tickers"))
    )
    actual_entities = _operand_field_values(
        _operands(plan),
        ("entity", "ticker", "company", "entity_id", "issuer"),
    )
    if expected_entities and actual_entities != expected_entities:
        reasons.append("ENTITY_MISMATCH")

    expected_scope = _values(
        contract.get("required_scope", contract.get("scope"))
    )
    actual_scopes = _operand_field_values(
        _operands(plan),
        ("scope", "scope_type", "statement_scope"),
    )
    if expected_scope and actual_scopes != expected_scope:
        reasons.append("SCOPE_MISMATCH")

    expected_unit = _values(
        contract.get("required_unit", contract.get("source_unit"))
    )
    actual_units = _operand_field_values(
        _operands(plan),
        ("unit", "source_unit", "target_unit", "requested_unit", "output_unit"),
    )
    if expected_unit and actual_units != expected_unit:
        reasons.append("UNIT_MISMATCH")

    return list(dict.fromkeys(reasons))


def _mark_contract_rejected(
    plan: Mapping[str, Any],
    reasons: Sequence[str],
) -> dict[str, Any]:
    prepared = deepcopy(dict(plan))
    prepared["filter_passed"] = False
    prepared["survived_filter"] = False
    prepared["candidate_status"] = "REJECTED"
    prepared["_contract_rejection_reason_codes"] = list(dict.fromkeys(reasons))
    return prepared


def _safe_plan_copy(plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return deepcopy(dict(plan))
    except (TypeError, ValueError):
        return dict(plan)


@dataclass(frozen=True, slots=True)
class CandidatePlanSet:
    """Bounded whole-question candidates plus an optional control plan."""

    question_id: int
    question_contract: Mapping[str, Any]
    plans: Sequence[Mapping[str, Any]] = ()
    baseline_plan: Mapping[str, Any] | None = None
    max_plans: int = DEFAULT_MAX_PLANS

    def __post_init__(self) -> None:
        if isinstance(self.question_id, bool) or not isinstance(self.question_id, int):
            raise TypeError("question_id must be an integer")
        if not isinstance(self.question_contract, Mapping):
            raise TypeError("question_contract must be a mapping")
        if isinstance(self.plans, (str, bytes, bytearray)):
            raise TypeError("plans must be a sequence of mappings")
        if isinstance(self.max_plans, bool) or not isinstance(self.max_plans, int):
            raise TypeError("max_plans must be an integer")
        if not 1 <= self.max_plans <= MAX_MAX_PLANS:
            raise ValueError(f"max_plans must be between 1 and {MAX_MAX_PLANS}")

        try:
            raw_plans = tuple(self.plans)
        except TypeError:
            raise TypeError("plans must be a sequence of mappings") from None
        normalized: list[Mapping[str, Any]] = []
        for plan in raw_plans:
            if not isinstance(plan, Mapping):
                raise TypeError("plans must contain mappings")
            normalized.append(_safe_plan_copy(plan))
        baseline = self.baseline_plan
        if baseline is not None:
            if not isinstance(baseline, Mapping):
                raise TypeError("baseline_plan must be a mapping or None")
            baseline_copy = _safe_plan_copy(baseline)
            baseline_copy["is_baseline"] = True
            baseline = baseline_copy

        # Keep the constructor side-effect free.  Selection applies the
        # question contract and the bounded raw-selector ordering; preserving
        # input order here makes diagnostics traceable to the producer.
        object.__setattr__(self, "question_contract", _safe_plan_copy(self.question_contract))
        object.__setattr__(self, "plans", tuple(normalized))
        object.__setattr__(self, "baseline_plan", baseline)

    @property
    def all_plans(self) -> tuple[Mapping[str, Any], ...]:
        plans = list(self.plans)
        if self.baseline_plan is not None:
            plans.append(self.baseline_plan)
        return tuple(plans)

    def __len__(self) -> int:
        return len(self.plans) + (1 if self.baseline_plan is not None else 0)

    def __iter__(self):
        return iter(self.all_plans)


def _answer_text(plan: Mapping[str, Any] | None) -> str | None:
    if not isinstance(plan, Mapping):
        return None
    for key in (
        "replay_answer_decimal",
        "answer_decimal",
        "replay_answer",
        "answer",
        "value",
    ):
        value = plan.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, Decimal):
            if not value.is_finite():
                return None
            return format(value.normalize(), "f")
        try:
            decimal = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            return str(value).strip() or None
        if not decimal.is_finite():
            return None
        text = format(decimal.normalize(), "f")
        return "0" if text in {"-0", "-0.0"} else text
    return None


def _authority() -> dict[str, bool]:
    return {key: False for key in _AUTHORITY_KEYS}


class AnswerLevelSelector:
    """Select a complete plan for one question without granting authority."""

    def __init__(self, *, max_candidates: int = DEFAULT_MAX_PLANS) -> None:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise TypeError("max_candidates must be an integer")
        if not 1 <= max_candidates <= MAX_MAX_PLANS:
            raise ValueError(
                f"max_candidates must be between 1 and {MAX_MAX_PLANS}"
            )
        self.max_candidates = max_candidates

    def select(self, plan_set: CandidatePlanSet) -> dict[str, Any]:
        if not isinstance(plan_set, CandidatePlanSet):
            raise TypeError("select expects a CandidatePlanSet")

        raw_plans: list[Mapping[str, Any]] = []
        contract_reasons: dict[str, list[str]] = {}
        for plan in plan_set.all_plans:
            reasons = _contract_reasons(
                question_id=plan_set.question_id,
                contract=plan_set.question_contract,
                plan=plan,
            )
            candidate_id = _plan_id(plan)
            if reasons:
                prepared = _mark_contract_rejected(plan, reasons)
                if candidate_id:
                    contract_reasons[candidate_id] = reasons
            else:
                prepared = _safe_plan_copy(plan)
            # The raw selector checks plan-local required IDs/fields.  These
            # are copied from the whole-question contract so a plan cannot
            # pass merely because it omitted a question-level dimension.
            for key in (
                "required_operand_ids",
                "required_operand_count",
                "required_periods",
                "required_years",
                "required_entities",
                "required_tickers",
                "required_scope",
                "required_unit",
            ):
                if key in plan_set.question_contract:
                    prepared[key] = deepcopy(plan_set.question_contract[key])
            raw_plans.append(prepared)

        raw_result = _RawAnswerLevelSelector(
            max_candidates=self.max_candidates,
        ).select(raw_plans)
        selected = raw_result.get("selected_plan")
        selected_id = _text(raw_result.get("selected_candidate_id")) or None

        rejected_ids: list[str] = []
        raw_rejected = raw_result.get("rejected_plans")
        if isinstance(raw_rejected, Sequence) and not isinstance(raw_rejected, (str, bytes)):
            for rejected in raw_rejected:
                if isinstance(rejected, Mapping):
                    candidate_id = _text(rejected.get("candidate_id"))
                    if candidate_id and candidate_id not in rejected_ids:
                        rejected_ids.append(candidate_id)
        for candidate_id in contract_reasons:
            if candidate_id and candidate_id not in rejected_ids:
                rejected_ids.append(candidate_id)

        reason_codes: list[str] = []
        raw_reasons = raw_result.get("selection_reason_codes")
        if isinstance(raw_reasons, Sequence) and not isinstance(raw_reasons, (str, bytes)):
            reason_codes.extend(_text(code) for code in raw_reasons if _text(code))
        for reasons in contract_reasons.values():
            reason_codes.extend(reasons)
        reason_codes = list(dict.fromkeys(reason_codes))

        selected_is_baseline = bool(
            selected_id
            and plan_set.baseline_plan is not None
            and selected_id == _plan_id(plan_set.baseline_plan)
        )
        if selected_is_baseline:
            decision = "BASELINE_FALLBACK"
            if "BASELINE_FALLBACK" not in reason_codes:
                reason_codes.insert(0, "BASELINE_FALLBACK")
        elif selected_id:
            decision = "SELECTED"
        else:
            decision = _text(raw_result.get("status")) or "ABSTAIN"

        result = {
            "protocol": CANDIDATE_PLAN_SELECTOR_PROTOCOL,
            "question_id": plan_set.question_id,
            "selected_plan_id": selected_id,
            "selected_answer_decimal": _answer_text(selected),
            "decision": decision,
            "reason_codes": reason_codes,
            "rejected_plan_ids": rejected_ids,
            "authority": _authority(),
            "selected_plan": _json_safe(selected) if isinstance(selected, Mapping) else None,
            "candidate_only": bool(selected_id),
            "answer_channel": (
                "BEST_EFFORT_CANDIDATE" if selected_id else "ABSTAIN"
            ),
            "raw_selector": {
                "status": raw_result.get("status"),
                "candidate_count": raw_result.get("candidate_count", len(raw_plans)),
                "eligible_candidate_count": raw_result.get("eligible_candidate_count", 0),
                "truncated_candidate_count": raw_result.get("truncated_candidate_count", 0),
            },
        }
        result.update(_authority())
        return result


__all__ = [
    "CANDIDATE_PLAN_SELECTOR_PROTOCOL",
    "DEFAULT_MAX_PLANS",
    "MAX_MAX_PLANS",
    "CandidatePlanSet",
    "AnswerLevelSelector",
]
