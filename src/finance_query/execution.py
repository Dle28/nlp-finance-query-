from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation, getcontext
from statistics import median
from typing import Any, Mapping, Sequence

from .schemas import ParsedNumber


getcontext().prec = 38

NULL_MARKERS = {"", "-", "—", "–", "n/a", "na", "không có", "nil"}


def parse_decimal(raw_value: Any) -> ParsedNumber:
    raw = "" if raw_value is None else str(raw_value).strip()
    warnings: list[str] = []
    if raw.casefold() in NULL_MARKERS:
        return ParsedNumber(raw, None, None, 0.0, ["null_or_missing_value"])

    text = raw.replace("\u00a0", " ").strip()
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
