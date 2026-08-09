from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "compile_vifinqa_submission", ROOT / "scripts" / "compile_vifinqa_submission.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class CompileViFinQASubmissionTests(unittest.TestCase):
    def test_explicitly_shadow_only_ledger_row_is_rejected(self) -> None:
        row = {
            "id": 960,
            "provenance_status": "machine_calibrated",
            "execution_status": "grounded",
            "grounding_status": "exact_rows_validated",
            "formula_definition_status": "defined",
            "submission_eligible": False,
        }
        with self.assertRaisesRegex(ValueError, "not submission-eligible"):
            mod.validate_ledger_row(row)

    def test_legacy_or_explicitly_eligible_row_keeps_existing_gate(self) -> None:
        row = {
            "id": 1,
            "provenance_status": "machine_calibrated",
            "execution_status": "grounded",
            "grounding_status": "exact_rows_validated",
            "formula_definition_status": "defined",
        }
        mod.validate_ledger_row(row)
        row["submission_eligible"] = True
        mod.validate_ledger_row(row)


if __name__ == "__main__":
    unittest.main()
