import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "build_direct_evidence_sets", ROOT / "scripts" / "build_direct_evidence_sets.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class DirectEvidenceMetricVariantTests(unittest.TestCase):
    def test_variant_removes_only_resolved_ticker_and_trailing_period(self):
        variants = mod.context_free_metric_variants(
            {
                "effective_metric": "Quỹ khen thưởng, phúc lợi HT1 cuối năm",
                "question_plan": {"tickers": ["HT1"]},
            }
        )
        self.assertEqual(len(variants), 2)
        self.assertEqual(
            variants[1]["matched_metric"], "Quỹ khen thưởng, phúc lợi"
        )
        self.assertEqual(
            variants[1]["policy"],
            "exact_raw_v2_metric_context_stripped_token_sequence_v1",
        )
        self.assertEqual(variants[1]["removed_context"], ["cuối năm", "HT1"])

    def test_variant_never_removes_accounting_subtotal_modifier(self):
        variants = mod.context_free_metric_variants(
            {
                "effective_metric": "Tổng phải thu ngắn hạn khác",
                "question_plan": {"tickers": ["NVL"]},
            }
        )
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["matched_metric"], "Tổng phải thu ngắn hạn khác")

    def test_variant_may_remove_only_leading_balance_query_descriptor(self):
        variants = mod.context_free_metric_variants(
            {
                "effective_metric": "Số dư vay ngắn hạn CEO cuối năm",
                "question_plan": {"tickers": ["CEO"]},
            }
        )
        self.assertEqual(variants[1]["matched_metric"], "vay ngắn hạn")
        self.assertEqual(
            variants[1]["removed_context"], ["cuối năm", "CEO", "Số dư"]
        )

    def test_one_token_context_free_fallback_is_rejected(self):
        variants = mod.context_free_metric_variants(
            {
                "effective_metric": "Tiền VJC cuối năm",
                "question_plan": {"tickers": ["VJC"]},
            }
        )
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["matched_metric"], "Tiền VJC cuối năm")

    def test_context_axis_requires_explicit_parent_and_exact_child_row_in_question(self):
        table = {
            "report_segment": {
                "source_context_sha256": "a" * 64,
                "source_parent_heading": "9 CHO VAY KHÁCH HÀNG",
                "source_heading": "9.6 Theo ngành nghề kinh doanh",
            }
        }
        variant = mod.context_axis_metric_variant(
            "Số dư cho vay khách hàng ngành Thương mại cuối năm 2022 là bao nhiêu?",
            table,
            ["Thương mại", "72.917.566"],
            original_metric="Số dư cho vay khách hàng ngành Thương mại",
        )
        self.assertIsNotNone(variant)
        assert variant is not None
        self.assertEqual(
            variant["matched_metric"], "9 CHO VAY KHÁCH HÀNG | Thương mại"
        )
        self.assertEqual(
            variant["policy"],
            "exact_raw_v2_source_parent_and_row_token_sequence_v1",
        )
        self.assertIsNone(
            mod.context_axis_metric_variant(
                "Số dư cho vay khách hàng cuối năm 2022 là bao nhiêu?",
                table,
                ["Thương mại", "72.917.566"],
                original_metric="Số dư cho vay khách hàng",
            )
        )

    def test_context_axis_keeps_value_row_in_v3_delimited_field(self):
        candidate = mod.base_candidate(
            uid="u1",
            ticker="ACB",
            report_year=2022,
            scope="separate",
            row_index=1,
            row=["Thương mại", "72.917.566"],
            metric_variant={
                "original_metric": "Số dư cho vay khách hàng ngành Thương mại",
                "matched_metric": "9 CHO VAY KHÁCH HÀNG | Thương mại",
                "policy": "exact_raw_v2_source_parent_and_row_token_sequence_v1",
                "removed_context": [],
                "context_evidence": {"parent_heading": "9 CHO VAY KHÁCH HÀNG"},
            },
        )
        self.assertEqual(
            mod.v4.v3.projected_value_text(candidate["direct_evidence"]),
            "Thương mại | 72.917.566",
        )


if __name__ == "__main__":
    unittest.main()
