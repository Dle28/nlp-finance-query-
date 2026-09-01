"""Canonical VND-scale validation shared by grounded E2E executors.

Only unit labels that denote a fixed VND scale are accepted.  This prevents a
binding from silently changing a source multiplier or an output divisor while
still allowing an exact, source-anchored conversion such as triệu đồng to tỷ
đồng.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class CurrencyUnitContractError(ValueError):
    """A source or output unit is not an exact VND-scale contract."""


CURRENCY_SCALE_TO_VND: dict[str, Decimal] = {
    "vnd": Decimal("1"),
    "thousand_vnd": Decimal("1000"),
    "nghin_dong": Decimal("1000"),
    "million_vnd": Decimal("1000000"),
    "trieu_dong": Decimal("1000000"),
    "billion_vnd": Decimal("1000000000"),
    "ty_dong": Decimal("1000000000"),
    "tram_ty_dong": Decimal("100000000000"),
    "trillion_vnd": Decimal("1000000000000"),
    "nghin_ty_dong": Decimal("1000000000000"),
}


def is_fixed_vnd_scale(unit: object) -> bool:
    return isinstance(unit, str) and unit in CURRENCY_SCALE_TO_VND


def vnd_scale(unit: object) -> Decimal:
    if not is_fixed_vnd_scale(unit):
        raise CurrencyUnitContractError(f"unsupported fixed VND scale: {unit!r}")
    return CURRENCY_SCALE_TO_VND[str(unit)]


def validate_source_multiplier(*, source_unit: object, multiplier: object) -> Decimal:
    """Require the bound multiplier to exactly match the canonical source scale."""
    expected = vnd_scale(source_unit)
    try:
        observed = Decimal(str(multiplier))
    except (InvalidOperation, ValueError) as error:
        raise CurrencyUnitContractError("source multiplier is not a Decimal") from error
    if observed != expected:
        raise CurrencyUnitContractError("source multiplier does not match source unit")
    return observed


def validate_output_divisor(request: Mapping[str, Any] | None) -> Decimal:
    """Require a currency request to use the canonical VND-to-output divisor."""
    if not isinstance(request, Mapping) or request.get("kind") != "currency":
        raise CurrencyUnitContractError("requested output is not a currency scale")
    expected = vnd_scale(request.get("unit"))
    try:
        observed = Decimal(str(request.get("vnd_to_output_divisor")))
    except (InvalidOperation, ValueError) as error:
        raise CurrencyUnitContractError("output divisor is not a Decimal") from error
    if observed != expected:
        raise CurrencyUnitContractError("output divisor does not match requested unit")
    return observed
