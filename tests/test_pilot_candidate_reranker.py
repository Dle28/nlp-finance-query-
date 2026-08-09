import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "pilot_candidate_reranker",
    ROOT / "scripts" / "train_pilot_candidate_reranker.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PilotCandidateRerankerTests(unittest.TestCase):
    def test_machine_pseudo_label_never_turns_other_candidates_into_negatives(self):
        items = [
            {
                "id": 1,
                "candidates": [
                    {"internal_table_uid": "h_positive", "rank": 1},
                    {"internal_table_uid": "h_unknown", "rank": 2},
                ],
            },
            {
                "id": 2,
                "candidates": [
                    {"internal_table_uid": "m_positive", "rank": 1},
                    {"internal_table_uid": "m_unknown", "rank": 2},
                ],
            },
        ]
        labels = {
            1: {
                "id": 1,
                "annotation_status": "human_verified",
                "label_source": "human",
                "human_verified": True,
                "training_weight": 1.0,
                "positive_table_uids": ["h_positive"],
                "structure_validation": {"complete": True, "structure_version": 2},
            },
            2: {
                "id": 2,
                "annotation_status": "machine_calibrated",
                "label_source": "machine",
                "human_verified": False,
                "training_weight": 0.8,
                "positive_table_uids": ["m_positive"],
                "structure_validation": {"validated": True, "structure_version": 2},
            },
        }
        ledger = {
            1: {"id": 1, "annotation_status": "human_verified", "training_eligible": True},
            2: {"id": 2, "annotation_status": "machine_calibrated", "training_eligible": True},
        }

        _x, y, weights, groups, evaluation_items, metadata = mod.build_dataset(
            items, labels, ledger
        )

        self.assertEqual(y.tolist(), [1, 0, 1])
        self.assertEqual(weights.tolist(), [1.0, 1.0, 0.8])
        self.assertEqual(groups.tolist(), [1, 1, 2])
        self.assertEqual(metadata["negative_candidate_counts"], {"human": 1})
        self.assertEqual(
            {candidate["uid"] for candidate in evaluation_items[2]["candidates"]},
            {"m_positive", "m_unknown"},
        )

    def test_incomplete_human_label_is_rejected(self):
        label = {
            "id": 1,
            "annotation_status": "human_verified",
            "label_source": "human",
            "human_verified": True,
            "training_weight": 1.0,
            "structure_validation": {},
        }
        ledger = {"annotation_status": "human_verified", "training_eligible": True}
        with self.assertRaisesRegex(ValueError, "complete V2 structure"):
            mod.validate_label_provenance(label, ledger)

    def test_ranking_metrics_use_all_selected_positive_tables(self):
        evaluation_items = {
            1: {
                "id": 1,
                "source": "human",
                "positive_uids": {"u1", "u3"},
                "candidates": [
                    {"uid": "u1", "rank": 2, "position": 0},
                    {"uid": "u2", "rank": 1, "position": 1},
                    {"uid": "u3", "rank": 3, "position": 2},
                ],
            }
        }

        metrics = mod.ranking_metrics(evaluation_items, ks=[1, 2, 3])

        self.assertEqual(metrics["mrr"], 0.5)
        self.assertEqual(metrics["at_k"]["1"]["recall"], 0.0)
        self.assertEqual(metrics["at_k"]["2"]["recall"], 0.5)
        self.assertEqual(metrics["at_k"]["3"]["recall"], 1.0)

    def test_promotion_holds_when_human_recall_regresses(self):
        baseline = {"mrr": 1.0, "at_k": {"1": {"recall": 1.0}}}
        oof = {"mrr": 1.0, "at_k": {"1": {"recall": 0.5}}}

        result = mod.promotion_recommendation(30, 30, baseline, oof)

        self.assertEqual(result["decision"], "hold")
        self.assertIn("recall@1", result["reason"])


if __name__ == "__main__":
    unittest.main()
