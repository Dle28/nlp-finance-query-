import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "train_review_calibrator",
    ROOT / "scripts" / "train_review_calibrator.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TrainReviewCalibratorTests(unittest.TestCase):
    def test_v2_partial_review_contributes_selected_positive_only(self):
        items = [
            {
                "id": 1,
                "candidates": [
                    {"internal_table_uid": "u1", "rank": 1},
                    {"internal_table_uid": "u2", "rank": 2},
                ],
            }
        ]
        annotations = {
            1: {
                "id": 1,
                "annotation_status": "human_verified_partial",
                "positive_table_uids": ["u1"],
                "structure_validation": {"complete": True},
            }
        }

        _x, y, groups, metadata = mod.build_dataset(items, annotations)

        self.assertEqual(y.tolist(), [1])
        self.assertEqual(groups.tolist(), [1])
        self.assertEqual(metadata["partial_positive_only_question_ids"], [1])
        self.assertEqual(metadata["partial_positive_only_candidate_count"], 1)

    def test_partial_review_without_v2_validation_is_not_used(self):
        items = [{"id": 1, "candidates": [{"internal_table_uid": "u1", "rank": 1}]}]
        annotations = {
            1: {
                "id": 1,
                "annotation_status": "human_verified_partial",
                "positive_table_uids": ["u1"],
            }
        }

        _x, y, groups, metadata = mod.build_dataset(items, annotations)

        self.assertEqual(len(y), 0)
        self.assertEqual(len(groups), 0)
        self.assertEqual(metadata["skipped_unvalidated_structure_question_ids"], [1])
