from decimal import Decimal
import pytest
from finance_query.grounded_execution import execute_formula

def test_ratio_canaries_are_decimal_and_fail_closed() -> None:
    assert execute_formula({"op":"divide","args":[{"op":"subtract","args":["assets","inventory"]},"liabilities"]},{"assets":Decimal("100"),"inventory":Decimal("20"),"liabilities":Decimal("40")}) == Decimal("2")
    assert execute_formula({"op":"ratio_to_percent","args":[{"op":"divide","args":["income","revenue"]}]},{"income":Decimal("5"),"revenue":Decimal("20")}) == Decimal("25")
    with pytest.raises(Exception): execute_formula({"op":"divide","args":["a","b"]},{"a":Decimal("1"),"b":Decimal("0")})
