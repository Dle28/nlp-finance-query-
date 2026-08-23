from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import yaml

from finance_query.financial_taxonomy import FinancialTaxonomy
from finance_query.metric_registry import FinancialMetricRegistry


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "configs" / "vietnamese_financial_taxonomy_v1.yaml"
REGISTRY = ROOT / "configs" / "financial_metric_registry_v1.yaml"


def question_item(question: str, *, scope: str | None = "consolidated") -> dict[str, object]:
    return {
        "id": 101,
        "question": question,
        "question_plan": {
            "tickers": ["HPG"],
            "years": [2022],
            "scope": scope,
        },
    }


class FinancialMetricRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taxonomy = FinancialTaxonomy.load(TAXONOMY)
        cls.registry = FinancialMetricRegistry.load(REGISTRY, taxonomy=cls.taxonomy)

    def test_registry_loads_all_metrics(self):
        self.assertEqual(len(self.registry.metrics), 9)
        self.assertIn("quick_ratio", self.registry.by_id)

    def test_registry_rejects_unknown_concept(self):
        payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        candidate = deepcopy(payload)
        candidate["metrics"][0]["operands"][0]["concept_id"] = "unregistered_concept"
        with self.assertRaisesRegex(ValueError, "unknown concept"):
            FinancialMetricRegistry(candidate, taxonomy=self.taxonomy)

    def test_quick_ratio_route_has_exact_three_balance_sheet_operands(self):
        route = self.registry.build_question_route(
            question_item("Hệ số thanh toán nhanh của HPG năm 2022 là bao nhiêu?")
        )
        self.assertEqual(route["route_status"], "metric_candidate")
        stage = route["stages"][0]
        self.assertEqual(stage["metric_id"], "quick_ratio")
        self.assertEqual(
            [operand["role"] for operand in stage["required_operands"]],
            ["current_assets", "inventory", "current_liabilities"],
        )
        self.assertEqual(stage["retrieval_filters"]["table_types"], ["balance_sheet"])
        self.assertTrue(stage["formula_ast_metadata_only"])
        self.assertFalse(stage["source_contract"]["may_execute_formula"])

    def test_multi_metric_question_retains_ordered_stages(self):
        route = self.registry.build_question_route(
            question_item(
                "Hệ số thanh toán nhanh rồi biên lợi nhuận ròng của HPG năm 2022 là gì?"
            )
        )
        self.assertEqual(route["route_status"], "staged_candidate")
        self.assertEqual(route["reason_codes"], ["COMPOSED_EXECUTION_REQUIRED"])
        self.assertEqual(
            [stage["metric_id"] for stage in route["stages"]],
            ["quick_ratio", "net_profit_margin"],
        )
        self.assertEqual(
            [stage["stage_id"] for stage in route["stages"]],
            ["stage_1_quick_ratio", "stage_2_net_profit_margin"],
        )
        self.assertEqual(
            [operand["concept_id"] for operand in route["stages"][1]["required_operands"]],
            ["net_income", "net_revenue"],
        )

    def test_repeated_metric_occurrences_are_not_discarded(self):
        route = self.registry.build_question_route(
            question_item(
                "Hệ số thanh toán nhanh năm 2022 và hệ số thanh toán nhanh năm 2023 của HPG"
            )
        )
        self.assertEqual(
            [stage["metric_id"] for stage in route["stages"]],
            ["quick_ratio", "quick_ratio"],
        )
        self.assertEqual(route["route_status"], "staged_candidate")

    def test_direct_concept_uses_literal_lookup_candidate(self):
        route = self.registry.build_question_route(
            question_item("Tổng tài sản ngắn hạn của HPG năm 2022 là bao nhiêu?")
        )
        self.assertEqual(route["route_status"], "concept_lookup_candidate")
        stage = route["stages"][0]
        self.assertEqual(stage["route_kind"], "reported_concept")
        self.assertEqual(stage["concept_id"], "current_assets")
        self.assertEqual(stage["retrieval_filters"]["table_types"], ["balance_sheet"])

    def test_direct_concept_with_uncontrolled_operation_abstains(self):
        route = self.registry.build_question_route(
            question_item(
                "Tính chênh lệch tổng tài sản ngắn hạn của HPG giữa năm 2022 và 2021"
            )
        )
        self.assertEqual(route["route_status"], "abstain")
        self.assertEqual(route["reason_codes"], ["DIRECT_CONCEPT_OPERATION_UNSUPPORTED"])
        self.assertEqual(route["stages"][0]["concept_id"], "current_assets")
        self.assertEqual(route["feedback"]["missing_contract"], ["controlled_operation_contract"])

    def test_no_match_is_structured_abstain(self):
        route = self.registry.build_question_route(
            question_item("Số lượng nhân viên bình quân của HPG năm 2022 là bao nhiêu?")
        )
        self.assertEqual(route["route_status"], "abstain")
        self.assertEqual(route["reason_codes"], ["NO_LITERAL_METRIC_OR_CONCEPT_MATCH"])
        self.assertEqual(
            route["feedback"]["missing_contract"], ["literal_metric_or_concept_candidate"]
        )
        self.assertFalse(route["feedback"]["invent_synonyms_or_evidence_allowed"])
        self.assertFalse(route["source_contract"]["evidence_eligible"])

    def test_missing_question_plan_context_is_reason_coded_without_inference(self):
        route = self.registry.build_question_route(
            {
                "id": 102,
                "question": "Hệ số thanh toán nhanh của HPG năm 2022 là bao nhiêu?",
                "question_plan": {"tickers": [], "years": [], "scope": None},
            }
        )
        self.assertEqual(route["route_status"], "abstain")
        self.assertEqual(
            route["reason_codes"],
            [
                "MISSING_ENTITY_CONTEXT",
                "MISSING_SCOPE_CONTEXT",
                "MISSING_YEAR_CONTEXT",
            ],
        )
        self.assertEqual(route["question_context"], {
            "entities": [],
            "years": [],
            "scope": None,
            "source": "question_plan",
        })
        self.assertEqual(route["feedback"]["missing_contract"], ["entity", "scope", "year"])


if __name__ == "__main__":
    unittest.main()
