"""Evaluate the deterministic arithmetic kernel on frozen FinQA gold programs."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from finance_query.e2e.decimal_executor import OPERATOR_REGISTRY, execute_ast


PROTOCOL = "finqa_dsl_kernel_evaluation_v1"
STEP_RE = re.compile(r"([a-z_]+)\(([^()]*)\)(?:, |$)")
OP_MAP = {
    "add": "add",
    "subtract": "subtract",
    "multiply": "multiply",
    "divide": "divide",
    "table_average": "mean",
    "table_max": "max",
    "table_min": "min",
    "table_sum": "sum",
}


class UnsupportedProgram(ValueError):
    """The current arithmetic kernel has no equivalent operator."""


def _number(token: str) -> Decimal:
    value = token.strip().replace(",", "")
    if value.startswith("const_"):
        value = value.removeprefix("const_")
        if value == "m1":
            value = "-1"
    percent = value.endswith("%")
    if percent:
        value = value[:-1]
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid numeric token: {token}") from error
    return number / Decimal("100") if percent else number


def _table_numbers(table: list[list[Any]], row_label: str) -> list[Decimal]:
    rows = {str(row[0]).strip(): row[1:] for row in table if row}
    if row_label not in rows:
        raise ValueError(f"table row not found: {row_label}")
    numbers: list[Decimal] = []
    for raw in rows[row_label]:
        value = str(raw).replace("$", "").strip().split("(", 1)[0].strip()
        numbers.append(_number(value))
    if not numbers:
        raise ValueError("table aggregation row is empty")
    return numbers


def execute_finqa_program(program: str, table: list[list[Any]]) -> tuple[Any, list[str]]:
    results: list[Any] = []
    operators: list[str] = []
    matches = list(STEP_RE.finditer(program.strip()))
    if not matches or "".join(match.group(0) for match in matches).removesuffix(", ") != program.strip():
        raise ValueError(f"invalid FinQA program: {program}")
    for match in matches:
        source_op, raw_arguments = match.group(1), match.group(2)
        arguments_pair = raw_arguments.split(", ", 1)
        if len(arguments_pair) != 2:
            raise ValueError(f"invalid FinQA arguments: {raw_arguments}")
        left_token, right_token = arguments_pair
        operators.append(source_op)
        if source_op not in OP_MAP:
            raise UnsupportedProgram(source_op)
        target_op = OP_MAP[source_op]
        if source_op.startswith("table_"):
            arguments: list[Any] = [_table_numbers(table, left_token.strip())]
        else:
            arguments = []
            for token in (left_token, right_token):
                token = token.strip()
                if token.startswith("#"):
                    index = int(token[1:])
                    if index < 0 or index >= len(results):
                        raise ValueError(f"invalid result reference: {token}")
                    arguments.append(results[index])
                else:
                    arguments.append(_number(token))
        results.append(execute_ast({"op": target_op, "args": arguments}, {}))
    if not results:
        raise ValueError("empty FinQA program")
    return results[-1], operators


def _answer_matches(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) or isinstance(expected, str) and str(expected).casefold() in {"yes", "no"}:
        return str(actual).casefold() == str(expected).casefold()
    try:
        actual_number = Decimal(str(actual))
        expected_number = Decimal(str(expected))
    except InvalidOperation:
        return False
    return abs(actual_number - expected_number) <= Decimal("0.00001")


def evaluate_finqa_items(
    items: Iterable[Mapping[str, Any]],
    *,
    source_split: str,
    benchmark_stage: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    shadow_counts: Counter[str] = Counter()
    operator_counts: Counter[str] = Counter()
    for item in items:
        qa = item.get("qa") or {}
        program = str(qa.get("program") or "")
        source_id = str(item.get("id") or "")
        actual: Any = None
        reason = None
        operators = re.findall(r"([a-z_]+)\(", program)
        try:
            actual, operators = execute_finqa_program(program, list(item.get("table") or []))
            status = "PASS" if _answer_matches(actual, qa.get("exe_ans")) else "FAIL"
        except UnsupportedProgram as error:
            status = "UNSUPPORTED"
            reason = f"unsupported_operator:{error}"
        except (ValueError, TypeError, ZeroDivisionError, InvalidOperation) as error:
            status = "ERROR"
            reason = f"{type(error).__name__}:{error}"
        mapped = [OP_MAP.get(op) for op in operators]
        shadow_eligible = bool(mapped) and all(
            mapped_op in OPERATOR_REGISTRY and OPERATOR_REGISTRY[mapped_op].shadow_eligible
            for mapped_op in mapped
        )
        shadow_status = "ELIGIBLE" if shadow_eligible else "BLOCKED"
        operator_counts.update(operators)
        status_counts[status] += 1
        shadow_counts[shadow_status] += 1
        rows.append(
            {
                "protocol": PROTOCOL,
                "source_split": source_split,
                "benchmark_stage": benchmark_stage,
                "source_record_id": source_id,
                "execution_status": status,
                "shadow_status": shadow_status,
                "operators": operators,
                "expected_answer": qa.get("exe_ans"),
                "actual_answer": format(actual, "f") if isinstance(actual, Decimal) else actual,
                "reason": reason,
                "source_contract": {
                    "research_only": True,
                    "may_materialize_vifinqa_answer": False,
                    "submission_eligible": False,
                },
            }
        )
    total = len(rows)
    supported = status_counts["PASS"] + status_counts["FAIL"]
    report = {
        "protocol": PROTOCOL,
        "source_split": source_split,
        "benchmark_stage": benchmark_stage,
        "record_count": total,
        "status_counts": dict(sorted(status_counts.items())),
        "shadow_status_counts": dict(sorted(shadow_counts.items())),
        "operator_counts": dict(sorted(operator_counts.items())),
        "kernel_accuracy_on_supported": status_counts["PASS"] / supported if supported else None,
        "kernel_coverage": supported / total if total else None,
        "shadow_eligibility_rate": shadow_counts["ELIGIBLE"] / total if total else None,
        "untouched_evaluation_opened": benchmark_stage == "untouched_evaluation",
    }
    return rows, report


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)
