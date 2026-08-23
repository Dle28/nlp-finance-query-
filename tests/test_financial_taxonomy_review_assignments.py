import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_financial_taxonomy_review_assignments import build_assignments


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FinancialTaxonomyReviewAssignmentTests(unittest.TestCase):
    def test_builds_two_blind_assignments_with_different_stable_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calibration = root / "calibration.jsonl"
            manifest = root / "calibration.manifest.json"
            items = [
                {
                    "calibration_item_id": f"cal-{index:04d}",
                    "calibration_labels": {
                        "semantic_correct": None,
                        "table_role_correct": None,
                        "navigation_eligible": None,
                        "source_coordinates_valid": None,
                        "abstain_required": None,
                        "decision": None,
                        "reason_codes": [],
                        "reviewer_notes": None,
                    },
                    "source_contract": {"promotion_allowed": False},
                }
                for index in range(1, 11)
            ]
            calibration.write_text(
                "".join(json.dumps(item) + "\n" for item in items), encoding="utf-8"
            )
            manifest.write_text(
                json.dumps({"output": {"sha256": sha(calibration)}}), encoding="utf-8"
            )
            result = build_assignments(
                calibration_set=calibration,
                calibration_manifest=manifest,
                output_dir=root / "assignments",
            )
            assignments = {}
            for slot in ("reviewer_a", "reviewer_b"):
                path = Path(result["outputs"][slot]["path"])
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                assignments[slot] = [row["calibration_item_id"] for row in rows]
                self.assertEqual(len(rows), 10)
                self.assertTrue(all(row["reviewer_slot"] == slot for row in rows))
                self.assertTrue(
                    all(row["review_provenance"]["blind_to_other_review"] for row in rows)
                )
                self.assertTrue(all(not row["source_contract"]["promotion_allowed"] for row in rows))
            self.assertNotEqual(assignments["reviewer_a"], assignments["reviewer_b"])
            self.assertEqual(set(assignments["reviewer_a"]), set(assignments["reviewer_b"]))

    def test_rejects_prepopulated_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calibration = root / "calibration.jsonl"
            manifest = root / "calibration.manifest.json"
            calibration.write_text(
                json.dumps(
                    {
                        "calibration_item_id": "cal-0001",
                        "calibration_labels": {
                            "decision": "accept",
                            "reason_codes": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest.write_text(
                json.dumps({"output": {"sha256": sha(calibration)}}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "already contains"):
                build_assignments(
                    calibration_set=calibration,
                    calibration_manifest=manifest,
                    output_dir=root / "assignments",
                )


if __name__ == "__main__":
    unittest.main()
