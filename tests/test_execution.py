from __future__ import annotations

import unittest
from decimal import Decimal

from finance_query.execution import convert_unit, execute_ast, parse_decimal


class ExecutionTests(unittest.TestCase):
    def test_vietnamese_number(self) -> None:
        parsed = parse_decimal("1.234.567,89")
        self.assertEqual(parsed.value, "1234567.89")

    def test_parenthesized_negative(self) -> None:
        parsed = parse_decimal("(1.234)")
        self.assertEqual(parsed.value, "-1234")

    def test_rejects_ocr_concatenated_parenthesized_numbers(self) -> None:
        parsed = parse_decimal("(72.193.585.614)(27.471.160.925)")
        self.assertIsNone(parsed.value)
        self.assertEqual(parsed.warnings, ["multiple_numeric_groups"])

    def test_rejects_fraction_or_adjacent_numeric_groups(self) -> None:
        parsed = parse_decimal("31/12/2023")
        self.assertIsNone(parsed.value)
        self.assertEqual(parsed.warnings, ["multiple_or_fractional_numeric_groups"])

    def test_percentage_change(self) -> None:
        result = execute_ast(
            {"op": "percentage_change", "args": ["new", "old"]},
            {"new": Decimal("120"), "old": Decimal("100")},
        )
        self.assertEqual(result, Decimal("20"))

    def test_unit_conversion(self) -> None:
        result = convert_unit(
            Decimal("2500"),
            "million_vnd",
            "billion_vnd",
        )
        self.assertEqual(result, Decimal("2.5"))


if __name__ == "__main__":
    unittest.main()
