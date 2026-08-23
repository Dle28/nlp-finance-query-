import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_financial_taxonomy_calibration_set import build_calibration_set


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_record(
    *,
    index: int,
    review_type: str,
    risk_score: int,
    concept_id: str = "inventory",
) -> dict:
    if review_type == "row_concept":
        candidate = {
            "record_kind": "row_semantic_candidate",
            "document_id": f"d{index}",
            "internal_table_uid": f"t{index}",
            "row_index": index,
            "table_type": "balance_sheet",
            "navigation_gate_status": "blocked",
            "concept_candidates": [{"concept_id": concept_id}],
        }
    elif review_type == "table_role":
        candidate = {
            "record_kind": "table_role_candidate",
            "document_id": f"d{index}",
            "internal_table_uid": f"t{index}",
            "existing_table_type": "other",
            "proposed_table_type": "income_statement",
        }
    else:
        candidate = {
            "record_kind": "document_sector_candidate",
            "document_id": f"d{index}",
            "sector": "banking",
            "status": "candidate",
        }
    return {
        "review_type": review_type,
        "risk_score": risk_score,
        "risk_reason_codes": ["risk"],
        "candidate": candidate,
        "source_excerpt": {"document_id": f"d{index}"},
    }


class FinancialTaxonomyCalibrationSetTests(unittest.TestCase):
    def test_keeps_all_high_risk_and_leaves_multi_axis_labels_blank(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            examples = root / "examples.jsonl"
            manifest = root / "examples.manifest.json"
            output = root / "calibration.jsonl"
            records = [
                candidate_record(index=1, review_type="row_concept", risk_score=190),
                candidate_record(index=2, review_type="table_role", risk_score=120),
                candidate_record(index=3, review_type="row_concept", risk_score=40),
                candidate_record(index=4, review_type="table_role", risk_score=40),
                candidate_record(index=5, review_type="document_sector", risk_score=40),
            ]
            examples.write_text(
                "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
            )
            manifest.write_text(
                json.dumps({"outputs": {"examples": {"sha256": sha(examples)}}}),
                encoding="utf-8",
            )
            result = build_calibration_set(
                audit_examples=examples,
                audit_manifest=manifest,
                output=output,
                target_count=4,
            )
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(result["calibration_count"], 4)
            self.assertEqual(result["risk_band_counts"]["critical"], 1)
            self.assertEqual(result["risk_band_counts"]["high"], 1)
            selected_documents = {row["candidate"]["document_id"] for row in rows}
            self.assertTrue({"d1", "d2"}.issubset(selected_documents))
            self.assertTrue(
                all(value is None for row in rows for key, value in row["calibration_labels"].items() if key not in {"reason_codes"})
            )
            self.assertTrue(all(row["calibration_labels"]["reason_codes"] == [] for row in rows))
            self.assertTrue(all(not row["source_contract"]["promotion_allowed"] for row in rows))

    def test_rejects_manifest_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            examples = root / "examples.jsonl"
            manifest = root / "examples.manifest.json"
            examples.write_text(json.dumps(candidate_record(index=1, review_type="row_concept", risk_score=10)) + "\n")
            manifest.write_text(json.dumps({"outputs": {"examples": {"sha256": "bad"}}}))
            with self.assertRaisesRegex(ValueError, "hash"):
                build_calibration_set(
                    audit_examples=examples,
                    audit_manifest=manifest,
                    output=root / "out.jsonl",
                    target_count=1,
                )


if __name__ == "__main__":
    unittest.main()
