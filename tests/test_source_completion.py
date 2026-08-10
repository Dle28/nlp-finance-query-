import tempfile
import unittest
from pathlib import Path

from finance_query.source_completion import (
    operand_requires_scope_gap_probe,
    revalidate_raw_source_candidate,
    raw_source_candidates,
    source_completion_manifest_path,
    source_report_index,
)


class SourceCompletionTests(unittest.TestCase):
    def test_scope_gap_probe_is_limited_to_explicit_staged_multi_entity_programs(self):
        evidence = {
            "formula": {
                "execution_status": "stage_binding_required",
                "entities": ["AAA", "BBB"],
            }
        }
        self.assertTrue(operand_requires_scope_gap_probe(evidence, {"entity": "AAA"}))
        self.assertFalse(operand_requires_scope_gap_probe(evidence, {"entity": "CCC"}))
        self.assertFalse(
            operand_requires_scope_gap_probe(
                {"formula": {"entities": ["AAA", "BBB"]}}, {"entity": "AAA"}
            )
        )

    def test_versioned_completion_snapshot_keeps_a_distinct_manifest(self):
        bundle = Path("/tmp/review_bundle")
        self.assertEqual(
            source_completion_manifest_path(bundle / "source_completion_tables_v1.jsonl"),
            bundle / "source_completion_v1.manifest.json",
        )
        self.assertEqual(
            source_completion_manifest_path(bundle / "source_completion_balance_codes_v1.jsonl"),
            bundle / "source_completion_balance_codes_v1.manifest.json",
        )

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
            table, context = revalidate_raw_source_candidate(
                candidates[0], reports_root=root
            )
            self.assertEqual(table["source_completion"]["protocol"], "raw_source_completion_v1")
            self.assertFalse(table["source_completion"]["answer_eligible"])
            self.assertEqual(context["quality"]["status"], "review_ready")

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

    def test_raw_balance_sheet_fragment_with_standard_codes_is_source_auditable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = "ABC_financial_statements_2022_consolidated"
            path = root / "ABC" / "2022" / document / f"{document}_extracted.txt"
            path.parent.mkdir(parents=True)
            path.write_text(
                """<table><tr><td>Chỉ tiêu</td><td>Mã số</td><td>31/12/2022 VND</td></tr>
<tr><td>Tài sản ngắn hạn</td><td>100</td><td>1.000</td></tr>
<tr><td>Tiền và các khoản tương đương tiền</td><td>110</td><td>100</td></tr>
<tr><td>Các khoản phải thu ngắn hạn</td><td>130</td><td>200</td></tr>
<tr><td>Hàng tồn kho</td><td>140</td><td>300</td></tr></table>""",
                encoding="utf-8",
            )
            finding, candidates = raw_source_candidates(
                {
                    "entity": "ABC",
                    "years": [2022],
                    "metric_hints": ["hàng tồn kho"],
                    "allowed_table_functions": ["balance_sheet"],
                },
                reports_root=root,
                reports_by_ticker_year=source_report_index(root),
                bundled_uids=set(),
            )
            self.assertEqual(finding, "raw_source_present_not_in_bundle")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["table_function"]["specificity"], "structural")
            self.assertEqual(candidates[0]["matching_rows"][0]["row_index"], 4)

    def test_omitted_scope_sibling_is_not_hidden_by_bundled_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = [
                "ABC_financial_statements_2022_consolidated",
                "ABC_financial_statements_2022_separate",
            ]
            for document in documents:
                path = root / "ABC" / "2022" / document / f"{document}_extracted.txt"
                path.parent.mkdir(parents=True)
                path.write_text(
                    """<table><tr><td>Chỉ tiêu</td><td>Mã số</td><td>31/12/2022 VND</td></tr>
<tr><td>Tài sản ngắn hạn</td><td>100</td><td>1000</td></tr>
<tr><td>Tiền và các khoản tương đương tiền</td><td>110</td><td>100</td></tr>
<tr><td>Các khoản phải thu ngắn hạn</td><td>130</td><td>200</td></tr>
<tr><td>Hàng tồn kho</td><td>140</td><td>300</td></tr></table>""",
                    encoding="utf-8",
                )
            index = source_report_index(root)
            _, all_candidates = raw_source_candidates(
                {
                    "entity": "ABC",
                    "years": [2022],
                    "metric_hints": ["hàng tồn kho"],
                    "allowed_table_functions": ["balance_sheet"],
                },
                reports_root=root,
                reports_by_ticker_year=index,
                bundled_uids=set(),
            )
            bundled = {
                candidate["raw_table_uid"]
                for candidate in all_candidates
                if candidate["scope"] == "separate"
            }
            finding, candidates = raw_source_candidates(
                {
                    "entity": "ABC",
                    "years": [2022],
                    "metric_hints": ["hàng tồn kho"],
                    "allowed_table_functions": ["balance_sheet"],
                },
                reports_root=root,
                reports_by_ticker_year=index,
                bundled_uids=bundled,
            )
            self.assertEqual(finding, "raw_source_present_not_in_bundle")
            self.assertEqual({candidate["scope"] for candidate in candidates}, {"consolidated", "separate"})
            self.assertEqual(sum(not candidate["already_in_immutable_bundle"] for candidate in candidates), 1)
