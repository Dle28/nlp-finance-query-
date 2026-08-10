import unittest

from finance_query.evidence_context import build_evidence_context
from finance_query.semantic_catalog import build_semantic_catalog_entry


def structure(function):
    return {
        "internal_table_uid": "semantic_table",
        "rows": [["Chỉ tiêu", "Năm 2023", "Năm 2022"], ["Tiền", "100", "90"]],
        "header_row_indices": [0],
        "cell_provenance": [
            [
                {"anchor_row": row_index, "anchor_column": column_index, "covered_by_span": False}
                for column_index in range(3)
            ]
            for row_index in range(2)
        ],
        "table_function": function,
        "table_section": {"kind": "unknown"},
        "source_provenance": {},
    }


class SemanticCatalogTests(unittest.TestCase):
    def test_primary_statement_is_observed_from_existing_function_only(self):
        source = structure({"kind": "cash_flow_statement", "specificity": "structural"})
        entry = build_semantic_catalog_entry(
            source,
            build_evidence_context(source),
            {"internal_table_uid": "semantic_table", "source_heading_kind": "statement_heading"},
        )
        self.assertEqual(entry["document_role"], "primary_financial_statement")
        self.assertEqual(entry["statement_family"], "cash_flow_statement")
        self.assertEqual(entry["note_hierarchy"]["path"], [])
        self.assertFalse(entry["source_contract"]["evidence_eligible"])

    def test_numbered_note_path_comes_only_from_source_heading(self):
        source = structure({"kind": "financial_note", "specificity": "broad"})
        entry = build_semantic_catalog_entry(
            source,
            build_evidence_context(source),
            {
                "internal_table_uid": "semantic_table",
                "source_heading_kind": "numbered_heading",
                "source_heading": "6.2. Chứng khoán kinh doanh",
            },
        )
        self.assertEqual(entry["document_role"], "financial_note")
        self.assertEqual(entry["note_hierarchy"]["path"], ["6", "6.2"])
        self.assertEqual(entry["layout"]["kind"], "multi_column_numeric_table")

    def test_generic_data_schedule_without_source_note_stays_unclassified(self):
        source = structure({"kind": "financial_data_schedule", "specificity": "generic"})
        entry = build_semantic_catalog_entry(
            source,
            build_evidence_context(source),
            {"internal_table_uid": "semantic_table", "source_heading_kind": "none"},
        )
        self.assertEqual(entry["document_role"], "unclassified")
        self.assertEqual(entry["statement_family"], "")

