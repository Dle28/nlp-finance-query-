from __future__ import annotations

import unittest

from finance_query.query_fingerprint import build_query_fingerprint


class QueryFingerprintTests(unittest.TestCase):
    def test_fingerprint_ignores_operand_name_but_keeps_shape(self) -> None:
        left = {
            "id": 1,
            "question_plan": {
                "family": "direct_lookup",
                "tickers": ["AAA"],
                "years": [2023],
                "scope": "separate",
                "requested_unit": "million_vnd",
                "operands": [{"operand_id": "x0"}],
                "operation_ast": {"op": "lookup", "args": ["x0"]},
                "warnings": [],
            },
        }
        right = {**left, "id": 2, "question_plan": {**left["question_plan"], "operation_ast": {"op": "lookup", "args": ["revenue"]}}}
        self.assertEqual(build_query_fingerprint(left)["fingerprint"], build_query_fingerprint(right)["fingerprint"])
        self.assertEqual(build_query_fingerprint(left)["route"], "operator_contract_candidate")

    def test_complex_plan_routes_to_decomposition(self) -> None:
        item = {
            "id": 7,
            "question_plan": {
                "family": "ratio_or_derived",
                "tickers": ["AAA"],
                "years": [2023],
                "operands": [],
                "operation_ast": {"op": "divide", "args": ["a", "b"]},
                "warnings": ["Complex operand decomposition requires the semantic planner or manual gold plan."],
            },
        }
        self.assertEqual(build_query_fingerprint(item)["route"], "requires_operand_decomposition")

    def test_unknown_program_abstains(self) -> None:
        item = {"id": 9, "question_plan": {"operation_ast": {"op": "plan_required", "args": []}}}
        self.assertEqual(build_query_fingerprint(item)["route"], "abstain_unknown_program")


if __name__ == "__main__":
    unittest.main()
