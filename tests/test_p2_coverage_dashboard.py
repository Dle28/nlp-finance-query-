from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.build_p2_coverage_dashboard import build_dashboard


class P2CoverageDashboardTests(unittest.TestCase):
    def test_dashboard_is_read_only_and_reports_bottlenecks(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            files = {}
            payloads = {
                "typed": [{"question_id": 1, "decomposition_status": "abstain", "reason_codes": ["MISSING_OPERANDS"]}],
                "formula": [{"id": 1, "evidence_completeness": "partial", "reason_codes": ["ambiguous_operand_bindings"]}],
                "audit": [{"question_id": 1, "independent_audit_status": "blocked", "reason_codes": ["MISSING_OPERANDS"]}],
                "ledger": [{"id": 1, "execution_status": "not_executable"}],
                "census": [{"question_id": 1, "fingerprint": "fp", "route": "requires_operand_decomposition"}],
                "canaries": [{"fingerprint": "fp", "verdict": "explicit_block"}],
                "ocr": [{"internal_table_uid": "t", "triage_action": "review_required"}],
            }
            for name, rows in payloads.items():
                path = root / f"{name}.jsonl"
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                files[name] = path
            dashboard = build_dashboard(**files, output=root / "dashboard.json")
            self.assertEqual(dashboard["question_count"], 1)
            self.assertEqual(dashboard["audit_counts"], {"blocked": 1})
            self.assertEqual(dashboard["ocr_triage_counts"], {"review_required": 1})
            self.assertEqual(dashboard["canary_counts"], {"explicit_block": 1})
            self.assertEqual(dashboard["fingerprint_coverage"][0]["canary_verdict"], "explicit_block")
            self.assertEqual(
                dashboard["audit_blocker_matrix"],
                [
                    {
                        "family": "missing",
                        "typed_plan_status": "missing",
                        "formula_evidence_status": "missing",
                        "independent_critic_status": "missing",
                        "query_program_status": "missing",
                        "count": 1,
                    }
                ],
            )
            self.assertEqual(
                dashboard["formula_partial_matrix"],
                [{"formula_id": "missing", "missing_operand_count": 0, "count": 1}],
            )
            self.assertFalse(dashboard["source_contract"]["may_promote_provenance"])

    def test_dashboard_reads_nested_v1_ocr_triage(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            files = {}
            payloads = {
                "typed": [{"question_id": 1, "decomposition_status": "complete"}],
                "formula": [{"id": 1, "evidence_completeness": "complete"}],
                "audit": [{"question_id": 1, "independent_audit_status": "passed"}],
                "ledger": [{"id": 1, "execution_status": "grounded"}],
                "census": [{"question_id": 1, "fingerprint": "fp"}],
                "canaries": [{"fingerprint": "fp", "verdict": "exact_bound"}],
                "ocr": [{"internal_table_uid": "t", "triage": {"action": "normal"}}],
            }
            for name, rows in payloads.items():
                path = root / f"{name}.jsonl"
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                files[name] = path
            dashboard = build_dashboard(**files, output=root / "dashboard.json")
            self.assertEqual(dashboard["ocr_triage_counts"], {"normal": 1})
            self.assertEqual(dashboard["canary_counts"], {"exact_bound": 1})


if __name__ == "__main__":
    unittest.main()
