from decimal import Decimal

import pytest

from finance_query.e2e.decimal_sandbox import (
    DecimalSandboxPolicy,
    SandboxViolation,
    execute_decimal_ast,
    execute_unique_period_extreme,
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


def test_decimal_sandbox_calculates_a_multi_entity_mean() -> None:
    result = execute_decimal_ast(
        {"op": "mean", "args": ["a", "b", "c"]},
        {"a": Decimal("1000000000"), "b": Decimal("2000000000"), "c": Decimal("3000000000")},
    )
    assert result.value == Decimal("2000000000")


def test_decimal_sandbox_selects_a_unique_extreme_period_and_rejects_ties() -> None:
    result = execute_unique_period_extreme(
        stage_order=["y2020", "y2021", "y2023"],
        stage_values={"y2020": Decimal("10"), "y2021": Decimal("30"), "y2023": Decimal("20")},
        stage_periods={"y2020": 2020, "y2021": 2021, "y2023": 2023},
        direction="max",
    )
    assert result.value == Decimal("2021")
    with pytest.raises(SandboxViolation, match="TIE"):
        execute_unique_period_extreme(
            stage_order=["y2020", "y2021"],
            stage_values={"y2020": Decimal("10"), "y2021": Decimal("10")},
            stage_periods={"y2020": 2020, "y2021": 2021},
            direction="max",
        )


def test_decimal_sandbox_typed_scalar_multiply_is_fail_closed() -> None:
    scalar = {"kind": "dimensionless_scalar", "source": "percent_literal", "token": "7.5%"}
    result = execute_decimal_ast(
        {"op": "scalar_multiply", "args": ["raw", scalar]},
        {"raw": Decimal("200")},
    )
    assert result.value == Decimal("15.000")
    with pytest.raises(SandboxViolation, match="SCALAR_REQUIRED"):
        execute_decimal_ast(
            {"op": "scalar_multiply", "args": ["raw", "scale"]},
            {"raw": Decimal("200"), "scale": Decimal("0.075")},
        )
    with pytest.raises(SandboxViolation, match="SCALAR_INVALID"):
        execute_decimal_ast(
            {
                "op": "scalar_multiply",
                "args": [
                    "raw",
                    {"kind": "dimensionless_scalar", "source": "percent_literal", "token": "bad%"},
                ],
            },
            {"raw": Decimal("200")},
        )
