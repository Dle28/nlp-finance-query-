from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "analyze_formula_evidence", ROOT / "scripts" / "analyze_formula_evidence.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class AnalyzeFormulaEvidenceTests(unittest.TestCase):
    def test_selected_operand_must_match_raw_cell_header_and_parser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            table = {"internal_table_uid": "u1", "rows": [["Chỉ tiêu", "Năm 2023"], ["Tiền", "100"]]}
            context = {
                "internal_table_uid": "u1",
                "canonical_headers": {"columns": [{"column_index": 1, "source_label": "Năm 2023"}]},
            }
            (bundle / "tables_structured_v2.jsonl").write_text(json.dumps(table) + "\n", encoding="utf-8")
            (bundle / "tables_evidence_context_v1.jsonl").write_text(json.dumps(context) + "\n", encoding="utf-8")
            rows = [
                {
                    "id": 1,
                    "selected_operand_matches": {
                        "cash": {
                            "internal_table_uid": "u1",
                            "source_row": ["Tiền", "100"],
                            "binding": {
                                "status": "cell_bound",
                                "row_index": 1,
                                "column_index": 1,
                                "column_label": "Năm 2023",
                                "raw_value": "100",
                                "parsed_value": "100",
                            },
                        }
                    },
                }
            ]
            self.assertEqual(mod.validate_selected_matches(rows, bundle), 1)
            rows[0]["selected_operand_matches"]["cash"]["binding"]["parsed_value"] = "999"
            with self.assertRaises(ValueError):
                mod.validate_selected_matches(rows, bundle)

    def test_summary_separates_coverage_from_execution_eligibility(self) -> None:
        summary = mod.summarize(
            [
                {
                    "id": 1,
                    "formula": {"formula_id": "ratio"},
                    "evidence_completeness": "partial",
                    "operand_coverage_status": "complete",
                    "reason_codes": ["ambiguous_operand_bindings"],
                    "missing_operand_ids": [],
                    "candidate_gate_rejections": {"candidate_scope_mismatch": 2},
                }
            ]
        )
        self.assertEqual(summary["fully_covered_but_not_complete_ids"], [1])
        self.assertEqual(summary["candidate_gate_rejection_counts"], {"candidate_scope_mismatch": 2})


if __name__ == "__main__":
    unittest.main()
