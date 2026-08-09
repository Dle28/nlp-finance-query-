import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "auto_review_bundle_v3",
    Path(__file__).parents[1] / "scripts" / "auto_review_bundle_v3.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class MachineStructureGuardTests(unittest.TestCase):
    def test_projected_value_binds_to_exact_v2_row_with_empty_cells(self):
        candidate = {
            "best_row_index": 1,
            "direct_evidence": "COLUMNS: Chỉ tiêu | 2022 || VALUE: Nợ ngắn hạn | 310 | 120",
        }
        table = {
            "rows": [
                ["", "Mã số", "Thuyết minh", "2022"],
                ["Nợ ngắn hạn", "310", "", "120"],
            ],
            "column_labels": ["Chỉ tiêu", "Mã số", "Thuyết minh", "2022"],
            "structure_quality": {"status": "reconstructed_from_raw_html"},
        }
        result = mod.validate_candidate_structure(candidate, table)
        self.assertTrue(result["validated"])
        self.assertEqual(result["row"], ["Nợ ngắn hạn", "310", "", "120"])
        self.assertEqual(result["column_labels"][3], "2022")

    def test_projected_value_mismatch_fails_closed(self):
        candidate = {
            "best_row_index": 0,
            "direct_evidence": "VALUE: Nợ ngắn hạn | 999",
        }
        table = {
            "rows": [["Nợ ngắn hạn", "120"]],
            "column_labels": ["Chỉ tiêu", "2022"],
            "structure_quality": {"status": "reconstructed_from_raw_html"},
        }
        result = mod.validate_candidate_structure(candidate, table)
        self.assertFalse(result["validated"])
        self.assertIn("differs", result["reason"])


if __name__ == "__main__":
    unittest.main()
