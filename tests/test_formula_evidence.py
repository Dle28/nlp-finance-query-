import unittest

from finance_query.evidence_context import build_evidence_context
from finance_query.formula_evidence import formula_evidence_set, operand_evidence_matches


def _table() -> dict:
    return {
        "internal_table_uid": "u1",
        "document_id": "ABC_financial_statements_2023_separate",
        "ticker": "ABC",
        "rows": [
            ["Chỉ tiêu", "Năm 2023", "Năm 2022"],
            ["Tài sản ngắn hạn", "100", "90"],
            ["Nợ ngắn hạn", "50", "45"],
        ],
        "header_row_indices": [0],
        "cell_provenance": [
            [
                {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                for column in range(3)
            ]
            for row in range(3)
        ],
        "source_provenance": {},
    }


class FormulaEvidenceTests(unittest.TestCase):
    def test_operand_binds_exact_row_and_explicit_year_cell(self):
        table = _table()
        context = build_evidence_context(table)
        operand = {
            "operand_id": "assets",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2023],
            "required": True,
        }
        candidate = {"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}
        matches = operand_evidence_matches(operand, candidate, table, context)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["row_index"], 1)
        self.assertEqual(matches[0]["binding"]["column_index"], 1)
        self.assertEqual(matches[0]["binding"]["raw_value"], "100")

    def test_missing_year_is_not_implicitly_bound(self):
        table = _table()
        context = build_evidence_context(table)
        operand = {
            "operand_id": "assets",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2021],
            "required": True,
        }
        candidate = {"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}
        self.assertEqual(operand_evidence_matches(operand, candidate, table, context), [])

    def test_set_stays_partial_when_a_required_operand_has_no_exact_cell(self):
        table = _table()
        context = build_evidence_context(table)
        formula = {
            "formula_id": "ratio",
            "operands": [
                {"operand_id": "assets", "metric_hints": ["tài sản ngắn hạn"], "years": [2023], "required": True},
                {"operand_id": "inventory", "metric_hints": ["hàng tồn kho"], "years": [2023], "required": True},
            ],
        }
        item = {
            "id": 1,
            "question": "test",
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}],
        }
        evidence = formula_evidence_set(formula, item, {"u1": table}, {"u1": context})
        self.assertEqual(evidence["evidence_completeness"], "partial")
        self.assertEqual(evidence["missing_operand_ids"], ["inventory"])

