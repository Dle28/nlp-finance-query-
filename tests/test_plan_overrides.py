import unittest

from finance_query.plan_overrides import (
    apply_plan_overrides,
    reported_direct_override,
    source_ticker_direct_override,
    validate_plan_overrides,
)


class PlanOverrideTests(unittest.TestCase):
    def test_disclosed_row_override_is_hash_bound_and_effective(self):
        item = {
            "id": 92,
            "question": "Tổng cộng tài sản của SSH cuối năm 2025 là bao nhiêu nghìn tỷ đồng?",
            "question_plan": {
                "family": "multi_entity_or_period_aggregation",
                "years": [2025],
                "tickers": ["SSH"],
                "scope": None,
                "requested_unit": "trillion_vnd",
                "operation_ast": {"op": "count", "args": ["filtered_values"]},
                "operands": [],
            },
        }
        override = reported_direct_override(item)
        self.assertIsNotNone(override)
        validated = validate_plan_overrides([item], [override])
        effective = apply_plan_overrides([item], validated)[0]
        self.assertEqual(effective["question_plan"]["family"], "direct_lookup")
        self.assertEqual(effective["question_plan"]["operation_ast"]["op"], "lookup")
        self.assertEqual(effective["_question_plan_provenance"]["source"], "source_bound_plan_override_v1")

    def test_override_refuses_changed_question(self):
        item = {
            "id": 92,
            "question": "Tổng cộng tài sản của SSH cuối năm 2025 là bao nhiêu nghìn tỷ đồng?",
            "question_plan": {"family": "multi_entity_or_period_aggregation"},
        }
        override = reported_direct_override(item)
        changed = {**item, "question": "Tổng cộng tài sản của SSH cuối năm 2024 là bao nhiêu?"}
        with self.assertRaisesRegex(ValueError, "question hash"):
            validate_plan_overrides([changed], [override])

    def test_source_ticker_override_requires_one_exact_metadata_token(self):
        item = {
            "id": 7,
            "question": "Quỹ khen thưởng, phúc lợi của HT1 cuối năm 2019 là bao nhiêu?",
            "question_plan": {
                "family": "direct_lookup",
                "years": [2019],
                "tickers": [],
                "scope": None,
                "operation_ast": {"op": "lookup", "args": ["x0"]},
                "operands": [{"operand_id": "x0", "metric": "Quỹ khen thưởng"}],
            },
        }
        override = source_ticker_direct_override(item, ["HT1", "PC1"])
        self.assertIsNotNone(override)
        assert override is not None
        self.assertEqual(override["effective_question_plan"]["tickers"], ["HT1"])
        self.assertEqual(override["effective_question_plan"]["operands"][0]["ticker"], "HT1")
        self.assertIn("exact_query_ticker_token", override["reason_code"])
        self.assertIsNone(
            source_ticker_direct_override(
                {**item, "question": "HT1 và PC1 có giá trị bao nhiêu?"},
                ["HT1", "PC1"],
            )
        )
