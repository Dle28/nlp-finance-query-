import unittest
import json
import tempfile
from pathlib import Path

from finance_query.report_segments import (
    build_report_segment,
    validate_report_segment_sidecar,
)
from finance_query.table_structure import sha256_file


class ReportSegmentTests(unittest.TestCase):
    def test_segment_uses_current_page_heading_not_prior_table_content(self):
        table = {
            "internal_table_uid": "u1",
            "document_id": "ABC_financial_statements_2023_consolidated",
            "ticker": "ABC",
            "report_year": 2023,
            "scope": "consolidated",
            "local_ordinal": 4,
            "context_before": (
                "<table><tr><td>Old metric 999</td></tr></table>"
                "===== PAGE 7 ===== BÁO CÁO LƯU CHUYỂN TIỀN TỆ "
                "Cho năm tài chính kết thúc ngày 31/12/2023"
            ),
            "rows": [["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "100"]],
        }
        context = {
            "table_function": {"kind": "cash_flow_statement", "label": "Báo cáo lưu chuyển tiền tệ"},
            "table_section": {"kind": "cash_flow", "label": "Lưu chuyển tiền tệ"},
            "canonical_headers": {"columns": [{"period_labels": ["31/12/2023"], "unit_labels": ["VND"]}]},
        }
        segment = build_report_segment(table, context)
        self.assertIn("BÁO CÁO LƯU CHUYỂN TIỀN TỆ", segment["source_heading"])
        self.assertTrue(segment["source_heading"].startswith("BÁO CÁO"))
        self.assertNotIn("Old metric", segment["source_heading"])
        self.assertEqual(segment["period_labels"], ["31/12/2023"])
        self.assertEqual(
            segment["compact_descriptor"],
            "Báo cáo lưu chuyển tiền tệ · kỳ: 31/12/2023 · đơn vị: VND",
        )
        self.assertFalse(segment["evidence_eligible"])

    def test_segment_has_no_raw_row_or_numeric_value_summary(self):
        table = {
            "internal_table_uid": "u2",
            "context_before": "<p>12. Chi phí phạt</p>",
            "rows": [["Chi phí phạt (100 = 110 + 120)", "5.535.987.434"]],
        }
        segment = build_report_segment(table, {"canonical_headers": {"columns": []}})
        rendered = " ".join(str(value) for value in segment.values())
        self.assertNotIn("5.535.987.434", rendered)
        self.assertNotIn("100 = 110", rendered)
        self.assertNotIn("first_non_numeric_row_label", segment)

    def test_statement_heading_drops_entity_prefix_and_legal_boilerplate(self):
        table = {
            "internal_table_uid": "u3",
            "context_before": (
                "Công ty và các công ty con Bảng cân đối kế toán hợp nhất "
                "tại ngày 31/12/2023 Mẫu số B 01 - DN/HN "
                "(Ban hành theo Thông tư 200/2014/TT-BTC)"
            ),
        }
        segment = build_report_segment(table, {"canonical_headers": {"columns": []}})
        self.assertEqual(
            segment["source_heading"],
            "Bảng cân đối kế toán hợp nhất tại ngày 31/12/2023",
        )

    def test_numbered_heading_removes_immediately_repeated_subheading(self):
        table = {
            "internal_table_uid": "u4",
            "context_before": (
                "6. Các khoản đầu tư tài chính (a) Chứng khoán kinh doanh "
                "Chứng khoán kinh doanh phản ánh khoản đầu tư của Công ty"
            ),
        }
        segment = build_report_segment(table, {"canonical_headers": {"columns": []}})
        self.assertEqual(
            segment["source_heading"],
            "6. Các khoản đầu tư tài chính (a) Chứng khoán kinh doanh",
        )

    def test_segment_manifest_rejects_wrong_context_or_training_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            paths = {
                "tables.jsonl": "{}\n",
                "tables_structured_v2.jsonl": "{}\n",
                "tables_evidence_context_v3.jsonl": "{}\n",
                "report_segments_v1.jsonl": '{"internal_table_uid":"u1"}\n',
            }
            for name, contents in paths.items():
                (bundle / name).write_text(contents, encoding="utf-8")
            sidecar = bundle / "report_segments_v1.jsonl"
            manifest = {
                "schema_version": 1,
                "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
                "structured_tables_sha256": sha256_file(bundle / "tables_structured_v2.jsonl"),
                "evidence_context_file": "tables_evidence_context_v3.jsonl",
                "evidence_context_sha256": sha256_file(bundle / "tables_evidence_context_v3.jsonl"),
                "segment_count": 1,
                "evidence_eligible": False,
                "training_eligible": False,
                "sidecar_sha256": sha256_file(sidecar),
            }
            sidecar.with_suffix(".manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            self.assertEqual(
                validate_report_segment_sidecar(bundle, sidecar)["segment_count"], 1
            )
            manifest["training_eligible"] = True
            sidecar.with_suffix(".manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "non-evidence"):
                validate_report_segment_sidecar(bundle, sidecar)


if __name__ == "__main__":
    unittest.main()
