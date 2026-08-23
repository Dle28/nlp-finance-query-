"""Generic deterministic stage runner above the numeric Operator Registry."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .execution import execute_ast, validate_operation_ast


STAGED_EXECUTION_PROTOCOL = "generic_staged_ast_shadow_v1"


@dataclass(frozen=True, slots=True)
class StageOperatorContract:
    name: str
    required_config: tuple[str, ...]


STAGE_OPERATOR_REGISTRY = {
    "map_ast": StageOperatorContract("map_ast", ("input_bindings", "ast")),
    "reduce_ast": StageOperatorContract("reduce_ast", ("source", "ast")),
    "filter_lt": StageOperatorContract("filter_lt", ("values", "threshold")),
    "filter_all_positive": StageOperatorContract("filter_all_positive", ("input_bindings",)),
    "argmax_unique": StageOperatorContract("argmax_unique", ("source",)),
    "select_entity_value": StageOperatorContract("select_entity_value", ("values", "entity")),
}


def _decimal(value: Any) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"Non-decimal stage value: {value!r}") from error


def _state_ref(value: Any, state: Mapping[str, Any], entity: str | None = None) -> Any:
    if not isinstance(value, str) or not value.startswith("@"):
        return value
    name = value[1:]
    if name not in state:
        raise ValueError(f"Unknown stage state reference: {value}")
    resolved = state[name]
    if entity is not None and isinstance(resolved, Mapping):
        if entity not in resolved:
            raise ValueError(f"Stage state {value} has no entity {entity}")
        return resolved[entity]
    return resolved


def _entities(stage: Mapping[str, Any], state: Mapping[str, Any], defaults: Sequence[str]) -> list[str]:
    source = stage.get("entity_source")
    if source:
        value = _state_ref(str(source), state)
        if not isinstance(value, list):
            raise ValueError("entity_source must resolve to a list")
        return [str(entity) for entity in value]
    return [str(entity) for entity in defaults]


def execute_staged_ast_shadow(
    stages: Sequence[Mapping[str, Any]],
    operand_values: Mapping[str, Any],
    *,
    entities: Sequence[str],
    result_name: str,
    result_unit: str,
) -> dict[str, Any]:
    """Execute a declarative stage list; unknown or malformed stages block."""
    state: dict[str, Any] = {}
    trace: dict[str, Any] = {}
    try:
        for stage in stages:
            operator = str(stage.get("operator") or "")
            contract = STAGE_OPERATOR_REGISTRY.get(operator)
            if contract is None:
                raise ValueError(f"Unsupported stage operator: {operator}")
            config = dict(stage.get("config") or {})
            missing_config = [name for name in contract.required_config if name not in config]
            if missing_config:
                raise ValueError(f"Stage {stage.get('stage_id')} lacks config: {missing_config}")
            output = str(stage.get("output_name") or "")
            if not output or output in state:
                raise ValueError("Every stage output_name must be unique and non-empty")

            if operator == "map_ast":
                ast = config["ast"]
                errors = validate_operation_ast(ast)
                if errors:
                    raise ValueError("Invalid stage AST: " + ",".join(errors))
                mapped: dict[str, Decimal] = {}
                bindings = config["input_bindings"]
                for entity in _entities(stage, state, entities):
                    symbols = dict(bindings.get(entity) or {})
                    if not symbols:
                        raise ValueError(f"No input bindings for entity {entity}")
                    values = {
                        symbol: _decimal(
                            _state_ref(reference, state, entity)
                            if str(reference).startswith("@")
                            else operand_values[reference]
                        )
                        for symbol, reference in symbols.items()
                    }
                    mapped[entity] = _decimal(execute_ast(ast, values))
                value: Any = mapped
            elif operator == "reduce_ast":
                ast = config["ast"]
                errors = validate_operation_ast(ast)
                if errors:
                    raise ValueError("Invalid reduction AST: " + ",".join(errors))
                source = _state_ref(config["source"], state)
                if not isinstance(source, Mapping) or not source:
                    raise ValueError("Reduction source must be a non-empty entity map")
                value = _decimal(execute_ast(ast, {"values": list(source.values())}))
            elif operator == "filter_lt":
                values = _state_ref(config["values"], state)
                threshold = _decimal(_state_ref(config["threshold"], state))
                if not isinstance(values, Mapping):
                    raise ValueError("filter_lt values must be an entity map")
                value = [entity for entity in entities if entity in values and _decimal(values[entity]) < threshold]
                if not value:
                    raise ValueError("No entity passes strict less-than filter")
            elif operator == "filter_all_positive":
                bindings = config["input_bindings"]
                value = [
                    entity
                    for entity in entities
                    if bindings.get(entity)
                    and all(_decimal(operand_values[name]) > 0 for name in bindings[entity])
                ]
                if not value:
                    raise ValueError("No entity passes all-positive filter")
            elif operator == "argmax_unique":
                source = _state_ref(config["source"], state)
                if not isinstance(source, Mapping) or not source:
                    raise ValueError("argmax source must be a non-empty entity map")
                maximum = max(_decimal(item) for item in source.values())
                winners = [entity for entity, item in source.items() if _decimal(item) == maximum]
                if len(winners) != 1:
                    raise ValueError("argmax has no unique winner")
                value = winners[0]
            else:  # select_entity_value
                values = _state_ref(config["values"], state)
                entity = str(_state_ref(config["entity"], state))
                if not isinstance(values, Mapping) or entity not in values:
                    raise ValueError("Selected entity has no computed value")
                value = _decimal(values[entity])
            state[output] = value
            trace[output] = (
                {key: format(item, "f") for key, item in value.items()}
                if isinstance(value, Mapping)
                else format(value, "f") if isinstance(value, Decimal) else value
            )
        if result_name not in state:
            raise ValueError(f"Program result state is missing: {result_name}")
        result = _decimal(state[result_name])
    except (KeyError, TypeError, ValueError, ZeroDivisionError, InvalidOperation) as error:
        return {
            "protocol": STAGED_EXECUTION_PROTOCOL,
            "status": "shadow_blocked",
            "reason_codes": ["staged_ast_contract_failed"],
            "detail": str(error),
            "submission_eligible": False,
            "review_status_promotion_allowed": False,
        }
    winner = str(state["winning_entity"]) if "winning_entity" in state else None
    return {
        "protocol": STAGED_EXECUTION_PROTOCOL,
        "status": "shadow_complete",
        "winner_entity": winner,
        "result_value": format(result, "f"),
        "result_unit": result_unit,
        "stage_trace": trace,
        "submission_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
        "execution_mode": "shadow_only",
    }
