from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.build_subset_execution_ledger import build_subset


class SubsetExecutionLedgerTests(unittest.TestCase):
    def test_only_dual_gated_grounded_rows_are_emitted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            def write(name: str, rows: list[dict]) -> Path:
                path = root / name
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                return path
            ledger = write("ledger.jsonl", [
                {"id": 1, "execution_status": "grounded", "provenance_status": "machine_calibrated"},
                {"id": 2, "execution_status": "not_executable"},
            ])
            selection = write("selection.jsonl", [
                {"id": 1, "annotation_status": "machine_calibrated", "independent_critic_gate": {"status": "independent_ready"}, "direct_replay_gate": {"status": "shadow_replay_ready"}},
                {"id": 2, "annotation_status": "machine_calibrated", "independent_critic_gate": {"status": "independent_ready"}, "direct_replay_gate": {"status": "shadow_replay_ready"}},
            ])
            audit = write("audit.jsonl", [
                {"question_id": 1, "independent_audit_status": "passed"},
                {"question_id": 2, "independent_audit_status": "passed"},
            ])
            output = root / "subset.jsonl"
            manifest = build_subset(ledger, selection, audit, output)
            self.assertEqual(manifest["subset_question_count"], 1)
            row = json.loads(output.read_text().splitlines()[0])
            self.assertTrue(row["training_eligible"])
            self.assertFalse(row["submission_eligible"])

    def test_audit_failure_is_excluded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            def write(name: str, row: dict) -> Path:
                path = root / name
                path.write_text(json.dumps(row) + "\n", encoding="utf-8")
                return path
            common = {"id": 1, "annotation_status": "machine_calibrated", "independent_critic_gate": {"status": "independent_ready"}, "direct_replay_gate": {"status": "shadow_replay_ready"}}
            manifest = build_subset(write("ledger.jsonl", {"id": 1, "execution_status": "grounded"}), write("selection.jsonl", common), write("audit.jsonl", {"question_id": 1, "independent_audit_status": "blocked"}), root / "subset.jsonl")
            self.assertEqual(manifest["subset_question_count"], 0)


if __name__ == "__main__":
    unittest.main()
