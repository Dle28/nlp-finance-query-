import unittest

from finance_query.llm_stage_execution import (
    execute_gross_profit_margin_change_rank,
    execute_reviewed_stage,
)


def review(metric_values: dict[str, dict[str, str]]) -> dict:
    return {
        "annotation_status": "machine_provisional",
        "selected_bindings": [
            {
                "company": company,
                "variable_id": variable,
                "raw_value": value,
                "unit_labels": ["VND"],
            }
            for company, variables in metric_values.items()
            for variable, value in variables.items()
        ],
    }


class LlmStageExecutionTests(unittest.TestCase):
    def test_quick_ratio_screen_computes_median_and_strictly_lower_entities(self):
        stage = {
            "stage_id": "quick_ratio_screen",
            "metric_id": "quick_ratio",
            "entities": ["HPG", "HSG", "MSR", "NKG"],
            "required_variables": ["current_assets", "inventory", "current_liabilities"],
        }
        result = execute_reviewed_stage(
            stage,
            review(
                {
                    "HPG": {"current_assets": "100", "inventory": "20", "current_liabilities": "40"},
                    "HSG": {"current_assets": "80", "inventory": "20", "current_liabilities": "40"},
                    "MSR": {"current_assets": "70", "inventory": "20", "current_liabilities": "25"},
                    "NKG": {"current_assets": "90", "inventory": "30", "current_liabilities": "60"},
                }
            ),
        )
        self.assertEqual(result["status"], "stage_complete")
        self.assertEqual(result["aggregate_value"], "1.75")
        self.assertEqual(result["eligible_entities"], ["HSG", "NKG"])

    def test_net_profit_margin_average_is_deterministic_not_llm_written(self):
        stage = {
            "stage_id": "net_profit_margin_after_screen",
            "metric_id": "net_profit_margin",
            "required_variables": ["net_income", "net_revenue"],
            "aggregate": "average",
        }
        result = execute_reviewed_stage(
            stage,
            review(
                {
                    "HSG": {"net_income": "10", "net_revenue": "100"},
                    "NKG": {"net_income": "20", "net_revenue": "100"},
                }
            ),
        )
        self.assertEqual(result["status"], "stage_complete")
        self.assertEqual(result["aggregate_value"], "15.0")
        self.assertEqual(result["result_unit"], "percent")
        self.assertTrue(result["final_stage"])

    def test_stage_blocks_mixed_units_within_one_company(self):
        stage = {
            "stage_id": "quick_ratio_screen",
            "metric_id": "quick_ratio",
            "entities": ["HPG"],
            "required_variables": ["current_assets", "inventory", "current_liabilities"],
        }
        source = review(
            {"HPG": {"current_assets": "100", "inventory": "20", "current_liabilities": "40"}}
        )
        source["selected_bindings"][1]["unit_labels"] = ["Nghìn VND"]
        result = execute_reviewed_stage(stage, source)
        self.assertEqual(result["status"], "stage_blocked")
        self.assertEqual(result["reason_codes"], ["deterministic_stage_execution_failed"])

    def test_gross_margin_change_selects_only_unique_signed_increase(self):
        stage = {
            "stage_id": "gross_profit_margin_change_rank",
            "state_inputs": {"old": "gross_profit_margin_old", "new": "gross_profit_margin_new"},
        }
        state = {
            "gross_profit_margin_old": {
                "status": "stage_complete",
                "metric_values": {"HSG": "10", "MSR": "20"},
            },
            "gross_profit_margin_new": {
                "status": "stage_complete",
                "metric_values": {"HSG": "15", "MSR": "21"},
            },
        }
        result = execute_gross_profit_margin_change_rank(stage, state)
        self.assertEqual(result["status"], "stage_complete")
        self.assertEqual(result["winning_entity"], "HSG")
        self.assertEqual(result["aggregate_value"], "5")


if __name__ == "__main__":
    unittest.main()
