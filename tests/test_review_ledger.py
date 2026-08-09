import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "review_ledger",
    Path(__file__).parents[1] / "scripts" / "build_review_ledger.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReviewLedgerTests(unittest.TestCase):
    def test_codex_review_cannot_claim_human_verified(self):
        item = {
            "id": 1,
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "direct_evidence": "VALUE: x"}],
        }
        review = {
            "reviewer_type": "codex_assisted",
            "human_verified": True,
            "proposed_positive_table_uids": ["u1"],
        }
        with self.assertRaisesRegex(ValueError, "cannot claim human_verified"):
            mod.validate_assistant_review(item, review, {"u1": {"rows": [["x"]]}})

    def test_exact_evidence_reference_is_validated(self):
        item = {
            "id": 1,
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "direct_evidence": "VALUE: exact"}],
        }
        review = {
            "reviewer_type": "codex_assisted",
            "human_verified": False,
            "proposed_positive_table_uids": ["u1"],
            "evidence_refs": [
                {
                    "internal_table_uid": "u1",
                    "rank": 1,
                    "row_indices": [0],
                    "direct_evidence": "VALUE: invented",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "differs from immutable bundle"):
            mod.validate_assistant_review(item, review, {"u1": {"rows": [["exact"]]}})

    def test_source_row_payload_must_match_immutable_table(self):
        item = {
            "id": 1,
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "direct_evidence": "VALUE: exact"}],
        }
        review = {
            "reviewer_type": "codex_assisted",
            "human_verified": False,
            "proposed_positive_table_uids": ["u1"],
            "evidence_refs": [
                {
                    "internal_table_uid": "u1",
                    "rank": 1,
                    "row_indices": [0],
                    "direct_evidence": "VALUE: exact",
                    "source_rows": [{"row_index": 0, "row": ["invented"]}],
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "differs from immutable table"):
            mod.validate_assistant_review(item, review, {"u1": {"rows": [["exact"]]}})

    def test_machine_assistant_disagreement_requires_human(self):
        machine = {"consensus_status": "machine_provisional", "machine_candidate_uid": "u1"}
        assistant = {
            "annotation_status": "machine_provisional",
            "proposed_positive_table_uids": ["u2"],
        }
        self.assertEqual(mod.ledger_status(machine, assistant, None), ("needs_human", "machine"))

    def test_human_always_wins_without_losing_provenance(self):
        machine = {"consensus_status": "machine_calibrated", "machine_candidate_uid": "u1"}
        assistant = {"annotation_status": "needs_human"}
        human = {"annotation_status": "human_verified", "positive_table_uids": ["u2"]}
        self.assertEqual(mod.ledger_status(machine, assistant, human), ("human_verified", "human"))

    def test_human_can_confirm_partial_without_creating_training_gold(self):
        machine = {"consensus_status": "machine_provisional", "machine_candidate_uid": "u1"}
        assistant = {"annotation_status": "needs_human"}
        human = {
            "annotation_status": "human_verified_partial",
            "positive_table_uids": ["u1"],
        }
        self.assertEqual(
            mod.ledger_status(machine, assistant, human),
            ("human_verified_partial", "human"),
        )

    def test_complete_formula_review_requires_exact_v2_operand_rows(self):
        item = {
            "id": 7,
            "candidates": [
                {"internal_table_uid": "u1", "rank": 1, "direct_evidence": "VALUE: exact"}
            ],
        }
        formula = {
            "definition_status": "defined",
            "operands": [
                {"operand_id": "numerator", "required": True},
                {"operand_id": "denominator", "required": True},
            ],
        }
        review = {
            "annotation_status": "human_verified",
            "positive_table_uids": ["u1"],
            "formula_spec": formula,
            "formula_confirmed": True,
            "operand_coverage": {
                "numerator": [
                    {
                        "uid": "u1",
                        "rank": 1,
                        "source_rows": [
                            {
                                "row_index": 0,
                                "row": ["Nợ ngắn hạn", "120"],
                                "column_labels": ["Chỉ tiêu", "2022"],
                            }
                        ],
                    }
                ],
                "denominator": [
                    {
                        "uid": "u1",
                        "rank": 1,
                        "source_rows": [
                            {
                                "row_index": 1,
                                "row": ["Vốn chủ sở hữu", "80"],
                                "column_labels": ["Chỉ tiêu", "2022"],
                            }
                        ],
                    }
                ],
            },
        }
        tables = {
            "u1": {
                "rows": [["Nợ ngắn hạn", "120"], ["Vốn chủ sở hữu", "80"]],
                "column_labels": ["Chỉ tiêu", "2022"],
                "structure_quality": {"status": "reconstructed_from_raw_html"},
            }
        }
        mod.validate_human_review(item, review, tables)

        review["operand_coverage"]["denominator"][0]["source_rows"][0]["row"] = [
            "Vốn chủ sở hữu",
            "invented",
        ]
        with self.assertRaisesRegex(ValueError, "differs from V2 source"):
            mod.validate_human_review(item, review, tables)

    def test_complete_formula_review_fails_without_v2_grid(self):
        item = {
            "id": 8,
            "candidates": [{"internal_table_uid": "u1", "rank": 1}],
        }
        review = {
            "annotation_status": "human_verified",
            "positive_table_uids": ["u1"],
            "formula_spec": {
                "definition_status": "defined",
                "operands": [{"operand_id": "x", "required": True}],
            },
            "formula_confirmed": True,
            "operand_coverage": {
                "x": [
                    {
                        "uid": "u1",
                        "rank": 1,
                        "source_rows": [
                            {
                                "row_index": 0,
                                "row": ["Metric", "1"],
                                "column_labels": ["Chỉ tiêu", "2022"],
                            }
                        ],
                    }
                ]
            },
        }
        tables = {
            "u1": {
                "rows": [["Metric", "1"]],
                "column_labels": ["Chỉ tiêu", "2022"],
                "structure_quality": {"status": "legacy_bundle_rows"},
            }
        }
        with self.assertRaisesRegex(ValueError, "lacks V2 rows"):
            mod.validate_human_review(item, review, tables)

    def test_legacy_human_label_is_requeued_for_v2_refresh(self):
        ledger = [
            {
                "id": 9,
                "family": "direct_lookup",
                "annotation_status": "human_verified",
                "human_verified": True,
                "needs_review_refresh": True,
                "machine_assistant_disagreement": False,
                "human_review_reasons": ["legacy_human_label_requires_v2_revalidation"],
            }
        ]
        queue = mod.make_human_check_queue(ledger, 1)
        self.assertEqual([row["id"] for row in queue], [9])


if __name__ == "__main__":
    unittest.main()
