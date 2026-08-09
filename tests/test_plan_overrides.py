import unittest

from finance_query.plan_overrides import (
    apply_plan_overrides,
    reported_direct_override,
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

