from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.build_subset_execution_package import materialize


class SubsetExecutionPackageTests(unittest.TestCase):
    def test_intersection_is_grounded_and_never_submission_eligible(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.jsonl"
            silver = root / "silver.jsonl"
            output = root / "subset.jsonl"
            ledger.write_text(
                json.dumps({"id": 1, "execution_status": "grounded", "grounding_status": "exact_rows_validated", "provenance_status": "machine_calibrated"})
                + "\n"
                + json.dumps({"id": 2, "execution_status": "not_executable", "grounding_status": "blocked", "provenance_status": "needs_human"})
                + "\n",
                encoding="utf-8",
            )
            silver.write_text(
                json.dumps({"id": 1, "annotation_status": "machine_calibrated"})
                + "\n"
                + json.dumps({"id": 2, "annotation_status": "machine_calibrated"})
                + "\n",
                encoding="utf-8",
            )
            manifest = materialize(ledger, silver, output)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(manifest["question_count"], 1)
            self.assertTrue(rows[0]["subset_execution_eligible"])
            self.assertFalse(rows[0]["submission_eligible"])
            self.assertEqual(manifest["blocked_counts"], {"execution_not_grounded": 1})


if __name__ == "__main__":
    unittest.main()
