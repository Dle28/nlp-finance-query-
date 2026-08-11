from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
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
    def eligible_row(self) -> dict:
        digest = "a" * 64
        lineage = {key: digest for key in mod.REQUIRED_LINEAGE_KEYS}
        return {
            "id": 1,
            "provenance_status": "machine_calibrated",
            "execution_status": "grounded",
            "grounding_status": "exact_rows_validated",
            "formula_definition_status": "defined",
            "submission_eligible": True,
            "artifact_lineage": lineage,
            "production_eligibility": {
                "protocol": mod.PRODUCTION_ELIGIBILITY_PROTOCOL,
                "status": "approved",
                "independent_audit_status": "passed",
                "independent_audit_sha256": digest,
            },
        }

    def test_explicitly_shadow_only_ledger_row_is_rejected(self) -> None:
        row = {
            "id": 960,
            "provenance_status": "machine_calibrated",
            "execution_status": "grounded",
            "grounding_status": "exact_rows_validated",
            "formula_definition_status": "defined",
            "submission_eligible": False,
        }
        with self.assertRaisesRegex(ValueError, "literal true"):
            mod.validate_ledger_row(row)

    def test_complete_production_lineage_is_required(self) -> None:
        row = self.eligible_row()
        mod.validate_ledger_row(row)
        row.pop("artifact_lineage")
        with self.assertRaisesRegex(ValueError, "artifact_lineage"):
            mod.validate_ledger_row(row)

    def test_legacy_row_without_explicit_eligibility_is_rejected(self) -> None:
        row = self.eligible_row()
        row.pop("submission_eligible")
        with self.assertRaisesRegex(ValueError, "literal true"):
            mod.validate_ledger_row(row)

    def test_production_staged_contract_uses_staged_grounding_status(self) -> None:
        row = self.eligible_row()
        row.update(
            {
                "execution_mode": "exact_staged_contract",
                "grounding_status": "staged_exact_cells_replayed",
            }
        )
        mod.validate_ledger_row(row)

    def test_staged_contract_cannot_claim_ordinary_exact_rows(self) -> None:
        row = self.eligible_row()
        row["execution_mode"] = "exact_staged_contract"
        with self.assertRaisesRegex(ValueError, "staged_exact_cells_replayed"):
            mod.validate_ledger_row(row)

    def test_production_audit_must_cover_and_approve_every_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "question_id": 1,
                        "production_eligible": True,
                        "independent_audit_status": "passed",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            path.with_suffix(".manifest.json").write_text(
                json.dumps(
                    {
                        "protocol": mod.PRODUCTION_AUDIT_PROTOCOL,
                        "audit_status": "passed",
                        "production_eligibility_approved": True,
                        "question_count": 1,
                        "sidecar_sha256": digest,
                    }
                ),
                encoding="utf-8",
            )
            mod.validate_production_audit(path, {1})
            with self.assertRaisesRegex(ValueError, "cover every"):
                mod.validate_production_audit(path, {1, 2})


if __name__ == "__main__":
    unittest.main()
