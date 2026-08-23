"""Tests for closed-world two-model component-selection agreement."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_ccl_phase5_component_selection_agreement.py"
SPEC = importlib.util.spec_from_file_location("component_selection_agreement", SCRIPT)
assert SPEC and SPEC.loader
agreement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agreement)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


class ComponentSelectionAgreementTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, Path]:
        primary_results = root / "primary.jsonl"
        challenger_results = root / "challenger.jsonl"
        primary_rows = [
            {
                "component_selection_packet_id": "packet-1",
                "internal_table_uid": "table-1",
                "status": "VALID_COMPONENT_SELECTION_ONLY",
                "selection": {"primary_component_id": "context-1", "supporting_component_ids": ["header-1"], "unresolved_conditions": []},
                "training_eligible": False,
                "certification_allowed": False,
            },
            {
                "component_selection_packet_id": "packet-2",
                "internal_table_uid": "table-2",
                "status": "VALID_COMPONENT_SELECTION_ONLY",
                "selection": {"primary_component_id": "context-2", "supporting_component_ids": ["row-2"], "unresolved_conditions": []},
                "training_eligible": False,
                "certification_allowed": False,
            },
        ]
        challenger_rows = [
            primary_rows[0],
            {
                **primary_rows[1],
                "selection": {"primary_component_id": "other-context-2", "supporting_component_ids": ["row-2"], "unresolved_conditions": []},
            },
        ]
        _write_jsonl(primary_results, primary_rows)
        _write_jsonl(challenger_results, challenger_rows)
        primary_audit = root / "primary-audit.json"
        challenger_audit = root / "challenger-audit.json"
        common = {
            "protocol": agreement.AUDIT_PROTOCOL,
            "audit_passed": True,
            "training_eligible": False,
            "certification_allowed": False,
            "input_hashes": {"phase5_component_selection_packets_v1.jsonl": "same-source-hash"},
        }
        _write_json(
            primary_audit,
            {
                **common,
                "route": {"route_id": "source-route", "model_id": "Qwen/Qwen3-8B"},
                "artifact_hashes": {primary_results.name: agreement.sha256_file(primary_results)},
            },
        )
        _write_json(
            challenger_audit,
            {
                **common,
                "route": {"route_id": "source-route", "model_id": "mistralai/Mistral-Nemo-Instruct-2407"},
                "artifact_hashes": {challenger_results.name: agreement.sha256_file(challenger_results)},
            },
        )
        return {
            "primary_audit": primary_audit,
            "primary_results": primary_results,
            "challenger_audit": challenger_audit,
            "challenger_results": challenger_results,
        }

    def test_keeps_exact_agreement_separate_from_quarantined_disagreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            report = agreement.compare_component_selection_agreement(**paths, output_dir=Path(temporary) / "output")
            self.assertEqual(report["status_counts"], {"DISAGREEMENT_QUARANTINED": 1, "EXACT_CLOSED_WORLD_AGREEMENT": 1})
            self.assertEqual(report["quarantine_count"], 1)
            rows = [json.loads(line) for line in (Path(temporary) / "output" / agreement.RESULTS_NAME).read_text(encoding="utf-8").splitlines()]
            self.assertFalse(any(row["training_eligible"] for row in rows))
            manifest = json.loads((Path(temporary) / "output" / agreement.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(
                set(manifest["inputs"]),
                {"primary_audit", "primary_results", "challenger_audit", "challenger_results"},
            )
            self.assertEqual(manifest["inputs"]["primary_audit"]["sha256"], agreement.sha256_file(paths["primary_audit"]))
            self.assertEqual(manifest["inputs"]["challenger_audit"]["sha256"], agreement.sha256_file(paths["challenger_audit"]))

    def test_rejects_a_result_that_no_longer_matches_its_audit_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            paths["challenger_results"].write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "challenger audit does not bind"):
                agreement.compare_component_selection_agreement(**paths, output_dir=Path(temporary) / "output")


if __name__ == "__main__":
    unittest.main()
