import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_financial_taxonomy_review_assignments import build_assignments
from scripts.score_financial_taxonomy_reviews import score_reviews


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def calibration_item(index: int, review_type: str) -> dict:
    return {
        "calibration_item_id": f"cal-{index:04d}",
        "risk_band": "critical",
        "stratum": f"{review_type}|x",
        "review_type": review_type,
        "risk_score": 200,
        "risk_reason_codes": ["risk"],
        "candidate": {"record_kind": review_type, "document_id": f"d{index}"},
        "source_excerpt": {"document_id": f"d{index}"},
        "review_instructions": ["review"],
        "review_contract": {"independent_reviews_required": 2},
        "source_contract": {"promotion_allowed": False},
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
    }


class ScoreFinancialTaxonomyReviewsTests(unittest.TestCase):
    def build_completed(self, root: Path) -> tuple[Path, Path, Path]:
        calibration = root / "calibration.jsonl"
        calibration_manifest = root / "calibration.manifest.json"
        items = [calibration_item(1, "row_concept"), calibration_item(2, "table_role")]
        write_jsonl(calibration, items)
        calibration_manifest.write_text(
            json.dumps({"output": {"sha256": sha(calibration)}}), encoding="utf-8"
        )
        assignment_manifest = build_assignments(
            calibration_set=calibration,
            calibration_manifest=calibration_manifest,
            output_dir=root / "assignments",
        )
        outputs = []
        for slot in ("reviewer_a", "reviewer_b"):
            path = Path(assignment_manifest["outputs"][slot]["path"])
            rows = load_jsonl(path)
            for row in rows:
                row["review_provenance"] = {
                    "reviewer_id": slot,
                    "reviewer_type": "human",
                    "completed_at_utc": "2026-08-12T00:00:00Z",
                    "blind_to_other_review": True,
                }
                labels = row["calibration_labels"]
                labels.update(
                    {
                        "navigation_eligible": False,
                        "source_coordinates_valid": True,
                        "abstain_required": True,
                        "decision": "accept",
                    }
                )
                if row["review_type"] == "row_concept":
                    labels["semantic_correct"] = True
                else:
                    labels["table_role_correct"] = slot == "reviewer_a"
                    if slot == "reviewer_b":
                        labels["decision"] = "reject"
            completed = root / f"{slot}_completed.jsonl"
            write_jsonl(completed, rows)
            outputs.append(completed)
        return calibration, outputs[0], outputs[1]

    def test_scores_agreement_and_materializes_disagreement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calibration, reviewer_a, reviewer_b = self.build_completed(root)
            summary = score_reviews(
                calibration_set=calibration,
                reviewer_a=reviewer_a,
                reviewer_b=reviewer_b,
                output_summary=root / "summary.json",
                output_adjudication=root / "adjudication.jsonl",
            )
            self.assertEqual(summary["completion_counts"], {"both_complete": 2})
            self.assertEqual(summary["exact_complete_agreement_count"], 1)
            self.assertEqual(summary["adjudication_count"], 1)
            adjudication = load_jsonl(root / "adjudication.jsonl")
            self.assertIn(
                "axis_disagreement:table_role_correct",
                adjudication[0]["disagreement_reason_codes"],
            )
            self.assertIn(
                "decision_disagreement", adjudication[0]["disagreement_reason_codes"]
            )
            self.assertFalse(summary["calibration_truth_eligible"])

    def test_rejects_changed_source_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calibration, reviewer_a, reviewer_b = self.build_completed(root)
            rows = load_jsonl(reviewer_a)
            rows[0]["source_excerpt"] = {"document_id": "tampered"}
            write_jsonl(reviewer_a, rows)
            with self.assertRaisesRegex(ValueError, "immutable source payload"):
                score_reviews(
                    calibration_set=calibration,
                    reviewer_a=reviewer_a,
                    reviewer_b=reviewer_b,
                    output_summary=root / "summary.json",
                    output_adjudication=root / "adjudication.jsonl",
                )


if __name__ == "__main__":
    unittest.main()
