"""Deterministic hand-off between bounded LLM evidence review stages.

The LLM only supplies literal source-cell selections. This module parses the
verified selections, enforces per-entity unit consistency and performs the
allow-listed financial calculation that decides which entities flow to the
next retrieval stage. It never accepts a model-written answer.
"""
from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any, Mapping

from .execution import RELIABLE_BINDING_WARNINGS, parse_decimal


LLM_STAGE_EXECUTION_PROTOCOL = "bounded_llm_deterministic_stage_execution_v1"


def _format(value: Decimal) -> str:
    return format(value, "f")


def _blocked(reason: str, detail: str) -> dict[str, Any]:
    return {
        "protocol": LLM_STAGE_EXECUTION_PROTOCOL,
        "status": "stage_blocked",
        "reason_codes": [reason],
        "detail": detail,
        "submission_eligible": False,
        "human_verified": False,
    }


def _expected_bindings(stage: Mapping[str, Any], review: Mapping[str, Any]) -> set[tuple[str, str]]:
    entities = [str(value) for value in stage.get("entities") or []]
    if not entities:
        entities = sorted(
            {str(binding.get("company") or "") for binding in review.get("selected_bindings") or [] if binding.get("company")}
        )
    variables = {str(value) for value in stage.get("required_variables") or []}
    if not entities or not variables:
        raise ValueError("Stage lacks explicit entities or required variables")
    return {(entity, variable) for entity in entities for variable in variables}


def operands_from_verified_review(
    stage: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, dict[str, Decimal]]:
    """Decode model-reviewed literals only after the packet verifier passed."""
    if str(review.get("annotation_status") or "") != "machine_provisional":
        raise ValueError("Only machine_provisional literal-verified reviews may be executed")
    expected = _expected_bindings(stage, review)
    values: dict[str, dict[str, Decimal]] = {}
    units: dict[str, set[tuple[str, ...]]] = {}
    seen: set[tuple[str, str]] = set()
    for binding in review.get("selected_bindings") or []:
        entity = str(binding.get("company") or "")
        variable = str(binding.get("variable_id") or "")
        key = (entity, variable)
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate selected binding: {key}")
        parsed = parse_decimal(binding.get("raw_value"))
        if parsed.value is None or any(
            warning not in RELIABLE_BINDING_WARNINGS for warning in parsed.warnings
        ):
            raise ValueError(f"Unsafe parsed source value for {entity}/{variable}: {parsed.warnings}")
        unit = tuple(str(value) for value in binding.get("unit_labels") or [] if str(value))
        if not unit:
            raise ValueError(f"Missing source unit for {entity}/{variable}")
        values.setdefault(entity, {})[variable] = Decimal(parsed.value)
        units.setdefault(entity, set()).add(unit)
        seen.add(key)
    if seen != expected:
        missing = sorted(expected - seen)
        raise ValueError(f"Missing selected bindings: {missing}")
    inconsistent_units = sorted(entity for entity, group in units.items() if len(group) != 1)
    if inconsistent_units:
        raise ValueError("Inconsistent source units within entity: " + ", ".join(inconsistent_units))
    return values


def _metric_value(metric_id: str, values: Mapping[str, Decimal]) -> Decimal:
    try:
        if metric_id == "quick_ratio":
            return (values["current_assets"] - values["inventory"]) / values["current_liabilities"]
        if metric_id == "net_profit_margin":
            return values["net_income"] / values["net_revenue"] * Decimal("100")
        if metric_id == "gross_profit_margin":
            return values["gross_profit"] / values["net_revenue"] * Decimal("100")
        if metric_id == "interest_coverage":
            interest = abs(values["interest_expense"])
            return (values["profit_before_tax"] + interest) / interest
        if metric_id == "operating_cash_flow":
            return values["operating_cash_flow"]
    except (KeyError, DivisionByZero, InvalidOperation) as error:
        raise ValueError(f"Cannot calculate {metric_id}: {error}") from error
    raise ValueError(f"Unsupported metric: {metric_id}")


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


def execute_reviewed_stage(stage: Mapping[str, Any], review: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate one allow-listed routing stage from literal-reviewed operands."""
    try:
        operands = operands_from_verified_review(stage, review)
        metric_id = str(stage.get("metric_id") or "")
        values = {entity: _metric_value(metric_id, variables) for entity, variables in operands.items()}
        result: dict[str, Any] = {
            "protocol": LLM_STAGE_EXECUTION_PROTOCOL,
            "status": "stage_complete",
            "stage_id": str(stage.get("stage_id") or ""),
            "metric_id": metric_id,
            "metric_values": {entity: _format(value) for entity, value in values.items()},
            "submission_eligible": False,
            "human_verified": False,
        }
        if str(stage.get("stage_id") or "") == "quick_ratio_screen":
            threshold = _median(list(values.values()))
            eligible = [entity for entity, value in values.items() if value < threshold]
            if not eligible:
                return _blocked("no_entity_below_median", "Strict median filter selected no entity")
            result.update(
                {
                    "aggregate": "median",
                    "aggregate_value": _format(threshold),
                    "eligible_entities": eligible,
                }
            )
            return result
        if metric_id == "net_profit_margin" and str(stage.get("aggregate") or "") == "average":
            average = sum(values.values(), Decimal("0")) / Decimal(len(values))
            result.update(
                {
                    "aggregate": "average",
                    "aggregate_value": _format(average),
                    "result_unit": "percent",
                    "final_stage": True,
                }
            )
            return result
        if metric_id == "gross_profit_margin":
            return result
        if metric_id == "interest_coverage" and str(stage.get("aggregate") or "") == "selected":
            if len(values) != 1:
                return _blocked(
                    "interest_coverage_target_not_unique",
                    "Interest Coverage final stage requires exactly one winning entity",
                )
            entity, value = next(iter(values.items()))
            result.update(
                {
                    "aggregate": "selected",
                    "aggregate_value": _format(value),
                    "result_unit": "times",
                    "winning_entity": entity,
                    "final_stage": True,
                }
            )
            return result
        return _blocked(
            "unsupported_deterministic_stage_contract",
            f"No exact execution contract for stage {stage.get('stage_id')!r}",
        )
    except (TypeError, ValueError, DivisionByZero, InvalidOperation) as error:
        return _blocked("deterministic_stage_execution_failed", str(error))


def execute_gross_profit_margin_change_rank(
    stage: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose the unique largest signed GPM change from two prior executions."""
    try:
        inputs = dict(stage.get("state_inputs") or {})
        old = dict(state.get(str(inputs.get("old") or "")) or {})
        new = dict(state.get(str(inputs.get("new") or "")) or {})
        old_values = {str(key): Decimal(str(value)) for key, value in (old.get("metric_values") or {}).items()}
        new_values = {str(key): Decimal(str(value)) for key, value in (new.get("metric_values") or {}).items()}
        if str(old.get("status") or "") != "stage_complete" or str(new.get("status") or "") != "stage_complete":
            raise ValueError("Gross-margin source stages are not complete")
        if not old_values or set(old_values) != set(new_values):
            raise ValueError("Gross-margin source stages cover different entity sets")
        changes = {entity: new_values[entity] - old_values[entity] for entity in sorted(old_values)}
        best = max(changes.values())
        winners = [entity for entity, value in changes.items() if value == best]
        if len(winners) != 1:
            return _blocked(
                "gross_profit_margin_change_tied",
                "Largest signed gross-profit-margin change is not unique",
            )
        return {
            "protocol": LLM_STAGE_EXECUTION_PROTOCOL,
            "status": "stage_complete",
            "stage_id": str(stage.get("stage_id") or ""),
            "metric_id": "gross_profit_margin_change",
            "metric_values": {entity: _format(value) for entity, value in changes.items()},
            "aggregate": "argmax_unique_signed_change",
            "aggregate_value": _format(best),
            "winning_entity": winners[0],
            "submission_eligible": False,
            "human_verified": False,
        }
    except (TypeError, ValueError, InvalidOperation) as error:
        return _blocked("deterministic_stage_execution_failed", str(error))
