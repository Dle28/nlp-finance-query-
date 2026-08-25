from decimal import Decimal

import pytest

from finance_query.e2e.decimal_sandbox import (
    DecimalSandboxPolicy,
    SandboxViolation,
    execute_decimal_ast,
)


def test_decimal_sandbox_executes_only_allowlisted_ast() -> None:
    result = execute_decimal_ast(
        {
            "op": "ratio_to_percent",
            "args": [{"op": "divide", "args": ["income", "revenue"]}],
        },
        {"income": Decimal("5"), "revenue": Decimal("20")},
    )
    assert result.value == Decimal("25")
    assert result.operation_count == 2
    assert result.max_depth_seen == 3
    assert len(result.policy_sha256) == 64


def test_decimal_sandbox_rejects_hidden_or_oversized_programs() -> None:
    with pytest.raises(SandboxViolation, match="NODE_INVALID"):
        execute_decimal_ast(
            {"op": "lookup", "args": ["value"], "python": "open('/tmp/x')"},
            {"value": Decimal("1")},
        )
    with pytest.raises(SandboxViolation, match="NODE_LIMIT"):
        execute_decimal_ast(
            {"op": "abs", "args": [{"op": "abs", "args": ["value"]}]},
            {"value": Decimal("1")},
            policy=DecimalSandboxPolicy(max_ast_nodes=1),
        )


def test_decimal_sandbox_bounds_policy_and_unit_arithmetic() -> None:
    result = execute_decimal_ast(
        {"op": "multiply", "args": ["raw", "scale"]},
        {"raw": Decimal("1234"), "scale": Decimal("1000")},
    )
    assert result.value == Decimal("1234000")
    with pytest.raises(ValueError, match="positive integer"):
        DecimalSandboxPolicy(max_wall_time_ms=0)
    with pytest.raises(SandboxViolation, match="DIGIT_LIMIT"):
        execute_decimal_ast(
            {"op": "lookup", "args": ["value"]},
            {"value": Decimal("123456")},
            policy=DecimalSandboxPolicy(max_decimal_digits=5),
        )
