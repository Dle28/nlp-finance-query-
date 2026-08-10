from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext
from statistics import median
from typing import Any, Mapping, Sequence

from .corpus import infer_unit
from .schemas import ParsedNumber


getcontext().prec = 38

NULL_MARKERS = {"", "-", "—", "–", "n/a", "na", "không có", "nil"}


@dataclass(frozen=True, slots=True)
class OperatorContract:
    """Typed contract above deterministic arithmetic.

    ``execute_ast`` remains the math kernel.  This contract states whether an
    operator may be used in grounded shadow execution and which dimensional
    checks must hold before the kernel receives any values.
    """

    name: str
    min_args: int
    max_args: int | None
    unit_policy: str
    output_kind: str
    shadow_eligible: bool = True


OPERATOR_REGISTRY: dict[str, OperatorContract] = {
    "lookup": OperatorContract("lookup", 1, 1, "single", "source_unit"),
    "add": OperatorContract("add", 1, None, "same", "source_unit"),
    "sum": OperatorContract("sum", 1, None, "same", "source_unit"),
    "subtract": OperatorContract("subtract", 2, 2, "same", "source_unit"),
    "absolute_difference": OperatorContract("absolute_difference", 2, 2, "same", "source_unit"),
    # Multiplication needs dimensional algebra which is intentionally not
    # inferred from report labels in v1.
    "multiply": OperatorContract("multiply", 1, None, "dimensional", "derived", False),
    "divide": OperatorContract("divide", 2, 2, "same", "ratio"),
    "percentage_change": OperatorContract("percentage_change", 2, 2, "same", "percent"),
    "mean": OperatorContract("mean", 1, None, "same", "source_unit"),
    "median": OperatorContract("median", 1, None, "same", "source_unit"),
    "min": OperatorContract("min", 1, None, "same", "source_unit"),
    "max": OperatorContract("max", 1, None, "same", "source_unit"),
    "count": OperatorContract("count", 1, None, "ignore", "count"),
    # CAGR includes a literal period count; a typed literal contract is a v2
    # concern, so it remains fail-closed in grounded shadow v1.
    "cagr": OperatorContract("cagr", 3, 3, "same", "percent", False),
}

GROUNDED_OPERATOR_PROTOCOL = "grounded_operator_shadow_v1"
RELIABLE_BINDING_WARNINGS = {"percent_value_not_scaled"}


def parse_decimal(raw_value: Any) -> ParsedNumber:
    raw = "" if raw_value is None else str(raw_value).strip()
    warnings: list[str] = []
    if raw.casefold() in NULL_MARKERS:
        return ParsedNumber(raw, None, None, 0.0, ["null_or_missing_value"])

    text = raw.replace("\u00a0", " ").strip()
    # OCR/table extraction can concatenate two adjacent source cells, for
    # example ``(72.193)(27.471)``. Removing punctuation would turn that into
    # one plausible-looking but fabricated number, so reject it before any
    # normalization. The raw value remains available for audit.
    parenthetical_numeric_groups = [
        group
        for group in re.findall(r"\(([^()]*)\)", text)
        if any(character.isdigit() for character in group)
    ]
    if len(parenthetical_numeric_groups) > 1:
        return ParsedNumber(raw, None, None, 0.0, ["multiple_numeric_groups"])
    if re.search(r"\d\s*[/;|]\s*\d", text):
        return ParsedNumber(raw, None, None, 0.0, ["multiple_or_fractional_numeric_groups"])
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()

    percent = "%" in text
    text = re.sub(r"[^0-9,\.\-+ ]", "", text)
    text = text.replace(" ", "")

    if not text:
        return ParsedNumber(raw, None, None, 0.0, ["no_numeric_content"])

    # Infer locale by the rightmost separator. Vietnamese financial statements
    # commonly use dots for thousands and commas for decimals.
    comma = text.rfind(",")
    dot = text.rfind(".")
    normalized = text

    if comma >= 0 and dot >= 0:
        if comma > dot:
            normalized = text.replace(".", "").replace(",", ".")
        else:
            normalized = text.replace(",", "")
            warnings.append("interpreted_as_english_number_format")
    elif comma >= 0:
        decimals = len(text) - comma - 1
        if decimals in {1, 2}:
            normalized = text.replace(",", ".")
        else:
            normalized = text.replace(",", "")
    elif dot >= 0:
        decimals = len(text) - dot - 1
        if decimals == 3 and text.count(".") >= 1:
            normalized = text.replace(".", "")
        elif text.count(".") > 1:
            normalized = text.replace(".", "")
        else:
            warnings.append("single_dot_is_ambiguous")

    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return ParsedNumber(raw, normalized, None, 0.0, ["invalid_decimal"])

    if negative:
        value = -value
    if percent:
        warnings.append("percent_value_not_scaled")

    confidence = 1.0
    if warnings:
        confidence = 0.75
    return ParsedNumber(raw, normalized, format(value, "f"), confidence, warnings)


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, ParsedNumber):
        if value.value is None:
            raise ValueError(f"Cannot execute with missing number: {value.raw!r}")
        return Decimal(value.value)
    if isinstance(value, bool):
        raise TypeError("Boolean values are not valid numeric operands.")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise TypeError(f"Cannot convert {value!r} to Decimal") from exc


def _resolve(argument: Any, values: Mapping[str, Any]) -> Any:
    if isinstance(argument, dict) and "op" in argument:
        return execute_ast(argument, values)
    if isinstance(argument, str) and argument in values:
        return values[argument]
    if isinstance(argument, list):
        return [_resolve(item, values) for item in argument]
    return argument


def execute_ast(ast: Mapping[str, Any], values: Mapping[str, Any]) -> Any:
    op = str(ast.get("op", ""))
    args = [_resolve(argument, values) for argument in ast.get("args", [])]

    if op == "lookup":
        if len(args) != 1:
            raise ValueError("lookup requires one argument")
        return _as_decimal(args[0])
    if op == "add":
        return sum((_as_decimal(value) for value in args), Decimal("0"))
    if op == "sum":
        sequence = args[0] if len(args) == 1 and isinstance(args[0], Sequence) else args
        return sum((_as_decimal(value) for value in sequence), Decimal("0"))
    if op == "subtract":
        if len(args) != 2:
            raise ValueError("subtract requires two arguments")
        return _as_decimal(args[0]) - _as_decimal(args[1])
    if op == "absolute_difference":
        if len(args) != 2:
            raise ValueError("absolute_difference requires two arguments")
        return abs(_as_decimal(args[0]) - _as_decimal(args[1]))
    if op == "multiply":
        result = Decimal("1")
        for value in args:
            result *= _as_decimal(value)
        return result
    if op == "divide":
        if len(args) != 2:
            raise ValueError("divide requires two arguments")
        denominator = _as_decimal(args[1])
        if denominator == 0:
            raise ZeroDivisionError("division by zero")
        return _as_decimal(args[0]) / denominator
    if op == "percentage_change":
        if len(args) != 2:
            raise ValueError("percentage_change requires new and old values")
        old = _as_decimal(args[1])
        if old == 0:
            raise ZeroDivisionError("percentage change from zero")
        return (_as_decimal(args[0]) - old) / abs(old) * Decimal("100")
    if op == "mean":
        sequence = args[0] if len(args) == 1 and isinstance(args[0], Sequence) else args
        numbers = [_as_decimal(value) for value in sequence]
        if not numbers:
            raise ValueError("mean requires at least one value")
        return sum(numbers, Decimal("0")) / Decimal(len(numbers))
    if op == "median":
        sequence = args[0] if len(args) == 1 and isinstance(args[0], Sequence) else args
        numbers = sorted(_as_decimal(value) for value in sequence)
        if not numbers:
            raise ValueError("median requires at least one value")
        return median(numbers)
    if op == "min":
        sequence = args[0] if len(args) == 1 and isinstance(args[0], Sequence) else args
        return min(_as_decimal(value) for value in sequence)
    if op == "max":
        sequence = args[0] if len(args) == 1 and isinstance(args[0], Sequence) else args
        return max(_as_decimal(value) for value in sequence)
    if op == "count":
        sequence = args[0] if len(args) == 1 and isinstance(args[0], Sequence) else args
        return Decimal(len(sequence))
    if op == "cagr":
        if len(args) != 3:
            raise ValueError("cagr requires end, start, and periods")
        end = float(_as_decimal(args[0]))
        start = float(_as_decimal(args[1]))
        periods = int(_as_decimal(args[2]))
        if start <= 0 or end < 0 or periods <= 0:
            raise ValueError("cagr requires start > 0, end >= 0, periods > 0")
        return Decimal(str((math.pow(end / start, 1.0 / periods) - 1.0) * 100.0))

    raise ValueError(f"Unsupported operation: {op!r}")


def validate_operation_ast(ast: Mapping[str, Any]) -> list[str]:
    """Return deterministic contract violations for an operation AST."""
    errors: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping) and "op" in node:
            op = str(node.get("op") or "")
            contract = OPERATOR_REGISTRY.get(op)
            if contract is None:
                errors.append(f"unsupported_operator:{op or 'missing'}")
                return
            args = node.get("args")
            if not isinstance(args, list):
                errors.append(f"invalid_args_container:{op}")
                return
            if len(args) < contract.min_args or (
                contract.max_args is not None and len(args) > contract.max_args
            ):
                errors.append(f"invalid_arity:{op}")
            if not contract.shadow_eligible:
                errors.append(f"operator_not_shadow_eligible:{op}")
            for argument in args:
                visit(argument)
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, str):
            return
        errors.append("ungrounded_literal_in_ast")

    visit(ast)
    return sorted(set(errors))


def _referenced_inputs(ast: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping) and "op" in node:
            for argument in node.get("args") or []:
                visit(argument)
        elif isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, str):
            names.add(node)

    visit(ast)
    return names


def _validate_grounded_value(value: Any) -> tuple[Any, list[dict[str, Any]], list[str]]:
    if isinstance(value, list):
        if not value:
            return [], [], ["empty_grounded_input_list"]
        resolved: list[Decimal] = []
        bindings: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in value:
            number, item_bindings, item_errors = _validate_grounded_value(item)
            if isinstance(number, Decimal):
                resolved.append(number)
            bindings.extend(item_bindings)
            errors.extend(item_errors)
        return resolved, bindings, errors
    if not isinstance(value, Mapping):
        return None, [], ["input_is_not_exact_binding"]

    record = dict(value)
    binding = dict(record.get("binding") or {})
    errors: list[str] = []
    for field in ("internal_table_uid", "ticker", "scope", "source_unit"):
        if not str(record.get(field) or "").strip():
            errors.append(f"missing_{field}")
    report_year = record.get("report_year")
    if isinstance(report_year, bool) or not isinstance(report_year, int):
        errors.append("missing_report_year")
    if binding.get("status") != "cell_bound":
        errors.append("binding_not_cell_bound")
    for field in ("row_index", "column_index"):
        coordinate = binding.get(field)
        if isinstance(coordinate, bool) or not isinstance(coordinate, int) or coordinate < 0:
            errors.append(f"invalid_{field}")
    parsed = parse_decimal(binding.get("raw_value", binding.get("value")))
    recorded_warnings = {str(item) for item in binding.get("parse_warnings") or []}
    actual_warnings = set(parsed.warnings)
    if actual_warnings - RELIABLE_BINDING_WARNINGS:
        errors.append("unreliable_numeric_parse")
    if recorded_warnings != actual_warnings:
        errors.append("parse_warnings_not_reproducible")
    expected = str(binding.get("parsed_value") or "")
    if parsed.value is None or not expected or parsed.value != expected:
        errors.append("parsed_value_not_reproducible")
    return (
        Decimal(expected) if not errors else None,
        [record],
        errors,
    )


def _table_unit(table: Mapping[str, Any]) -> str | None:
    unit = table.get("unit_hint")
    if isinstance(unit, str) and unit:
        return unit
    context = " ".join(
        [
            str(table.get("context_before") or ""),
            str((table.get("context_trace") or {}).get("source_title") or ""),
            " ".join((table.get("context_trace") or {}).get("unit_labels") or []),
        ]
    )
    rows = table.get("rows") or []
    return infer_unit(context, "\n".join(" | ".join(map(str, row)) for row in rows))


def _revalidate_binding_source(
    record: Mapping[str, Any],
    source_tables: Mapping[str, Mapping[str, Any]],
    source_contexts: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Prove one claimed binding against the current V2 cell and V3 header."""
    errors: list[str] = []
    uid = str(record.get("internal_table_uid") or "")
    table = source_tables.get(uid)
    context = source_contexts.get(uid)
    if table is None:
        errors.append("source_table_not_found")
    if context is None:
        errors.append("source_context_not_found")
    if table is None or context is None:
        return errors
    if str(table.get("internal_table_uid") or "") != uid:
        errors.append("source_table_uid_mismatch")
    if str(context.get("internal_table_uid") or "") != uid:
        errors.append("source_context_uid_mismatch")
    for field in ("ticker", "scope"):
        if str(table.get(field) or "") != str(record.get(field) or ""):
            errors.append(f"source_{field}_mismatch")
    try:
        table_year = int(table.get("report_year"))
    except (TypeError, ValueError):
        table_year = None
    if table_year != record.get("report_year"):
        errors.append("source_report_year_mismatch")
    if _table_unit(table) != str(record.get("source_unit") or ""):
        errors.append("source_unit_mismatch")

    binding = dict(record.get("binding") or {})
    row_index, column_index = binding.get("row_index"), binding.get("column_index")
    rows = table.get("rows") or []
    if (
        isinstance(row_index, bool)
        or not isinstance(row_index, int)
        or isinstance(column_index, bool)
        or not isinstance(column_index, int)
        or not 0 <= row_index < len(rows)
        or not 0 <= column_index < len(rows[row_index])
    ):
        errors.append("source_coordinates_invalid")
        return errors
    raw_value = str(rows[row_index][column_index])
    if raw_value != str(binding.get("raw_value", binding.get("value")) or ""):
        errors.append("raw_value_differs_from_v2")
    columns = (context.get("canonical_headers") or {}).get("columns") or []
    source_label = str(
        next(
            (
                column.get("source_label")
                for column in columns
                if isinstance(column.get("column_index"), int)
                and not isinstance(column.get("column_index"), bool)
                and column["column_index"] == column_index
            ),
            "",
        )
        or ""
    )
    if not source_label or source_label != str(binding.get("column_label") or ""):
        errors.append("column_label_differs_from_v3")
    profiles = context.get("row_profiles") or []
    profile = next((row for row in profiles if row.get("row_index") == row_index), {})
    if str(profile.get("role") or "") != "data" or column_index not in {
        int(value) for value in profile.get("numeric_columns") or []
    }:
        errors.append("cell_not_canonical_numeric_data")
    provenance_rows = table.get("cell_provenance") or []
    if (
        row_index >= len(provenance_rows)
        or column_index >= len(provenance_rows[row_index] or [])
        or provenance_rows[row_index][column_index] != binding.get("source_cell")
    ):
        errors.append("source_cell_provenance_mismatch")
    return errors


def execute_grounded_ast_shadow(
    ast: Mapping[str, Any],
    grounded_inputs: Mapping[str, Any],
    *,
    source_tables: Mapping[str, Mapping[str, Any]],
    source_contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Execute only exact, typed source bindings and never promote provenance."""
    errors = validate_operation_ast(ast)
    referenced = _referenced_inputs(ast)
    missing = sorted(referenced - set(grounded_inputs))
    errors.extend(f"missing_input:{name}" for name in missing)
    resolved: dict[str, Any] = {}
    all_bindings: list[dict[str, Any]] = []
    for name in sorted(referenced & set(grounded_inputs)):
        value, bindings, binding_errors = _validate_grounded_value(grounded_inputs[name])
        if not binding_errors:
            resolved[name] = value
        errors.extend(f"{name}:{error}" for error in binding_errors)
        for record in bindings:
            errors.extend(
                f"{name}:{error}"
                for error in _revalidate_binding_source(record, source_tables, source_contexts)
            )
        all_bindings.extend(bindings)

    scopes = {str(item.get("scope")) for item in all_bindings if item.get("scope")}
    if len(scopes) > 1:
        errors.append("scope_mismatch")
    root_op = str(ast.get("op") or "")
    contract = OPERATOR_REGISTRY.get(root_op)
    units = {str(item.get("source_unit")) for item in all_bindings if item.get("source_unit")}
    if contract and contract.unit_policy in {"same", "single"} and len(units) != 1:
        errors.append("unit_mismatch")

    result: Decimal | None = None
    if not errors:
        try:
            raw_result = execute_ast(ast, resolved)
            result = _as_decimal(raw_result)
        except (TypeError, ValueError, ZeroDivisionError, InvalidOperation) as error:
            errors.append(f"execution_error:{type(error).__name__}")

    output_unit = None
    if contract:
        output_unit = {
            "source_unit": next(iter(units), None),
            "ratio": "times",
            "percent": "percent",
            "count": "count",
        }.get(contract.output_kind)
    return {
        "protocol": GROUNDED_OPERATOR_PROTOCOL,
        "status": "shadow_complete" if not errors else "shadow_blocked",
        "operator": root_op,
        "result_value": format(result, "f") if result is not None else None,
        "output_unit": output_unit,
        "reason_codes": sorted(set(errors)),
        "exact_binding_count": len(all_bindings) if not errors else 0,
        "submission_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
    }


def convert_unit(value: Decimal, source_unit: str | None, target_unit: str | None) -> Decimal:
    if not source_unit or not target_unit or source_unit == target_unit:
        return value

    scale_to_vnd = {
        "vnd": Decimal("1"),
        "thousand_vnd": Decimal("1000"),
        "million_vnd": Decimal("1000000"),
        "billion_vnd": Decimal("1000000000"),
        "trillion_vnd": Decimal("1000000000000"),
    }
    if source_unit not in scale_to_vnd or target_unit not in scale_to_vnd:
        raise ValueError(f"Unsupported unit conversion: {source_unit} -> {target_unit}")
    return value * scale_to_vnd[source_unit] / scale_to_vnd[target_unit]
