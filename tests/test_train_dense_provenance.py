import importlib.util
import unittest
from pathlib import Path

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL
from finance_query.direct_replay import DIRECT_REPLAY_PROTOCOL


spec = importlib.util.spec_from_file_location(
    "train_dense_retriever",
    Path(__file__).parents[1] / "scripts" / "train_dense_retriever.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TrainDenseProvenanceTests(unittest.TestCase):
    def test_machine_silver_requires_autonomous_source_protocol(self):
        row = {
            "id": 1,
            "annotation_status": "machine_calibrated",
            "label_source": "machine",
            "positive_table_uids": ["table-1"],
            "structure_validation": {"validated": True},
            "machine_self_review": {
                "protocol": AUTONOMOUS_REVIEW_PROTOCOL,
                "training_eligible": True,
            },
            "direct_replay_gate": {
                "protocol": DIRECT_REPLAY_PROTOCOL,
                "status": "shadow_replay_ready",
                "question_id": 1,
                "machine_selected_uid": "table-1",
                "replay_artifact_sha256": "a" * 64,
                "training_gate_only": True,
            },
        }
        mod.validate_provenance(
            row, "machine_silver", 1, expected_replay_sha="a" * 64
        )

        row["machine_self_review"]["training_eligible"] = False
        with self.assertRaisesRegex(ValueError, "not training-eligible"):
            mod.validate_provenance(
                row, "machine_silver", 1, expected_replay_sha="a" * 64
            )

        row["machine_self_review"]["training_eligible"] = True
        row["machine_self_review"]["protocol"] = "raw_v2_canonical_context_v1"
        with self.assertRaisesRegex(ValueError, "numeric-safe autonomous"):
            mod.validate_provenance(
                row, "machine_silver", 1, expected_replay_sha="a" * 64
            )

    def test_machine_silver_requires_independent_replay_gate(self):
        row = {
            "id": 1,
            "annotation_status": "machine_calibrated",
            "label_source": "machine",
            "positive_table_uids": ["table-1"],
            "structure_validation": {"validated": True},
            "machine_self_review": {
                "protocol": AUTONOMOUS_REVIEW_PROTOCOL,
                "training_eligible": True,
            },
        }
        with self.assertRaisesRegex(ValueError, "independent direct replay"):
            mod.validate_provenance(
                row, "machine_silver", 1, expected_replay_sha="a" * 64
            )

    def test_ordinary_machine_calibrated_label_is_not_machine_silver(self):
        row = {
            "annotation_status": "machine_calibrated",
            "label_source": "machine",
            "structure_validation": {"validated": True},
        }
        with self.assertRaisesRegex(ValueError, "source protocol"):
            mod.validate_provenance(row, "machine_silver", 1)


if __name__ == "__main__":
    unittest.main()
