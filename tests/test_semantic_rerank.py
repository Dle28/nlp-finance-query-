import unittest

from finance_query.semantic_rerank import semantic_candidate_input, semantic_input_digest


class SemanticRerankTests(unittest.TestCase):
    def test_input_is_source_bounded_and_digest_is_stable(self):
        candidate = {
            "structure_validation": {"row_index": 1},
            "direct_evidence": "VALUE: Tiền | 100",
        }
        table = {
            "unit_hint": "VND",
            "rows": [["", "Số cuối năm"], ["Tiền", "100"]],
            "context_trace": {"source_title": "Bảng cân đối", "unit_labels": ["VND"]},
        }
        context = {
            "context_trace": {"source_title": "Bảng cân đối", "unit_labels": ["VND"]},
            "table_function": {"label": "balance_sheet"},
            "canonical_headers": {"columns": [{"source_label": "Chỉ tiêu"}, {"source_label": "Số cuối năm"}]},
        }
        value = semantic_candidate_input("Tiền cuối năm là bao nhiêu?", candidate, table, context)
        self.assertIn("Dòng nguồn exact: Tiền | 100", value)
        self.assertIn("Cột nguồn: Chỉ tiêu | Số cuối năm", value)
        self.assertEqual(semantic_input_digest("q", value), semantic_input_digest("q", value))
        self.assertNotEqual(semantic_input_digest("q", value), semantic_input_digest("q2", value))
