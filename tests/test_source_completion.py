import tempfile
import unittest
from pathlib import Path

from finance_query.source_completion import raw_source_candidates, source_report_index


class SourceCompletionTests(unittest.TestCase):
    def test_raw_income_table_is_audited_but_not_promoted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = "ABC_financial_statements_2023_consolidated"
            path = root / "ABC" / "2023" / document / f"{document}_extracted.txt"
            path.parent.mkdir(parents=True)
            path.write_text(
                """===== PAGE 1 =====
<table><tr><td>Chỉ tiêu</td><td>Năm 2023</td></tr>
<tr><td>Doanh thu thuần</td><td>100</td></tr>
<tr><td>Lợi nhuận gộp</td><td>30</td></tr>
<tr><td>Lợi nhuận kế toán trước thuế</td><td>20</td></tr>
<tr><td>Lợi nhuận sau thuế thu nhập doanh nghiệp</td><td>16</td></tr></table>""",
                encoding="utf-8",
            )
            finding, candidates = raw_source_candidates(
                {
                    "entity": "ABC",
                    "years": [2023],
                    "metric_hints": ["lợi nhuận sau thuế"],
                    "allowed_table_functions": ["income_statement"],
                },
                reports_root=root,
                reports_by_ticker_year=source_report_index(root),
                bundled_uids=set(),
            )
            self.assertEqual(finding, "raw_source_present_not_in_bundle")
            self.assertEqual(len(candidates), 1)
            self.assertFalse(candidates[0]["already_in_immutable_bundle"])
            self.assertEqual(candidates[0]["table_function"]["kind"], "income_statement")
            self.assertEqual(candidates[0]["matching_rows"][0]["row_index"], 4)

    def test_source_audit_rejects_table_without_expected_statement_function(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = "ABC_financial_statements_2023_separate"
            path = root / "ABC" / "2023" / document / f"{document}_extracted.txt"
            path.parent.mkdir(parents=True)
            path.write_text(
                "<table><tr><td>Lợi nhuận sau thuế</td><td>16</td></tr></table>",
                encoding="utf-8",
            )
            finding, candidates = raw_source_candidates(
                {
                    "entity": "ABC",
                    "years": [2023],
                    "metric_hints": ["lợi nhuận sau thuế"],
                    "allowed_table_functions": ["income_statement"],
                },
                reports_root=root,
                reports_by_ticker_year=source_report_index(root),
                bundled_uids=set(),
            )
            self.assertEqual(finding, "raw_metric_or_statement_not_found")
            self.assertEqual(candidates, [])
