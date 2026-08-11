from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.build_qwen_staged_execution_ledger import build_ledger


FAMILY = "quick_ratio_median_then_net_profit_margin"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class QwenStagedExecutionLedgerTests(unittest.TestCase):
    def make_inputs(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        bundle = root / "bundle"
        bundle.mkdir()
        write_jsonl(bundle / "review_items.jsonl", [{"id": 7, "question": "Q7"}])
        routes = root / "routes.jsonl"
        write_jsonl(routes, [{"id": 7, "route": {"routing_status": "planned", "family": FAMILY}}])
        qwen = root / "qwen.jsonl"
        write_jsonl(
            qwen,
            [
                {
                    "id": 7,
                    "record_type": "deterministic_final_result",
                    "annotation_status": "machine_provisional",
                    "human_verified": False,
                    "submission_eligible": False,
                    "result_value": "0.5",
                    "result_unit": "percent",
                }
            ],
        )
        qwen.with_suffix(".manifest.json").write_text(
            json.dumps(
                {
                    "dry_run": False,
                    "decision_fixture": False,
                    "submission_eligible": False,
                    "provenance_promotion_allowed": False,
                    "supported_families": [FAMILY],
                }
            ),
            encoding="utf-8",
        )
        audit = root / "audit.jsonl"
        write_jsonl(
            audit,
            [
                {
                    "id": 7,
                    "protocol": "independent_staged_llm_source_audit_v1",
                    "annotation_status": "machine_calibrated",
                    "provenance_status": "machine_calibrated",
                    "human_verified": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "result_value": "0.5",
                    "result_unit": "percent",
                    "direct_replay_gate": {"stage_1": {"status": "direct_replay_ready"}},
                    "independent_critic_gate": {"stage_1": {"status": "independent_critic_ready"}},
                    "reason_codes": ["independently_replayed"],
                }
            ],
        )
        return bundle, routes, qwen, audit, root / "ledger.jsonl"

    def test_machine_calibrated_staged_result_becomes_nonproduction_ledger_row(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle, routes, qwen, audit, output = self.make_inputs(Path(tmp))
            manifest = build_ledger(
                bundle_dir=bundle,
                routes_path=routes,
                qwen_output_path=qwen,
                audit_path=audit,
                output_path=output,
            )
            row = json.loads(output.read_text(encoding="utf-8").strip())
            self.assertEqual(manifest["record_count"], 1)
            self.assertEqual(row["execution_status"], "grounded")
            self.assertEqual(row["provenance_status"], "machine_calibrated")
            self.assertTrue(row["requires_production_audit"])
            self.assertFalse(row["training_eligible"])
            self.assertFalse(row["submission_eligible"])

    def test_final_value_mismatch_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle, routes, qwen, audit, output = self.make_inputs(Path(tmp))
            rows = [json.loads(line) for line in qwen.read_text(encoding="utf-8").splitlines()]
            rows[0]["result_value"] = "0.6"
            write_jsonl(qwen, rows)
            with self.assertRaisesRegex(ValueError, "does not match independent audit"):
                build_ledger(
                    bundle_dir=bundle,
                    routes_path=routes,
                    qwen_output_path=qwen,
                    audit_path=audit,
                    output_path=output,
                )


if __name__ == "__main__":
    unittest.main()
