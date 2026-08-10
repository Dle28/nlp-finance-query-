import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "analyze_autonomous_reviews", ROOT / "scripts" / "analyze_autonomous_reviews.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def calibrated_row(qid: int = 1, *, status: str = "machine_calibrated") -> dict:
    return {
        "id": qid,
        "family": "direct_lookup",
        "consensus_status": status,
        "machine_candidate_uid": "u1" if status == "machine_calibrated" else None,
        "machine_candidate_source": "raw_v2_direct_source_discovery",
        "structure_validation": {"validated": status == "machine_calibrated"},
        "machine_self_review": {
            "training_eligible": status == "machine_calibrated",
            "selection_policy": "ordinary_multi_view_consensus",
            "selected_value_binding": {
                "status": "cell_bound" if status == "machine_calibrated" else "unbound",
                "binding_reason": "explicit_year_header",
            },
            "selected_assessment": {
                "raw_metric_identity": {"exact": status == "machine_calibrated"}
            },
        },
    }


class AutonomousReviewAuditTests(unittest.TestCase):
    def test_audit_counts_only_provenance_validated_machine_silver(self):
        report = mod.audit_reviews(
            [calibrated_row(), calibrated_row(2, status="machine_provisional")],
            min_machine_pairs=2,
        )
        self.assertEqual(report["status_counts"]["machine_calibrated"], 1)
        self.assertEqual(
            report["machine_calibrated_provenance"]["validated_count"], 1
        )
        self.assertFalse(report["training_readiness"]["ready"])
        self.assertEqual(report["training_readiness"]["remaining_pairs"], 1)

    def test_audit_rejects_machine_silver_without_exact_cell_provenance(self):
        invalid = calibrated_row()
        invalid["machine_self_review"]["selected_assessment"]["raw_metric_identity"] = {
            "exact": False
        }
        with self.assertRaisesRegex(ValueError, "provenance validation"):
            mod.audit_reviews([invalid])

    def test_audit_reports_new_calibrated_delta(self):
        report = mod.audit_reviews(
            [calibrated_row()],
            baseline_rows=[calibrated_row(status="machine_provisional")],
        )
        self.assertEqual(report["comparison"]["new_machine_calibrated_count"], 1)
        self.assertEqual(
            report["comparison"]["status_change_counts"],
            {"machine_provisional->machine_calibrated": 1},
        )


if __name__ == "__main__":
    unittest.main()
