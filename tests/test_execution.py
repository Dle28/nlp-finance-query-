from __future__ import annotations

import unittest
from decimal import Decimal

from finance_query.execution import (
    convert_unit,
    execute_ast,
    execute_grounded_ast_shadow,
    parse_decimal,
    validate_operation_ast,
)


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

    @staticmethod
    def binding(value: str, *, unit: str = "million_vnd", scope: str = "consolidated") -> dict:
        return {
            "internal_table_uid": "table-1",
            "ticker": "AAA",
            "report_year": 2023,
            "scope": scope,
            "source_unit": unit,
            "binding": {
                "status": "cell_bound",
                "row_index": 1,
                "column_index": 2,
                "raw_value": value,
                "parsed_value": value,
                "parse_warnings": [],
                "column_label": "2023",
                "source_cell": {"row": 1, "column": 2},
            },
        }

    @staticmethod
    def sources(value: str = "120", *, unit: str = "million_vnd", scope: str = "consolidated") -> tuple[dict, dict]:
        table = {
            "internal_table_uid": "table-1",
            "ticker": "AAA",
            "report_year": 2023,
            "scope": scope,
            "unit_hint": unit,
            "rows": [["header"], ["metric", "note", value]],
            "cell_provenance": [[], [None, None, {"row": 1, "column": 2}]],
        }
        context = {
            "internal_table_uid": "table-1",
            "canonical_headers": {"columns": [{"column_index": 2, "source_label": "2023"}]},
            "row_profiles": [{"row_index": 1, "role": "data", "numeric_columns": [2]}],
        }
        return {"table-1": table}, {"table-1": context}

    def test_grounded_divide_executes_in_shadow_only(self) -> None:
        tables, contexts = self.sources("120")
        tables["table-2"] = {**tables["table-1"], "internal_table_uid": "table-2", "rows": [["header"], ["metric", "note", "100"]]}
        tables["table-2"]["cell_provenance"] = [[], [None, None, {"row": 1, "column": 2}]]
        contexts["table-2"] = {**contexts["table-1"], "internal_table_uid": "table-2"}
        denominator = self.binding("100")
        denominator["internal_table_uid"] = "table-2"
        result = execute_grounded_ast_shadow(
            {"op": "divide", "args": ["numerator", "denominator"]},
            {"numerator": self.binding("120"), "denominator": denominator},
            source_tables=tables,
            source_contexts=contexts,
        )
        self.assertEqual(result["status"], "shadow_complete")
        self.assertEqual(result["result_value"], "1.2")
        self.assertEqual(result["output_unit"], "times")
        self.assertFalse(result["submission_eligible"])

    def test_grounded_execution_blocks_unit_and_scope_mismatch(self) -> None:
        a = self.binding("120")
        b = self.binding("100", unit="vnd", scope="separate")
        tables, contexts = self.sources("120")
        tables["table-2"] = {
            **tables["table-1"],
            "internal_table_uid": "table-2",
            "scope": "separate",
            "unit_hint": "vnd",
            "rows": [["header"], ["metric", "note", "100"]],
        }
        contexts["table-2"] = {**contexts["table-1"], "internal_table_uid": "table-2"}
        b["internal_table_uid"] = "table-2"
        result = execute_grounded_ast_shadow(
            {"op": "subtract", "args": ["a", "b"]},
            {"a": a, "b": b},
            source_tables=tables,
            source_contexts=contexts,
        )
        self.assertEqual(result["status"], "shadow_blocked")
        self.assertIn("unit_mismatch", result["reason_codes"])
        self.assertIn("scope_mismatch", result["reason_codes"])

    def test_grounded_execution_blocks_missing_cell_metadata(self) -> None:
        bad = self.binding("10")
        del bad["binding"]["column_index"]
        tables, contexts = self.sources("10")
        result = execute_grounded_ast_shadow(
            {"op": "lookup", "args": ["x"]},
            {"x": bad},
            source_tables=tables,
            source_contexts=contexts,
        )
        self.assertEqual(result["status"], "shadow_blocked")
        self.assertIn("x:invalid_column_index", result["reason_codes"])

    def test_grounded_execution_blocks_zero_denominator(self) -> None:
        tables, contexts = self.sources("10")
        tables["table-2"] = {**tables["table-1"], "internal_table_uid": "table-2", "rows": [["header"], ["metric", "note", "0"]]}
        contexts["table-2"] = {**contexts["table-1"], "internal_table_uid": "table-2"}
        zero = self.binding("0")
        zero["internal_table_uid"] = "table-2"
        result = execute_grounded_ast_shadow(
            {"op": "divide", "args": ["a", "b"]},
            {"a": self.binding("10"), "b": zero},
            source_tables=tables,
            source_contexts=contexts,
        )
        self.assertIn("execution_error:ZeroDivisionError", result["reason_codes"])

    def test_grounded_list_mean(self) -> None:
        first = self.binding("10")
        second = self.binding("20")
        second["internal_table_uid"] = "table-2"
        tables, contexts = self.sources("10")
        tables["table-2"] = {**tables["table-1"], "internal_table_uid": "table-2", "rows": [["header"], ["metric", "note", "20"]]}
        contexts["table-2"] = {**contexts["table-1"], "internal_table_uid": "table-2"}
        result = execute_grounded_ast_shadow(
            {"op": "mean", "args": ["values"]},
            {"values": [first, second]},
            source_tables=tables,
            source_contexts=contexts,
        )
        self.assertEqual(result["result_value"], "15")

    def test_grounded_execution_rejects_unrecorded_parser_warning(self) -> None:
        binding = self.binding("1.2")
        tables, contexts = self.sources("1.2")
        result = execute_grounded_ast_shadow(
            {"op": "lookup", "args": ["x"]},
            {"x": binding},
            source_tables=tables,
            source_contexts=contexts,
        )
        self.assertIn("x:unreliable_numeric_parse", result["reason_codes"])
        self.assertIn("x:parse_warnings_not_reproducible", result["reason_codes"])

    def test_grounded_execution_rejects_fabricated_raw_cell(self) -> None:
        tables, contexts = self.sources("99")
        result = execute_grounded_ast_shadow(
            {"op": "lookup", "args": ["x"]},
            {"x": self.binding("10")},
            source_tables=tables,
            source_contexts=contexts,
        )
        self.assertIn("x:raw_value_differs_from_v2", result["reason_codes"])
        self.assertEqual(result["exact_binding_count"], 0)

    def test_unknown_and_dimensional_operators_fail_closed(self) -> None:
        self.assertIn(
            "unsupported_operator:plan_required",
            validate_operation_ast({"op": "plan_required", "args": []}),
        )
        self.assertIn(
            "operator_not_shadow_eligible:multiply",
            validate_operation_ast({"op": "multiply", "args": ["a", "b"]}),
        )


if __name__ == "__main__":
    unittest.main()
