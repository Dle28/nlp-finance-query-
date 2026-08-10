"""Deterministic structural fingerprints for ViFinQA question plans.

Fingerprints describe the *shape* of a question, not its answer.  They are
used to measure planner coverage and route unsupported structures to abstain;
they never make a retrieval or provenance decision.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


QUERY_FINGERPRINT_SCHEMA_VERSION = 1
KNOWN_OPERATORS = {
    "lookup",
    "add",
    "sum",
    "subtract",
    "absolute_difference",
    "multiply",
    "divide",
    "percentage_change",
    "mean",
    "median",
    "min",
    "max",
    "count",
    "cagr",
}


def _cardinality(values: Sequence[Any]) -> str:
    size = len(values)
    return "none" if size == 0 else "one" if size == 1 else "many"


def _unit_class(unit: object) -> str:
    value = str(unit or "").strip().casefold()
    if not value:
        return "unspecified"
    if value in {"vnd", "thousand_vnd", "million_vnd", "billion_vnd", "trillion_vnd"}:
        return "monetary"
    if value in {"percent", "times"}:
        return "ratio"
    return "other"


def canonical_operator_skeleton(value: Any) -> Any:
    """Remove question-specific operand names while retaining AST topology."""
    if isinstance(value, Mapping):
        if "op" in value:
            return {
                "op": str(value.get("op") or "unknown"),
                "args": [canonical_operator_skeleton(arg) for arg in value.get("args") or []],
            }
        return {str(key): canonical_operator_skeleton(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [canonical_operator_skeleton(item) for item in value]
    if isinstance(value, str):
        return "$operand"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "$number"
    return "$literal"


def _warning_categories(warnings: Sequence[object]) -> list[str]:
    categories: set[str] = set()
    for warning in warnings:
        text = str(warning).casefold()
        if "complex operand decomposition" in text or "semantic planner" in text:
            categories.add("operand_decomposition_required")
        elif "no ticker" in text or "company alias" in text:
            categories.add("entity_unresolved")
        elif "retrieval should run per operand" in text:
            categories.add("per_operand_retrieval_required")
        elif text:
            categories.add("other_planner_warning")
    return sorted(categories)


def build_query_fingerprint(
    review_item: Mapping[str, Any],
    *,
    formula_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one hash-stable census row from an existing review item."""
    plan = dict(review_item.get("effective_question_plan") or review_item.get("question_plan") or {})
    ast = dict(plan.get("operation_ast") or {})
    operator = str(ast.get("op") or "unknown")
    warnings = _warning_categories(plan.get("warnings") or [])
    operands = list(plan.get("operands") or [])
    formula = dict((formula_record or {}).get("formula") or {})

    if operator == "plan_required" or operator not in KNOWN_OPERATORS:
        route = "abstain_unknown_program"
    elif "operand_decomposition_required" in warnings or not operands:
        route = "requires_operand_decomposition"
    else:
        route = "operator_contract_candidate"

    payload = {
        "family": str(plan.get("family") or review_item.get("weak_family") or "unknown"),
        "operator_skeleton": canonical_operator_skeleton(ast),
        "operand_roles": sorted(
            str(operand.get("role") or operand.get("operand_id") or "unspecified")
            for operand in operands
        ),
        "entity_cardinality": _cardinality(plan.get("tickers") or []),
        "year_cardinality": _cardinality(plan.get("years") or []),
        "scope_policy": "explicit" if plan.get("scope") else "unspecified",
        "unit_class": _unit_class(plan.get("requested_unit")),
        "formula_id": str(formula.get("formula_id") or "none"),
        "warning_categories": warnings,
        "route": route,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": QUERY_FINGERPRINT_SCHEMA_VERSION,
        "question_id": int(review_item["id"]),
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        **payload,
    }

