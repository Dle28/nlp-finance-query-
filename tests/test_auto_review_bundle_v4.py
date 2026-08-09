import importlib.util
import sys
import unittest
from pathlib import Path

from finance_query.evidence_context import build_evidence_context


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "auto_review_bundle_v4",
    ROOT / "scripts" / "auto_review_bundle_v4.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class AutonomousReviewV4Tests(unittest.TestCase):
    def test_explicit_year_binds_only_matching_raw_header(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["", "Năm 2023", "Năm 2022"], ["Tiền", "100", "80"]],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 2, "covered_by_span": False},
                ],
                [
                    {"anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 2, "covered_by_span": False},
                ],
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        binding = mod.bind_value_row(
            {"question": "Tiền năm 2023 là bao nhiêu?"},
            {"report_year": 2023, "structure_validation": {"row_index": 1}},
            table,
            context,
        )
        self.assertEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["column_index"], 1)
        self.assertEqual(binding["value"], "100")
        self.assertEqual(binding["binding_reason"], "explicit_year_header")

    def test_year_without_matching_raw_header_remains_unbound(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["", "Năm 2023", "Năm 2022"], ["Tiền", "100", "80"]],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 2, "covered_by_span": False},
                ],
                [
                    {"anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 2, "covered_by_span": False},
                ],
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        binding = mod.bind_value_row(
            {"question": "Tiền năm 2021 là bao nhiêu?"},
            {"report_year": 2023, "structure_validation": {"row_index": 1}},
            table,
            context,
        )
        self.assertEqual(binding["status"], "ambiguous_period_column")

    def test_unreliable_ocr_number_cannot_be_cell_bound(self):
        table = {
            "internal_table_uid": "u_unsafe",
            "rows": [
                ["", "Năm 2023"],
                ["Chi phí tài chính", "(72.193.585.614)(27.471.160.925)"],
            ],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                    for column in range(2)
                ]
                for row in range(2)
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        binding = mod.bind_value_row(
            {"question": "Chi phí tài chính năm 2023 là bao nhiêu?"},
            {"report_year": 2023, "structure_validation": {"row_index": 1}},
            table,
            context,
        )
        self.assertNotEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["reason"], "projected evidence row is not a canonical data row")

    def test_direct_lookup_requires_exact_row_and_period_cell_binding(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["", "Số cuối năm"], ["Tiền", "100"]],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                ],
                [
                    {"anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
                ],
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        item = {
            "id": 1,
            "question": "Tiền vào cuối năm là bao nhiêu?",
            "weak_family": "direct_lookup",
            "effective_metric": "Tiền",
            "question_plan": {"family": "direct_lookup", "operands": [{"metric": "Tiền"}]},
            "candidates": [
                {
                    "internal_table_uid": "u1",
                    "rank": 1,
                    "candidate_source": "retrieved",
                    "metadata_score": 1.0,
                    "ticker_match": True,
                    "scope_match": True,
                    "year_match": True,
                    "best_row_index": 1,
                    "direct_evidence": "VALUE: Tiền | 100",
                    "evidence_features": {
                        "row_score": 1.0,
                        "metric_overlap": 1.0,
                        "question_overlap": 1.0,
                        "numeric": True,
                    },
                    "structure_validation": {"validated": True, "row_index": 1},
                }
            ],
        }

        review, _quarantine = mod.autonomous_review_item(
            item,
            {"u1": table},
            {"u1": context},
            token_gate=0.85,
            bigram_gate=0.45,
            min_agreement=0.67,
            silver_threshold=0.84,
        )

        binding = review["machine_self_review"]["selected_value_binding"]
        self.assertEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["column_index"], 1)
        self.assertEqual(review["consensus_status"], "machine_calibrated")
        self.assertTrue(review["machine_self_review"]["training_eligible"])

    def test_missing_period_header_cannot_be_autonomous_silver(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["Tiền", "100"]],
            "header_row_indices": [],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                ]
            ],
        }
        context = build_evidence_context(table)
        item = {
            "id": 1,
            "question": "Tiền cuối năm là bao nhiêu?",
            "weak_family": "direct_lookup",
            "effective_metric": "Tiền",
            "question_plan": {"family": "direct_lookup", "operands": [{"metric": "Tiền"}]},
            "candidates": [
                {
                    "internal_table_uid": "u1",
                    "rank": 1,
                    "candidate_source": "retrieved",
                    "metadata_score": 1.0,
                    "best_row_index": 0,
                    "direct_evidence": "VALUE: Tiền | 100",
                    "evidence_features": {"row_score": 1.0, "numeric": True},
                    "structure_validation": {"validated": True, "row_index": 0},
                }
            ],
        }

        review, _quarantine = mod.autonomous_review_item(
            item, {"u1": table}, {"u1": context}, 0.85, 0.45, 0.67, 0.84
        )

        self.assertNotEqual(review["consensus_status"], "machine_calibrated")


if __name__ == "__main__":
    unittest.main()
