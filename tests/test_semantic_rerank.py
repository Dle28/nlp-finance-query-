import unittest

from finance_query.semantic_rerank import semantic_candidate_input, semantic_input_digest


class SemanticRerankTests(unittest.TestCase):
    def test_input_is_source_bounded_and_digest_is_stable(self):
        candidate = {
            "value_row_index": 1,
            "evidence_window": [{"index": 1, "row": ["Tiền", "100"]}],
            "direct_evidence": "UNTRUSTED PROJECTION MUST NOT REACH THE MODEL",
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
        self.assertIn("Dòng nguồn exact (V2): c0=Tiền | c1=100", value)
        self.assertIn("Cột nguồn: Chỉ tiêu | Số cuối năm", value)
        self.assertLess(value.index("Dòng nguồn exact"), value.index("Cột nguồn"))
        self.assertNotIn("UNTRUSTED PROJECTION", value)
        self.assertEqual(semantic_input_digest("q", value), semantic_input_digest("q", value))
        self.assertNotEqual(semantic_input_digest("q", value), semantic_input_digest("q2", value))

    def test_input_refuses_candidate_without_an_exact_v2_row(self):
        value = semantic_candidate_input(
            "Tiền cuối năm là bao nhiêu?",
            {"value_row_index": 0, "evidence_window": [{"index": 0, "row": ["sai", "100"]}]},
            {"rows": [["Tiền", "100"]]},
            {"canonical_headers": {"columns": []}},
        )
        self.assertEqual(value, "")

    def test_long_cells_are_explicitly_truncated_not_synthesized(self):
        value = semantic_candidate_input(
            "Q",
            {"value_row_index": 0, "evidence_window": [{"index": 0, "row": ["a" * 300, "100"]}]},
            {"rows": [["a" * 300, "100"]]},
            {"canonical_headers": {"columns": []}},
        )
        self.assertIn("c0=" + "a" * 119 + "…", value)
