import importlib.util
import unittest
from pathlib import Path

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL


spec = importlib.util.spec_from_file_location(
    "train_dense_retriever",
    Path(__file__).parents[1] / "scripts" / "train_dense_retriever.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TrainDenseProvenanceTests(unittest.TestCase):
    def test_machine_silver_requires_autonomous_source_protocol(self):
        row = {
            "annotation_status": "machine_calibrated",
            "label_source": "machine",
            "structure_validation": {"validated": True},
            "machine_self_review": {
                "protocol": AUTONOMOUS_REVIEW_PROTOCOL,
                "training_eligible": True,
            },
        }
        mod.validate_provenance(row, "machine_silver", 1)

        row["machine_self_review"]["training_eligible"] = False
        with self.assertRaisesRegex(ValueError, "not training-eligible"):
            mod.validate_provenance(row, "machine_silver", 1)

        row["machine_self_review"]["training_eligible"] = True
        row["machine_self_review"]["protocol"] = "raw_v2_canonical_context_v1"
        with self.assertRaisesRegex(ValueError, "numeric-safe autonomous"):
            mod.validate_provenance(row, "machine_silver", 1)

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
