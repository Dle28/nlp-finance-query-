"""Resource-bounded interpreter for the declarative Decimal formula AST.

This module intentionally does not execute Python source, import modules,
spawn processes, read files, or access the network.  The accepted language is
the small formula registry AST only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, DivisionByZero, localcontext
import hashlib
import json
import time
from typing import Any, Mapping


DECIMAL_SANDBOX_PROTOCOL = "vifinqa_decimal_ast_sandbox_v1"


class SandboxViolation(ValueError):
    """Raised when an AST or value exceeds the fail-closed sandbox policy."""


@dataclass(frozen=True, slots=True)
class DecimalSandboxPolicy:
    max_ast_nodes: int = 64
    max_ast_depth: int = 16
    max_operands: int = 32
    max_decimal_digits: int = 160
    max_wall_time_ms: int = 250

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @property
    def policy_sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SandboxedDecimalResult:
    value: Decimal
    ast_sha256: str
    policy_sha256: str
    operation_count: int
    max_depth_seen: int
    elapsed_ns: int

    def telemetry(self) -> dict[str, Any]:
        return {
            "protocol": DECIMAL_SANDBOX_PROTOCOL,
            "ast_sha256": self.ast_sha256,
            "policy_sha256": self.policy_sha256,
            "operation_count": self.operation_count,
            "max_depth_seen": self.max_depth_seen,
            "elapsed_ns": self.elapsed_ns,
        }


def _decimal_digits(value: Decimal) -> int:
    return len(value.as_tuple().digits)


def execute_decimal_ast(
    ast: Mapping[str, Any],
    operands: Mapping[str, Decimal],
    *,
    policy: DecimalSandboxPolicy | None = None,
) -> SandboxedDecimalResult:
    """Execute only the allow-listed AST under explicit resource budgets."""

    active_policy = policy or DecimalSandboxPolicy()
    if len(operands) > active_policy.max_operands:
        raise SandboxViolation("SANDBOX_OPERAND_LIMIT_EXCEEDED")
    normalized_operands: dict[str, Decimal] = {}
    for name, value in operands.items():
        if not isinstance(name, str) or not name:
            raise SandboxViolation("SANDBOX_OPERAND_NAME_INVALID")
        if not isinstance(value, Decimal) or not value.is_finite():
            raise SandboxViolation("SANDBOX_OPERAND_DECIMAL_INVALID")
        if _decimal_digits(value) > active_policy.max_decimal_digits:
            raise SandboxViolation("SANDBOX_OPERAND_DIGIT_LIMIT_EXCEEDED")
        normalized_operands[name] = value

    try:
        ast_payload = json.dumps(ast, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SandboxViolation("SANDBOX_AST_NOT_JSON") from exc
    ast_sha256 = hashlib.sha256(ast_payload.encode("utf-8")).hexdigest()
    started_ns = time.monotonic_ns()
    deadline_ns = started_ns + active_policy.max_wall_time_ms * 1_000_000
    operation_count = 0
    max_depth_seen = 0

    def visit(node: Any, depth: int) -> Decimal:
        nonlocal operation_count, max_depth_seen
        if time.monotonic_ns() > deadline_ns:
            raise SandboxViolation("SANDBOX_WALL_TIME_EXCEEDED")
        if depth > active_policy.max_ast_depth:
            raise SandboxViolation("SANDBOX_AST_DEPTH_EXCEEDED")
        max_depth_seen = max(max_depth_seen, depth)
        if isinstance(node, str):
            if node not in normalized_operands:
                raise KeyError(f"missing operand {node}")
            return normalized_operands[node]
        if not isinstance(node, Mapping) or set(node) != {"op", "args"}:
            raise SandboxViolation("SANDBOX_AST_NODE_INVALID")
        operation_count += 1
        if operation_count > active_policy.max_ast_nodes:
            raise SandboxViolation("SANDBOX_AST_NODE_LIMIT_EXCEEDED")
        op = node.get("op")
        args = node.get("args")
        if not isinstance(op, str) or not isinstance(args, list):
            raise SandboxViolation("SANDBOX_AST_NODE_INVALID")
        values = [visit(argument, depth + 1) for argument in args]
        if op == "lookup" and len(values) == 1:
            result = values[0]
        elif op == "add" and values:
            result = sum(values, Decimal("0"))
        elif op == "subtract" and len(values) == 2:
            result = values[0] - values[1]
        elif op == "divide" and len(values) == 2:
            if values[1] == 0:
                raise DivisionByZero("zero denominator")
            result = values[0] / values[1]
        elif op == "multiply" and len(values) == 2:
            result = values[0] * values[1]
        elif op == "abs" and len(values) == 1:
            result = abs(values[0])
        elif op == "ratio_to_percent" and len(values) == 1:
            result = values[0] * Decimal("100")
        else:
            raise SandboxViolation(f"SANDBOX_OPERATION_NOT_ALLOWED:{op}")
        if not result.is_finite() or _decimal_digits(result) > active_policy.max_decimal_digits:
            raise SandboxViolation("SANDBOX_RESULT_DIGIT_LIMIT_EXCEEDED")
        return result

    with localcontext():
        value = visit(ast, 1)
    elapsed_ns = time.monotonic_ns() - started_ns
    if elapsed_ns > active_policy.max_wall_time_ms * 1_000_000:
        raise SandboxViolation("SANDBOX_WALL_TIME_EXCEEDED")
    return SandboxedDecimalResult(
        value=value,
        ast_sha256=ast_sha256,
        policy_sha256=active_policy.policy_sha256,
        operation_count=operation_count,
        max_depth_seen=max_depth_seen,
        elapsed_ns=elapsed_ns,
    )
