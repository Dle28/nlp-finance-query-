from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL
from finance_query.direct_replay import DIRECT_REPLAY_PROTOCOL


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "export_review_labels", ROOT / "scripts" / "export_review_labels.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class ExportReviewLabelTests(unittest.TestCase):
    def test_id_preview_is_bounded_but_keeps_the_full_count(self) -> None:
        self.assertEqual(mod.id_preview([]), "0")
        self.assertEqual(mod.id_preview([4, 8]), "2 [4, 8]")
        self.assertEqual(
            mod.id_preview(list(range(15))),
            "15 [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, …]",
        )

    def test_machine_silver_export_requires_v3_exact_raw_metric(self) -> None:
        review = {
            "id": 1,
            "consensus_status": "machine_calibrated",
            "machine_candidate_uid": "table-1",
            "structure_validation": {"validated": True},
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
            "valid_exact_candidates": [{"internal_table_uid": "table-1"}],
        }
        self.assertTrue(mod.machine_training_eligible(review, direct_replay=replay))

        self.assertFalse(mod.machine_training_eligible(review, direct_replay=None))
        replay["status"] = "shadow_ambiguous"
        self.assertFalse(mod.machine_training_eligible(review, direct_replay=replay))
        replay["status"] = "shadow_replay_ready"

        review["machine_self_review"]["selected_assessment"]["raw_metric_identity"][
            "exact"
        ] = False
        self.assertFalse(mod.machine_training_eligible(review, direct_replay=replay))

        review["machine_self_review"]["selected_assessment"]["raw_metric_identity"][
            "exact"
        ] = True
        review["machine_self_review"]["protocol"] = "raw_v2_canonical_context_v2"
        self.assertFalse(mod.machine_training_eligible(review, direct_replay=replay))


if __name__ == "__main__":
    unittest.main()
