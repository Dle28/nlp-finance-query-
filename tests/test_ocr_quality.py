import json
import unittest

from finance_query.evidence_context import build_evidence_context
from finance_query.ocr_quality import profile_ocr_quality


def table(rows):
    return {
        "internal_table_uid": "quality_table",
        "rows": rows,
        "header_row_indices": [0],
        "cell_provenance": [
            [
                {"anchor_row": row_index, "anchor_column": column_index, "covered_by_span": False}
                for column_index in range(len(rows[0]))
            ]
            for row_index in range(len(rows))
        ],
        "source_provenance": {},
    }


class OCRQualityTests(unittest.TestCase):
    def test_profile_is_observational_for_stable_table(self):
        source = table([
            ["Chỉ tiêu", "Năm 2023", "Năm 2022"],
            ["Tiền", "100", "90"],
            ["Hàng tồn kho", "50", "40"],
        ])
        profile = profile_ocr_quality(source, build_evidence_context(source))

        self.assertEqual(profile["triage"]["action"], "normal")
        self.assertEqual(profile["signals"]["numeric_column_alignment"]["dominant_numeric_columns"], [1, 2])
        self.assertFalse(profile["source_contract"]["evidence_eligible"])
        self.assertNotIn("100", json.dumps(profile, ensure_ascii=False))

    def test_fully_unreliable_numeric_table_is_quarantined_without_repair(self):
        source = table([
            ["Chỉ tiêu", "Năm 2023"],
            ["Chi phí tài chính", "(72.193.585.614)(27.471.160.925)"],
        ])
        profile = profile_ocr_quality(source, build_evidence_context(source))

        self.assertEqual(profile["triage"]["action"], "quarantine")
        self.assertEqual(profile["signals"]["reliable_numeric_cell_count"], 0)
        self.assertEqual(profile["signals"]["unreliable_cell_refs"], [{
            "row_index": 1,
            "column_index": 1,
            "warning_codes": ["multiple_numeric_groups"],
        }])

    def test_non_nested_layout_variation_requires_review_but_keeps_reliable_cells(self):
        source = table([
            ["Chỉ tiêu", "Năm 2023", "Năm 2022", "Năm 2021"],
            ["Tiền", "100", "90", ""],
            ["Dòng OCR lệch", "80", "", "70"],
        ])
        profile = profile_ocr_quality(source, build_evidence_context(source))

        self.assertEqual(profile["triage"]["action"], "review_required")
        self.assertIn("numeric_column_alignment_non_nested", profile["triage"]["reason_codes"])
        self.assertEqual(profile["signals"]["reliable_numeric_cell_count"], 4)

    def test_subset_numeric_layout_is_not_called_an_alignment_error(self):
        source = table([
            ["Chỉ tiêu", "Năm 2023", "Năm 2022"],
            ["Tiền", "100", "90"],
            ["Dòng chỉ có kỳ hiện tại", "80", ""],
        ])
        profile = profile_ocr_quality(source, build_evidence_context(source))

        self.assertEqual(profile["triage"]["action"], "normal")
        alignment = profile["signals"]["numeric_column_alignment"]
        self.assertEqual(alignment["subset_layout_row_indices"], [2])
        self.assertEqual(alignment["alignment_anomaly_row_indices"], [])
