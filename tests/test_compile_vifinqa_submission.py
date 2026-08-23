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

    def test_release_gate_requires_ready_empty_blocker_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.jsonl"
            ledger.write_text("{}\n", encoding="utf-8")
            ledger_manifest = ledger.with_suffix(".manifest.json")
            ledger_manifest.write_text("{}\n", encoding="utf-8")
            lineage = {key: "a" * 64 for key in mod.REQUIRED_LINEAGE_KEYS}
            gate = root / "release-gate.json"
            gate.write_text(
                json.dumps(
                    {
                        "protocol": mod.PRODUCTION_RELEASE_GATE_PROTOCOL,
                        "release_status": "ready_for_submission_compiler",
                        "production_eligible": True,
                        "submission_compilation_allowed": True,
                        "submission_eligible": False,
                        "answer_materialization_allowed": False,
                        "blockers": [],
                        "source_contract": {
                            "evidence_eligible": False,
                            "training_eligible": False,
                            "submission_eligible": False,
                            "promotion_allowed": False,
                        },
                        "inputs": {
                            "bundle_review_items": {"sha256": lineage["review_items_sha256"]},
                            "typed_plans": {"artifact": {"sha256": lineage["typed_operand_plans_sha256"]}},
                            "production_independent_audit": {"artifact": {"sha256": lineage["independent_audit_sha256"]}},
                            "production_execution_ledger": {
                                "artifact": {"sha256": hashlib.sha256(ledger.read_bytes()).hexdigest()},
                                "manifest": {"sha256": hashlib.sha256(ledger_manifest.read_bytes()).hexdigest()},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            mod.validate_production_release_gate(gate, lineage=lineage, execution_ledger=ledger)
            blocked = json.loads(gate.read_text())
            blocked["release_status"] = "blocked"
            gate.write_text(json.dumps(blocked), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not ready"):
                mod.validate_production_release_gate(gate, lineage=lineage, execution_ledger=ledger)

    def test_release_gate_rejects_ledger_hash_not_bound_to_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.jsonl"
            ledger.write_text("{}\n", encoding="utf-8")
            ledger.with_suffix(".manifest.json").write_text("{}\n", encoding="utf-8")
            lineage = {key: "a" * 64 for key in mod.REQUIRED_LINEAGE_KEYS}
            gate = root / "release-gate.json"
            gate.write_text(
                json.dumps(
                    {
                        "protocol": mod.PRODUCTION_RELEASE_GATE_PROTOCOL,
                        "release_status": "ready_for_submission_compiler",
                        "production_eligible": True,
                        "submission_compilation_allowed": True,
                        "submission_eligible": False,
                        "answer_materialization_allowed": False,
                        "blockers": [],
                        "source_contract": {
                            "evidence_eligible": False,
                            "training_eligible": False,
                            "submission_eligible": False,
                            "promotion_allowed": False,
                        },
                        "inputs": {
                            "bundle_review_items": {"sha256": lineage["review_items_sha256"]},
                            "typed_plans": {"artifact": {"sha256": lineage["typed_operand_plans_sha256"]}},
                            "production_independent_audit": {"artifact": {"sha256": lineage["independent_audit_sha256"]}},
                            "production_execution_ledger": {
                                "artifact": {"sha256": "b" * 64},
                                "manifest": {"sha256": hashlib.sha256(ledger.with_suffix(".manifest.json").read_bytes()).hexdigest()},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "does not bind"):
                mod.validate_production_release_gate(gate, lineage=lineage, execution_ledger=ledger)


if __name__ == "__main__":
    unittest.main()
