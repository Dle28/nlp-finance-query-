"""Fail-closed answer-level selection for independent candidate plans.

The selector is deliberately a small boundary between plan producers and a
submission compiler.  It accepts plain mappings so producers do not need to
share a class or import this module.  A plan is eligible only when it carries
enough structure to explain a whole answer: an operand graph, a complete
operation AST, semantic fields, source coordinates, and an independently
matching replay result.

The baseline is special only in one narrow way.  A producer may mark an
existing baseline plan with ``is_baseline``.  If no newer complete plan is
better, that baseline can remain in the best-effort candidate lane even when
its legacy metadata is incomplete.  Explicit period/entity/scope/unit
mismatches, replay mismatches, filter failures, and rejected verification
statuses still reject the baseline.  This preserves an existing prediction
without allowing a new route to replace it with an invalid plan.

This module never authorizes an answer, changes a plan in place, or promotes
``PARTIAL``/``UNRESOLVED`` to ``VERIFIED``.  The returned decision always
marks a selected value as ``BEST_EFFORT_CANDIDATE`` with no authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any


ANSWER_LEVEL_SELECTOR_PROTOCOL = "vifinqa_answer_level_selector_v1"
DEFAULT_MAX_CANDIDATES = 64
MAX_MAX_CANDIDATES = 256
SEMANTIC_COMPLETENESS_THRESHOLD = 0.8

_PASS_STATUSES = {
    "PASS",
    "PASSED",
    "OK",
    "MATCH",
    "MATCHED",
    "COMPLETE",
    "COMPLETED",
    "VERIFIED",
    "EXECUTION_REPLAY_READY",
    "REPLAY_PASS",
}
_FAIL_STATUSES = {
    "FAIL",
    "FAILED",
    "MISMATCH",
    "REJECTED",
    "ABSTAIN",
    "UNRESOLVED",
    "PARTIAL",
    "MISSING",
    "INVALID",
    "FILTER_REJECTED",
    "QUARANTINED",
}
_REJECTED_STATUSES = {
    "REJECTED",
    "FILTER_REJECTED",
    "QUARANTINED",
    "ABSTAIN",
}
_PLACEHOLDER_TOKENS = {
    "",
    "?",
    "UNKNOWN",
    "UNRESOLVED",
    "MISSING",
    "NONE",
    "NULL",
    "TBD",
    "PLAN_REQUIRED",
}
_INCOMPLETE_AST_OPS = {
    "",
    "ABSTAIN",
    "UNKNOWN",
    "PLAN_REQUIRED",
    "SEMANTIC_CELL_HEURISTIC",
    "HEURISTIC",
}
_PERIOD_KEYS = (
    "required_period",
    "required_year",
    "required_periods",
    "required_years",
    "expected_period",
    "expected_year",
    "question_period",
    "question_year",
    "requested_period",
    "target_period",
    "period",
    "year",
    "question_years",
    "periods",
    "years",
)
_ENTITY_KEYS = (
    "required_entity",
    "required_entities",
    "expected_entity",
    "requested_entity",
    "question_entity",
    "entity",
    "ticker",
    "entities",
    "tickers",
)
_SCOPE_KEYS = (
    "required_scope",
    "expected_scope",
    "requested_scope",
    "question_scope",
    "scope",
    "scopes",
)
_UNIT_KEYS = (
    "required_unit",
    "required_output_unit",
    "expected_unit",
    "requested_unit",
    "question_unit",
    "unit",
    "output_unit",
    "target_unit",
    "source_unit",
)
_OPERAND_FIELD_KEYS = {
    "period": ("period", "year", "period_year", "fiscal_year", "report_year"),
    "entity": ("entity", "ticker", "company", "entity_id", "issuer"),
    "scope": ("scope", "scope_type", "statement_scope"),
    "unit": ("unit", "source_unit", "target_unit", "requested_unit", "output_unit"),
}
_SOURCE_UID_KEYS = (
    "internal_table_uid",
    "source_uid",
    "table_uid",
    "source_table_uid",
)
_ROW_KEYS = ("row_index", "row", "source_row_index")
_COLUMN_KEYS = ("column_index", "column", "source_column_index")
_HASH_KEYS = (
    "source_hash",
    "source_sha256",
    "table_sha256",
    "source_coordinate_hash",
    "hash",
)
_ANSWER_KEYS = (
    "answer_decimal",
    "replay_answer_decimal",
    "answer",
    "replayed_answer",
    "replay_answer",
    "execution_answer",
    "value",
)
_AST_REFERENCE_KEYS = {
    "ARGS",
    "DENOMINATOR",
    "INPUT",
    "INPUTS",
    "LEFT",
    "NUMERATOR",
    "OPERAND",
    "OPERAND_ID",
    "OPERANDS",
    "OVER_OPERANDS",
    "RIGHT",
    "RETURN_OPERAND",
    "SELECT_OPERAND",
    "TARGET_OPERAND",
}


def _text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _token(value: object) -> str:
    return re.sub(r"\s+", "_", _text(value).upper())


def _finite_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _first_value(mapping: Mapping[str, Any], keys: Sequence[str]) -> object | None:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _status(value: object) -> str:
    if isinstance(value, Mapping):
        value = _first_value(
            value,
            (
                "status",
                "verification_status",
                "verification_class",
                "replay_status",
                "result",
            ),
        )
    return _token(value)


def _status_pass(value: object) -> bool:
    return _status(value) in _PASS_STATUSES


def _status_fail(value: object) -> bool:
    return _status(value) in _FAIL_STATUSES


def _normalise_period_values(value: object) -> tuple[int | str, ...]:
    values: list[int | str] = []
    raw_values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    for raw in raw_values:
        if raw is None or isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            values.append(raw)
            continue
        text = _text(raw)
        if not text:
            continue
        try:
            values.append(int(text))
            continue
        except ValueError:
            pass
        matches = re.findall(r"\b(?:19|20)\d{2}\b", text)
        if len(matches) == 1:
            values.append(int(matches[0]))
        else:
            values.append(text.casefold())
    return tuple(dict.fromkeys(values))


def _normalise_values(value: object, *, kind: str) -> tuple[str | int, ...]:
    if kind == "period":
        return _normalise_period_values(value)
    raw_values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    values: list[str] = []
    for raw in raw_values:
        text = _text(raw)
        if not text:
            continue
        normalised = re.sub(r"\s+", " ", text).strip()
        if kind in {"entity", "scope", "unit"}:
            normalised = normalised.casefold()
        values.append(normalised)
    return tuple(dict.fromkeys(values))


def _mapping_candidates(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    nested: list[Mapping[str, Any]] = []
    for key in ("question_contract", "contract", "question_plan", "expected"):
        value = plan.get(key)
        if isinstance(value, Mapping):
            nested.append(value)
    return [plan, *nested]


def _constraint_values(plan: Mapping[str, Any], kind: str) -> tuple[str | int, ...]:
    keys = {
        "period": _PERIOD_KEYS,
        "entity": _ENTITY_KEYS,
        "scope": _SCOPE_KEYS,
        "unit": _UNIT_KEYS,
    }[kind]
    values: list[str | int] = []
    for source in _mapping_candidates(plan):
        for key in keys:
            if key not in source or source[key] is None:
                continue
            values.extend(_normalise_values(source[key], kind=kind))
    return tuple(dict.fromkeys(values))


def _operand_values(
    operand: Mapping[str, Any],
    plan: Mapping[str, Any],
    kind: str,
) -> tuple[str | int, ...]:
    values: list[str | int] = []
    for key in _OPERAND_FIELD_KEYS[kind]:
        if key in operand and operand[key] is not None:
            values.extend(_normalise_values(operand[key], kind=kind))
    if values:
        return tuple(dict.fromkeys(values))
    inherited = _constraint_values(plan, kind)
    return inherited if len(inherited) == 1 else ()


def _candidate_id(plan: Mapping[str, Any]) -> str:
    return _text(_first_value(plan, ("candidate_id", "plan_id")))


def _is_baseline(plan: Mapping[str, Any]) -> bool:
    for key in ("is_baseline", "baseline", "baseline_plan", "baseline_candidate"):
        if plan.get(key) is True:
            return True
    for key in ("candidate_type", "plan_type", "route_family", "route", "tier"):
        value = _token(plan.get(key))
        if value in {"BASELINE", "BASELINE_V11", "V11", "CONTROL", "CONTROL_V1"}:
            return True
    return False


def _verification_status(plan: Mapping[str, Any]) -> str:
    verification = plan.get("verification")
    if isinstance(verification, Mapping):
        nested = _status(verification)
        if nested:
            return nested
    return _status(
        _first_value(
            plan,
            ("verification_status", "verification_class", "verification_result"),
        )
    )


def _hard_rejection_reasons(plan: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if plan.get("filter_passed") is False or plan.get("survived_filter") is False:
        reasons.append("FILTER_REJECTED")
    if _token(plan.get("candidate_status")) in _REJECTED_STATUSES:
        reasons.append("CANDIDATE_REJECTED")
    if _verification_status(plan) in _REJECTED_STATUSES:
        reasons.append("VERIFICATION_REJECTED")

    for kind in ("period", "entity", "scope", "unit"):
        match_value = plan.get(f"{kind}_match")
        if match_value is False:
            reasons.append(f"{kind.upper()}_MISMATCH")
        status_value = plan.get(f"{kind}_status")
        if status_value is not None and not _status_pass(status_value):
            reasons.append(f"{kind.upper()}_UNRESOLVED_OR_MISMATCH")
        validation_value = plan.get(f"{kind}_validation")
        if validation_value is not None and not _status_pass(validation_value):
            reasons.append(f"{kind.upper()}_UNRESOLVED_OR_MISMATCH")

    for key in ("replay_match", "replay_matches_answer", "replay_pass"):
        if plan.get(key) is False:
            reasons.append("REPLAY_MISMATCH")
    for key in ("replay_status", "execution_status"):
        value = plan.get(key)
        if value is not None and _status_fail(value):
            reasons.append("REPLAY_MISMATCH")
    for key in ("replay", "execution", "replay_result"):
        value = plan.get(key)
        if isinstance(value, Mapping):
            status_value = _status(value)
            if status_value and status_value not in _PASS_STATUSES:
                reasons.append("REPLAY_MISMATCH")
            for match_key in ("match", "matches_answer", "replay_match"):
                if value.get(match_key) is False:
                    reasons.append("REPLAY_MISMATCH")
    return list(dict.fromkeys(reasons))


def _semantic_complete(plan: Mapping[str, Any]) -> tuple[bool, str]:
    raw = _first_value(
        plan,
        ("semantic_completeness", "semantic_complete", "semantic_status"),
    )
    if raw is None:
        return False, "SEMANTIC_COMPLETENESS_MISSING"
    if isinstance(raw, bool):
        return raw, "SEMANTIC_COMPLETE" if raw else "SEMANTIC_COMPLETENESS_INSUFFICIENT"
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        passed = _finite_float(raw, -1.0) >= SEMANTIC_COMPLETENESS_THRESHOLD
        return passed, "SEMANTIC_COMPLETE" if passed else "SEMANTIC_COMPLETENESS_INSUFFICIENT"
    if isinstance(raw, Mapping):
        status = _status(raw)
        if status:
            passed = status in _PASS_STATUSES
        else:
            booleans = [
                value
                for key, value in raw.items()
                if key in {"complete", "entity", "period", "scope", "unit", "formula", "metric"}
                and isinstance(value, bool)
            ]
            passed = bool(booleans) and all(booleans)
        if "score" in raw:
            passed = passed and _finite_float(raw.get("score"), -1.0) >= SEMANTIC_COMPLETENESS_THRESHOLD
        return passed, "SEMANTIC_COMPLETE" if passed else "SEMANTIC_COMPLETENESS_INSUFFICIENT"
    status = _token(raw)
    passed = status in _PASS_STATUSES
    return passed, "SEMANTIC_COMPLETE" if passed else "SEMANTIC_COMPLETENESS_INSUFFICIENT"


def _operand_id(operand: Mapping[str, Any]) -> str:
    return _text(_first_value(operand, ("operand_id", "id", "name")))


def _operand_list(plan: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    raw_operands = plan.get("operands")
    if not isinstance(raw_operands, (list, tuple)) or not raw_operands:
        return [], ["OPERANDS_MISSING"]
    operands: list[Mapping[str, Any]] = []
    ids: list[str] = []
    reasons: list[str] = []
    for operand in raw_operands:
        if not isinstance(operand, Mapping):
            reasons.append("OPERAND_INVALID")
            continue
        operand_id = _operand_id(operand)
        if not operand_id or _token(operand_id) in _PLACEHOLDER_TOKENS:
            reasons.append("OPERAND_ID_MISSING")
            continue
        if operand_id in ids:
            reasons.append("DUPLICATE_OPERAND_ID")
            continue
        ids.append(operand_id)
        operands.append(operand)
    if len(operands) != len(raw_operands):
        reasons.append("OPERAND_INCOMPLETE")
    completeness = plan.get("operand_completeness")
    if completeness is not None:
        if isinstance(completeness, bool):
            if not completeness:
                reasons.append("OPERAND_COMPLETENESS_INSUFFICIENT")
        elif not _status_pass(completeness):
            reasons.append("OPERAND_COMPLETENESS_INSUFFICIENT")
    return operands, list(dict.fromkeys(reasons))


def _walk_ast(node: object, operand_ids: set[str], references: set[str]) -> bool:
    if isinstance(node, Mapping):
        if not node:
            return False
        for key, value in node.items():
            if _token(key) in {"OP", "OPERATOR"}:
                continue
            if value is None:
                return False
            if not _walk_ast(value, operand_ids, references):
                return False
        return True
    if isinstance(node, (list, tuple)):
        if not node:
            return False
        return all(_walk_ast(value, operand_ids, references) for value in node)
    if isinstance(node, str):
        text = node.strip()
        if text in operand_ids:
            references.add(text)
            return True
        if _token(text) in _PLACEHOLDER_TOKENS:
            return False
    return True


def _ast_reference_tokens(node: object, *, in_reference: bool = False) -> list[str]:
    """Collect string leaves under AST fields that require operand IDs."""

    if isinstance(node, Mapping):
        tokens: list[str] = []
        for key, value in node.items():
            tokens.extend(
                _ast_reference_tokens(
                    value,
                    in_reference=in_reference or _token(key) in _AST_REFERENCE_KEYS,
                )
            )
        return tokens
    if isinstance(node, (list, tuple)):
        tokens: list[str] = []
        for value in node:
            tokens.extend(_ast_reference_tokens(value, in_reference=in_reference))
        return tokens
    if in_reference and isinstance(node, str) and node.strip():
        return [node.strip()]
    return []


def _ast_quality(plan: Mapping[str, Any], operand_ids: Sequence[str]) -> tuple[bool, str]:
    ast = plan.get("operation_ast")
    if not isinstance(ast, Mapping) or not ast:
        return False, "OPERATION_AST_MISSING"
    op = _token(ast.get("op") or ast.get("operator"))
    if op in _INCOMPLETE_AST_OPS:
        return False, "OPERATION_AST_INCOMPLETE"
    references: set[str] = set()
    if not _walk_ast(ast, set(operand_ids), references):
        return False, "OPERATION_AST_INCOMPLETE"
    unknown_references = {
        value
        for value in _ast_reference_tokens(ast)
        if value not in set(operand_ids)
    }
    if unknown_references:
        return False, "OPERATION_AST_UNKNOWN_OPERAND_REFERENCE"
    if not references:
        return False, "OPERATION_AST_HAS_NO_OPERAND_REFERENCES"
    args = ast.get("args")
    if "args" in ast and (not isinstance(args, (list, tuple)) or not args):
        return False, "OPERATION_AST_ARGS_INCOMPLETE"
    if op in {"LOOKUP", "SELECT", "IDENTITY"} and len(references) != 1:
        return False, "OPERATION_AST_OPERAND_ARITY_INVALID"
    if op in {"SUBTRACT", "MINUS", "DIVIDE", "MULTIPLY", "ADD", "DIFFERENCE", "RATIO"} and len(references) < 2:
        return False, "OPERATION_AST_OPERAND_ARITY_INVALID"
    if op in {"PERCENTAGE_CHANGE", "GROWTH_RATE", "CHANGE_RATE"} and len(references) < 2:
        return False, "OPERATION_AST_OPERAND_ARITY_INVALID"
    if len(operand_ids) > 1 and not set(operand_ids).issubset(references):
        return False, "OPERATION_AST_MISSING_OPERAND_REFERENCE"
    return True, "OPERATION_AST_COMPLETE"


def _source_mapping(operand: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("source_coordinate", "coordinate", "source", "binding", "evidence"):
        value = operand.get(key)
        if isinstance(value, Mapping):
            return value
    return operand


def _coordinate_for(
    plan: Mapping[str, Any],
    operand: Mapping[str, Any],
    index: int,
) -> Mapping[str, Any]:
    direct = _source_mapping(operand)
    if any(key in direct for key in (*_SOURCE_UID_KEYS, *_ROW_KEYS, *_COLUMN_KEYS)):
        return direct
    for key in ("source_coordinates", "coordinates", "evidence", "bindings"):
        values = plan.get(key)
        if isinstance(values, Mapping):
            operand_id = _operand_id(operand)
            value = values.get(operand_id)
            if isinstance(value, Mapping):
                return value
        elif isinstance(values, (list, tuple)) and index < len(values):
            value = values[index]
            if isinstance(value, Mapping):
                return value
    return direct


def _source_coordinate_ok(coordinate: Mapping[str, Any]) -> bool:
    uid = _text(_first_value(coordinate, _SOURCE_UID_KEYS))
    row = _first_value(coordinate, _ROW_KEYS)
    column = _first_value(coordinate, _COLUMN_KEYS)
    return bool(uid) and isinstance(row, int) and not isinstance(row, bool) and row >= 0 and isinstance(column, int) and not isinstance(column, bool) and column >= 0


def _source_hash(plan: Mapping[str, Any], operand: Mapping[str, Any], coordinate: Mapping[str, Any]) -> str:
    value = _first_value(coordinate, _HASH_KEYS)
    if value is None:
        value = _first_value(operand, _HASH_KEYS)
    if value is None:
        value = _first_value(plan, _HASH_KEYS)
    return _text(value)


def _field_consistency(plan: Mapping[str, Any], operands: Sequence[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for kind in ("period", "entity", "scope", "unit"):
        expected = _constraint_values(plan, kind)
        for operand in operands:
            observed = _operand_values(operand, plan, kind)
            if not observed:
                reasons.append(f"{kind.upper()}_MISSING")
                continue
            if expected and not set(observed).issubset(set(expected)):
                reasons.append(f"{kind.upper()}_MISMATCH")
        # Scope and unit are single binding dimensions.  A mixed value is
        # unsafe even when a producer omitted a question-level expectation.
        if kind in {"scope", "unit"}:
            observed_values = {
                value
                for operand in operands
                for value in _operand_values(operand, plan, kind)
            }
            if len(observed_values) > 1:
                reasons.append(f"{kind.upper()}_INCONSISTENT")
    return not reasons, list(dict.fromkeys(reasons))


def _answer_value(plan: Mapping[str, Any]) -> Decimal | None:
    for key in _ANSWER_KEYS:
        if key in plan and plan[key] is not None:
            value = _decimal(plan[key])
            if value is not None:
                return value
    replay = plan.get("replay")
    if isinstance(replay, Mapping):
        for key in _ANSWER_KEYS:
            if key in replay and replay[key] is not None:
                value = _decimal(replay[key])
                if value is not None:
                    return value
    return None


def _replay_quality(plan: Mapping[str, Any], answer: Decimal | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    explicit_pass = False
    replay_answers: list[Decimal] = []

    for key in ("replay_pass", "execution_replay_ready"):
        value = plan.get(key)
        if value is True:
            explicit_pass = True
        elif value is False:
            reasons.append("REPLAY_MISMATCH")
    for key in ("replay_match", "replay_matches_answer"):
        value = plan.get(key)
        if value is True:
            explicit_pass = True
        elif value is False:
            reasons.append("REPLAY_MISMATCH")

    for key in ("replay_status", "execution_status"):
        value = plan.get(key)
        if value is not None:
            if _status_pass(value):
                explicit_pass = True
            else:
                reasons.append("REPLAY_MISMATCH")

    replay_objects: list[Mapping[str, Any]] = []
    for key in ("replay", "execution", "replay_result"):
        value = plan.get(key)
        if isinstance(value, Mapping):
            replay_objects.append(value)
        elif value is True:
            explicit_pass = True
        elif value is False:
            reasons.append("REPLAY_MISMATCH")
    for replay in replay_objects:
        status = _status(replay)
        if status:
            if _status_pass(status):
                explicit_pass = True
            else:
                reasons.append("REPLAY_MISMATCH")
        if replay.get("match") is True or replay.get("matches_answer") is True:
            explicit_pass = True
        if replay.get("match") is False or replay.get("matches_answer") is False:
            reasons.append("REPLAY_MISMATCH")
        for key in _ANSWER_KEYS:
            if key in replay and replay[key] is not None:
                value = _decimal(replay[key])
                if value is not None:
                    replay_answers.append(value)

    for key in ("replay_answer_decimal", "replayed_answer", "replay_answer", "execution_answer"):
        if key in plan and plan[key] is not None:
            value = _decimal(plan[key])
            if value is not None:
                replay_answers.append(value)

    if answer is None:
        reasons.append("ANSWER_MISSING_OR_INVALID")
    if replay_answers and answer is not None and any(value != answer for value in replay_answers):
        reasons.append("REPLAY_MISMATCH")
    if replay_answers and answer is not None and all(value == answer for value in replay_answers):
        explicit_pass = True
    if not explicit_pass:
        reasons.append("REPLAY_NOT_PROVEN")
    reasons = list(dict.fromkeys(reasons))
    return not reasons, reasons


def _plan_score(plan: Mapping[str, Any]) -> float:
    for key in ("candidate_score", "plan_score", "validity_score", "validity_probability", "retrieval_score"):
        if key in plan and plan[key] is not None:
            return _finite_float(plan[key])
    return 0.0


def _route_priority(plan: Mapping[str, Any]) -> float:
    return _finite_float(plan.get("route_priority"), 0.0)


def _verification_rank(status: str) -> int:
    return {"VERIFIED": 3, "PARTIAL": 2, "UNRESOLVED": 1}.get(status, 0)


@dataclass(frozen=True)
class _PlanEvaluation:
    plan: Mapping[str, Any]
    candidate_id: str
    is_baseline: bool
    valid: bool
    fallback: bool
    reason_codes: tuple[str, ...]
    quality: tuple[int, int, int, int, int, int, int, int]
    score: float
    route_priority: float

    @property
    def rank_without_route(self) -> tuple[object, ...]:
        return (*self.quality, self.score, -int(self.is_baseline))


def _evaluate_plan(plan: Mapping[str, Any]) -> _PlanEvaluation:
    candidate_id = _candidate_id(plan)
    baseline = _is_baseline(plan)
    hard_reasons = _hard_rejection_reasons(plan)
    answer = _answer_value(plan)
    operands, operand_reasons = _operand_list(plan)
    operand_ids = [_operand_id(operand) for operand in operands]
    required_raw = _first_value(
        plan,
        ("required_operand_ids", "question_operand_ids", "expected_operand_ids"),
    )
    required_operand_ids = tuple(
        _text(value)
        for value in (
            required_raw
            if isinstance(required_raw, (list, tuple, set, frozenset))
            else [required_raw]
        )
        if _text(value)
    ) if required_raw is not None else ()
    if required_operand_ids and set(operand_ids) != set(required_operand_ids):
        operand_reasons.append("OPERAND_SET_MISMATCH")
    ast_ok, ast_reason = _ast_quality(plan, operand_ids) if operands else (False, "OPERATION_AST_MISSING")
    semantic_ok, semantic_reason = _semantic_complete(plan)
    consistency_ok, consistency_reasons = _field_consistency(plan, operands) if operands else (False, ["OPERANDS_MISSING"])

    coordinates_ok = bool(operands)
    hashes_ok = bool(operands)
    coordinate_reasons: list[str] = []
    for index, operand in enumerate(operands):
        coordinate = _coordinate_for(plan, operand, index)
        if not _source_coordinate_ok(coordinate):
            coordinates_ok = False
            coordinate_reasons.append("SOURCE_COORDINATE_MISSING_OR_INVALID")
        if not _source_hash(plan, operand, coordinate):
            hashes_ok = False
    if not coordinates_ok:
        coordinate_reasons.append("SOURCE_COORDINATE_INCOMPLETE")
    if not hashes_ok:
        coordinate_reasons.append("SOURCE_HASH_MISSING")

    replay_ok, replay_reasons = _replay_quality(plan, answer)
    reasons = [*hard_reasons, *operand_reasons, ast_reason, semantic_reason, *consistency_reasons, *coordinate_reasons, *replay_reasons]
    reasons = list(dict.fromkeys(reason for reason in reasons if reason))

    # A normal candidate must prove every structural dimension.  Source hash
    # is a ranking signal rather than a hard gate because older producers may
    # have exact coordinates but no materialized hash yet.
    complete = bool(
        candidate_id
        and answer is not None
        and not hard_reasons
        and not operand_reasons
        and ast_ok
        and semantic_ok
        and consistency_ok
        and coordinates_ok
        and replay_ok
    )

    # The legacy baseline can survive only as an explicitly non-authoritative
    # fallback.  Explicit semantic/source/replay mismatches never get this
    # exception; merely missing old metadata does.
    fallback_blockers = {
        reason
        for reason in hard_reasons
        if reason in {
            "REPLAY_MISMATCH",
            "FILTER_REJECTED",
            "CANDIDATE_REJECTED",
            "VERIFICATION_REJECTED",
        }
        or reason.startswith(("PERIOD_", "ENTITY_", "SCOPE_", "UNIT_"))
    }
    fallback = bool(baseline and candidate_id and answer is not None and not fallback_blockers)
    valid = complete or fallback
    if not candidate_id:
        reasons.insert(0, "CANDIDATE_ID_MISSING")
    if valid and fallback and not complete:
        reasons = ["BASELINE_FALLBACK", *reasons]
    if valid and complete:
        reasons = [
            "PLAN_COMPLETE",
            ast_reason,
            "OPERANDS_COMPLETE",
            "SEMANTIC_COMPLETE",
            "PERIOD_ENTITY_SCOPE_UNIT_CONSISTENT",
            "SOURCE_COORDINATES_COMPLETE",
            "REPLAY_PASS",
            *( ["SOURCE_HASH_PRESENT"] if hashes_ok else ["SOURCE_HASH_MISSING"] ),
            *reasons,
        ]
    reasons = list(dict.fromkeys(reason for reason in reasons if reason))

    quality = (
        1 if complete else 0,
        1 if ast_ok else 0,
        1 if bool(operands) and not operand_reasons else 0,
        1 if semantic_ok else 0,
        1 if consistency_ok else 0,
        1 if coordinates_ok else 0,
        1 if hashes_ok else 0,
        1 if replay_ok else 0,
    )
    return _PlanEvaluation(
        plan=plan,
        candidate_id=candidate_id,
        is_baseline=baseline,
        valid=valid,
        fallback=fallback and not complete,
        reason_codes=tuple(reasons),
        quality=quality,
        score=_plan_score(plan),
        route_priority=_route_priority(plan),
    )


def _sort_key(evaluation: _PlanEvaluation) -> tuple[object, ...]:
    # Every completeness dimension is compared before any route priority.
    # Baseline preference is a narrow retention guard: on an equal-quality
    # tie, a new plan must be strictly better to displace the baseline.
    return (
        *(-value for value in evaluation.quality),
        -evaluation.score,
        -int(evaluation.is_baseline),
        -evaluation.route_priority,
        evaluation.candidate_id,
    )


def _safe_selected_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        selected = deepcopy(dict(plan))
    except (TypeError, ValueError):
        selected = dict(plan)
    # Do not let an input producer's authority-looking metadata leak through
    # the selector boundary.  The common case has no such keys, preserving a
    # faithful plan copy for callers and tests.
    for key in (
        "answer_authority",
        "may_authorize_answer",
        "release_authorized",
        "submission_eligible",
        "training_eligible",
        "promotion_allowed",
        "authoritative",
    ):
        if key in selected:
            selected[key] = False
    return selected


def _decision_base(
    *,
    status: str,
    reason_codes: Sequence[str],
    candidate_count: int,
    considered_count: int,
    eligible_count: int,
    rejected_plans: Sequence[Mapping[str, Any]],
    truncated_count: int,
) -> dict[str, Any]:
    return {
        "protocol": ANSWER_LEVEL_SELECTOR_PROTOCOL,
        "status": status,
        "selection_status": status,
        "selection_reason_codes": list(dict.fromkeys(reason_codes)),
        "selected_candidate_id": None,
        "selected_plan": None,
        "answer_channel": "ABSTAIN",
        "candidate_only": False,
        "authority": "none",
        "may_authorize_answer": False,
        "answer_authority": False,
        "release_authorized": False,
        "submission_eligible": False,
        "training_eligible": False,
        "promotion_allowed": False,
        "candidate_count": candidate_count,
        "considered_candidate_count": considered_count,
        "eligible_candidate_count": eligible_count,
        "rejected_plans": list(rejected_plans),
        "truncated_candidate_count": truncated_count,
    }


class AnswerLevelSelector:
    """Select one complete answer plan without granting answer authority."""

    def __init__(self, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> None:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise TypeError("max_candidates must be an integer")
        if max_candidates < 1 or max_candidates > MAX_MAX_CANDIDATES:
            raise ValueError(
                f"max_candidates must be between 1 and {MAX_MAX_CANDIDATES}"
            )
        self.max_candidates = max_candidates

    def __call__(self, plans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self.select(plans)

    def select(self, plans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Return a deterministic selection decision or an explicit abstain.

        Duplicate candidate IDs are a whole-pool error.  Valid evaluations
        are sorted by completeness and only then by score, baseline retention,
        route priority, and candidate ID.  The input mappings are never
        mutated.
        """

        if isinstance(plans, (str, bytes, bytearray)) or not isinstance(plans, Sequence):
            return _decision_base(
                status="ABSTAIN",
                reason_codes=["INVALID_PLAN_SEQUENCE"],
                candidate_count=0,
                considered_count=0,
                eligible_count=0,
                rejected_plans=[],
                truncated_count=0,
            )

        raw_plans = list(plans)
        candidate_count = len(raw_plans)
        ids: list[str] = []
        malformed: list[dict[str, Any]] = []
        for index, raw_plan in enumerate(raw_plans):
            if not isinstance(raw_plan, Mapping):
                malformed.append(
                    {
                        "candidate_id": None,
                        "reason_codes": ["PLAN_NOT_MAPPING"],
                        "input_index": index,
                    }
                )
                continue
            candidate_id = _candidate_id(raw_plan)
            if candidate_id:
                ids.append(candidate_id)
            else:
                malformed.append(
                    {
                        "candidate_id": None,
                        "reason_codes": ["CANDIDATE_ID_MISSING"],
                        "input_index": index,
                    }
                )
        counts: dict[str, int] = {}
        for candidate_id in ids:
            counts[candidate_id] = counts.get(candidate_id, 0) + 1
        duplicates = sorted(candidate_id for candidate_id, count in counts.items() if count > 1)
        if duplicates:
            duplicate_rows = [
                {
                    "candidate_id": candidate_id,
                    "reason_codes": ["DUPLICATE_CANDIDATE_ID"],
                }
                for candidate_id in duplicates
            ]
            return _decision_base(
                status="ABSTAIN",
                reason_codes=["DUPLICATE_CANDIDATE_ID", "ABSTAIN_DUPLICATE_CANDIDATE_ID"],
                candidate_count=candidate_count,
                considered_count=0,
                eligible_count=0,
                rejected_plans=[*malformed, *duplicate_rows],
                truncated_count=0,
            )

        evaluations: list[_PlanEvaluation] = []
        rejected: list[dict[str, Any]] = [*malformed]
        for raw_plan in raw_plans:
            if not isinstance(raw_plan, Mapping):
                continue
            evaluation = _evaluate_plan(raw_plan)
            if evaluation.valid:
                evaluations.append(evaluation)
            else:
                rejected.append(
                    {
                        "candidate_id": evaluation.candidate_id or None,
                        "reason_codes": list(evaluation.reason_codes) or ["PLAN_INVALID"],
                    }
                )

        ordered = sorted(evaluations, key=_sort_key)
        if len(ordered) > self.max_candidates:
            bounded = ordered[: self.max_candidates]
            baseline = next((item for item in ordered if item.is_baseline), None)
            if baseline is not None and baseline not in bounded:
                bounded[-1] = baseline
                bounded_by_id = {item.candidate_id: item for item in bounded}
                bounded = sorted(bounded_by_id.values(), key=_sort_key)
            truncated_count = len(ordered) - len(bounded)
            ordered = bounded
        else:
            truncated_count = 0
        if not ordered:
            result = _decision_base(
                status="ABSTAIN",
                reason_codes=["ABSTAIN_NO_VALID_PLAN"],
                candidate_count=candidate_count,
                considered_count=0,
                eligible_count=0,
                rejected_plans=rejected,
                truncated_count=0,
            )
            return result

        selected = ordered[0]
        reason_codes: list[str] = list(selected.reason_codes)
        if selected.is_baseline:
            reason_codes.insert(0, "BASELINE_RETAINED")
        else:
            reason_codes.insert(0, "SELECTED_COMPLETE_PLAN")
        if len(ordered) > 1 and selected.rank_without_route == ordered[1].rank_without_route:
            reason_codes.append("ROUTE_PRIORITY_TIE_BREAK")
        reason_codes.append("CANDIDATE_ONLY_NO_AUTHORITY")
        result = _decision_base(
            status="SELECTED",
            reason_codes=reason_codes,
            candidate_count=candidate_count,
            considered_count=len(ordered),
            eligible_count=len(evaluations),
            rejected_plans=rejected,
            truncated_count=truncated_count,
        )
        result.update(
            {
                "selected_candidate_id": selected.candidate_id,
                "selected_plan": _safe_selected_plan(selected.plan),
                "answer_channel": "BEST_EFFORT_CANDIDATE",
                "candidate_only": True,
                "selected_verification_status": _verification_status(selected.plan) or "UNRESOLVED",
                "selected_plan_is_baseline": selected.is_baseline,
                "selected_plan_fallback": selected.fallback,
            }
        )
        return result


def select_answer_level(
    plans: Sequence[Mapping[str, Any]],
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any]:
    """Functional API for callers that do not need to retain a selector."""

    return AnswerLevelSelector(max_candidates=max_candidates).select(plans)


def select_best_candidate_plan(
    plans: Sequence[Mapping[str, Any]],
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any] | None:
    """Return only the selected plan, or ``None`` after a fail-closed abstain."""

    decision = select_answer_level(plans, max_candidates=max_candidates)
    selected = decision.get("selected_plan")
    return selected if isinstance(selected, dict) else None


__all__ = [
    "ANSWER_LEVEL_SELECTOR_PROTOCOL",
    "DEFAULT_MAX_CANDIDATES",
    "MAX_MAX_CANDIDATES",
    "SEMANTIC_COMPLETENESS_THRESHOLD",
    "AnswerLevelSelector",
    "select_answer_level",
    "select_best_candidate_plan",
]
