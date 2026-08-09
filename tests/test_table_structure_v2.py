import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from finance_query.table_structure import parse_html_table, validate_structure_sidecar


ROOT = Path(__file__).parents[1]
script_spec = importlib.util.spec_from_file_location(
    "repair_review_bundle_tables",
    ROOT / "scripts" / "repair_review_bundle_tables.py",
)
repair = importlib.util.module_from_spec(script_spec)
script_spec.loader.exec_module(repair)


BALANCE_TABLE = """<table><tr><td></td><td>Mã số</td><td>Thuyết minh</td><td>31/12/2019 VND</td><td>1/1/2019 VND</td></tr><tr><td colspan="5">TÀI SẢN</td></tr><tr><td>Tài sản ngắn hạn</td><td>100</td><td></td><td>4.326</td><td>3.511</td></tr><tr><td>Tiền</td><td rowspan="2">111</td><td>5</td><td>181</td><td>126</td></tr><tr><td>Tiền khác</td><td></td><td>11</td><td>12</td></tr></table>"""


class TableStructureV2Tests(unittest.TestCase):
    def test_row_signature_recovers_income_statement_with_damaged_title(self):
        structure = parse_html_table(
            "<table>"
            "<tr><td>Mã số</td><td>Chỉ tiêu</td><td>2023</td><td>2022</td></tr>"
            "<tr><td>10</td><td>Doanh thu thuần</td><td>100</td><td>90</td></tr>"
            "<tr><td>20</td><td>Lợi nhuận gộp</td><td>20</td><td>18</td></tr>"
            "<tr><td>50</td><td>Lợi nhuận kế toán trước thuế</td><td>8</td><td>7</td></tr>"
            "</table>",
            context="Thuyết minh báo cáo tài chính — tiêu đề OCR bị mất",
        )
        self.assertEqual(structure["table_function"]["kind"], "income_statement")
        self.assertEqual(structure["table_function"]["specificity"], "structural")
        self.assertEqual(structure["table_section"]["kind"], "income_statement")

    def test_cash_flow_function_overrides_asset_words_in_transaction_rows(self):
        structure = parse_html_table(
            "<table><tr><td>Chỉ tiêu</td><td>2023</td></tr>"
            "<tr><td>Tiền chi mua sắm tài sản cố định</td><td>(100)</td></tr>"
            "<tr><td>Lưu chuyển tiền thuần từ hoạt động kinh doanh</td><td>500</td></tr>"
            "</table>",
            context="BÁO CÁO LƯU CHUYỂN TIỀN TỆ",
        )
        self.assertEqual(structure["table_function"]["kind"], "cash_flow_statement")
        self.assertEqual(structure["table_section"]["kind"], "cash_flow")

    def test_preserves_empty_cells_and_expands_spans(self):
        structure = parse_html_table(
            BALANCE_TABLE,
            context="Công ty ABC Băng cân đối kế toán hợp nhất",
        )
        self.assertEqual(
            structure["column_labels"],
            ["Nhãn dòng", "Mã số", "Thuyết minh", "31/12/2019 VND", "1/1/2019 VND"],
        )
        self.assertEqual(
            structure["rows"][2],
            ["Tài sản ngắn hạn", "100", "", "4.326", "3.511"],
        )
        self.assertEqual(structure["rows"][4], ["Tiền khác", "", "", "11", "12"])
        self.assertTrue(structure["cell_provenance"][4][1]["covered_by_span"])
        self.assertEqual(structure["table_function"]["kind"], "balance_sheet")
        self.assertEqual(structure["table_section"]["kind"], "asset")
        self.assertIn("empty_cells_preserved", structure["structure_quality"]["flags"])
        self.assertIn("span_cells_expanded", structure["structure_quality"]["flags"])

    def test_sidecar_verifies_bundle_uid_and_keeps_bundle_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            document = "MML_financial_statements_2019_consolidated"
            source = reports / "MML" / "2019" / document / f"{document}_extracted.txt"
            source.parent.mkdir(parents=True)
            raw = "report context Băng cân đối kế toán\n" + BALANCE_TABLE
            source.write_text(raw, encoding="utf-8")
            char_start = raw.index("<table>")
            table_hash = hashlib.sha256(BALANCE_TABLE.encode("utf-8")).hexdigest()
            uid = hashlib.sha256(
                f"{document}\x1f1\x1f{char_start}\x1f{table_hash}".encode("utf-8")
            ).hexdigest()
            bundle = root / "bundle"
            bundle.mkdir()
            table = {
                "internal_table_uid": uid,
                "document_id": document,
                "ticker": "MML",
                "report_year": 2019,
                "local_ordinal": 1,
                "context_before": "Băng cân đối kế toán hợp nhất",
                "rows": [["legacy", "misaligned"]],
            }
            (bundle / "tables.jsonl").write_text(json.dumps(table) + "\n", encoding="utf-8")
            output = bundle / "tables_structured_v2.jsonl"

            result = repair.repair_bundle_tables(bundle, reports, output)

            self.assertEqual(result["repaired_table_count"], 1)
            self.assertEqual(result["error_count"], 0)
            repaired = json.loads(output.read_text(encoding="utf-8").strip())
            self.assertEqual(repaired["internal_table_uid"], uid)
            self.assertEqual(repaired["rows"][2][2], "")
            self.assertEqual(repaired["rows"][2][3], "4.326")
            # The original bundle payload is deliberately not rewritten.
            original = json.loads((bundle / "tables.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(original["rows"], [["legacy", "misaligned"]])
            manifest = validate_structure_sidecar(bundle, output)
            self.assertEqual(manifest["repaired_table_count"], 1)

            output.write_text(output.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                validate_structure_sidecar(bundle, output)


if __name__ == "__main__":
    unittest.main()
