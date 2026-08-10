import importlib.util
import unittest
from pathlib import Path

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL
from finance_query.direct_replay import DIRECT_REPLAY_PROTOCOL


spec = importlib.util.spec_from_file_location(
    "export_review_labels",
    Path(__file__).parents[1] / "scripts" / "export_review_labels.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReviewTrainingGateTests(unittest.TestCase):
    def test_legacy_human_positive_is_not_training_eligible(self):
        row = {
            "annotation_status": "human_verified",
            "positive_table_uids": ["u1"],
        }
        self.assertFalse(mod.human_training_eligible(row))

    def test_v2_revalidated_human_positive_is_training_eligible(self):
        row = {
            "annotation_status": "human_verified",
            "positive_table_uids": ["u1"],
            "structure_validation": {"complete": True, "structure_version": 2},
        }
        self.assertTrue(mod.human_training_eligible(row))

    def test_machine_label_requires_exact_v2_validation_and_v3_source_contract(self):
        legacy = {
            "consensus_status": "machine_calibrated",
            "machine_candidate_uid": "u1",
        }
        self.assertFalse(mod.machine_training_eligible(legacy))
        grounded = {
            **legacy,
            "id": 1,
            "structure_validation": {"validated": True, "structure_version": 2},
            "machine_self_review": {
                "protocol": AUTONOMOUS_REVIEW_PROTOCOL,
                "training_eligible": True,
                "selected_assessment": {"raw_metric_identity": {"exact": True}},
            },
        }
        replay = {
            "protocol": DIRECT_REPLAY_PROTOCOL,
            "status": "shadow_replay_ready",
            "question_id": 1,
            "machine_consensus_status": "machine_calibrated",
            "valid_exact_candidates": [{"internal_table_uid": "u1"}],
        }
        self.assertTrue(mod.machine_training_eligible(grounded, direct_replay=replay))
        self.assertFalse(mod.machine_training_eligible(grounded, direct_replay=None))


if __name__ == "__main__":
    unittest.main()
