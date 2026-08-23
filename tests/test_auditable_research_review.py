from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from finance_query.research_review import (
    PROTOCOL,
    ResearchReviewValidationError,
    load_protocol,
    validate_matrix,
    write_report,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "configs" / "auditable_research_review_v1.json"
DEMO_MATRIX = ROOT / "examples" / "auditable_research_review_v1_demo.csv"


class AuditableResearchReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = load_protocol(PROTOCOL_PATH)

    def _demo_rows(self) -> tuple[list[str], list[dict[str, str]]]:
        with DEMO_MATRIX.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return list(reader.fieldnames or []), list(reader)

    def _write_matrix(self, directory: Path, rows: list[dict[str, str]]) -> Path:
        headers, _ = self._demo_rows()
        path = directory / "matrix.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_demo_matrix_passes_and_is_research_only(self) -> None:
        report = validate_matrix(self.protocol, DEMO_MATRIX)

        self.assertEqual(report["protocol"], PROTOCOL)
        self.assertEqual(report["validation_status"], "VALIDATION PASSED")
        self.assertEqual(report["matrix"]["record_count"], 5)
        self.assertEqual(report["counts"]["source_verified_evidence"], 3)
        self.assertEqual(
            report["policy"],
            {
                "research_only": True,
                "training_eligible": False,
                "submission_eligible": False,
                "promotion_allowed": False,
            },
        )

    def test_direct_evidence_cannot_use_unverified_provenance(self) -> None:
        _, rows = self._demo_rows()
        rows[0]["provenance_status"] = "needs_source_verification"
        with tempfile.TemporaryDirectory() as temporary:
            matrix = self._write_matrix(Path(temporary), rows)
            with self.assertRaisesRegex(ResearchReviewValidationError, "direct_evidence requires source_verified"):
                validate_matrix(self.protocol, matrix)

    def test_gap_requires_a_verified_counterexample(self) -> None:
        _, rows = self._demo_rows()
        rows.append(
            {
                "record_id": "G01",
                "record_type": "gap",
                "title": "Example bounded gap",
                "year": "",
                "source_url": "",
                "source_kind": "",
                "review_status": "not_applicable",
                "claim": "The corpus has no verified comparative source for this bounded issue.",
                "claim_type": "reviewer_synthesis",
                "evidence_locator": "",
                "supporting_record_ids": "E01;E02",
                "counterexample_record_ids": "",
                "project_axis": "semantic_certification",
                "provenance_status": "derived_from_verified_records",
                "promotion_allowed": "false",
                "notes": "Deliberately invalid fixture.",
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            matrix = self._write_matrix(Path(temporary), rows)
            with self.assertRaisesRegex(ResearchReviewValidationError, "gap requires at least one counterexample"):
                validate_matrix(self.protocol, matrix)

    def test_report_cannot_overwrite_existing_artifact(self) -> None:
        report = validate_matrix(self.protocol, DEMO_MATRIX)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            write_report(report, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["validation_status"], "VALIDATION PASSED")
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                write_report(report, path)


if __name__ == "__main__":
    unittest.main()
