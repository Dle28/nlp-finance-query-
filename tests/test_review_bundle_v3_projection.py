import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "bundle_v3",
    Path(__file__).parents[1] / "scripts" / "build_review_bundle_v3.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReviewBundleV3ProjectionTests(unittest.TestCase):
    def test_snapshot_revision_is_explicit_and_fails_fast_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {"VIFINQA_SOURCE_REVISION": "snapshot-abc"}):
                self.assertEqual(mod.source_revision(root), "snapshot-abc")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "VIFINQA_SOURCE_REVISION"):
                    mod.source_revision(root)

    def test_direct_lookup_effective_metric_strips_entity(self):
        q = (
            "Giá trị còn lại của bất động sản đầu tư của công ty mẹ IJC "
            "đến ngày 31 tháng 12 năm 2021 là bao nhiêu tỷ đồng?"
        )
        plan = {
            "family": "direct_lookup",
            "tickers": ["IJC"],
            "operands": [
                {
                    "metric": (
                        "Giá trị còn lại của bất động sản đầu tư IJC "
                        "31 tháng 12 năm 2021"
                    )
                }
            ],
        }
        self.assertEqual(
            mod.effective_metric(q, plan, "direct_lookup"),
            "Giá trị còn lại của bất động sản đầu tư",
        )

    def test_heading_keeps_start_of_current_page_title(self):
        context = (
            "noise from previous page ===== PAGE 6 ===== "
            "Công ty Cổ phần Masan MEATLife Băng cân đối kế toán hợp nhất "
            "tại ngày 31 tháng 12 năm 2019"
        )
        heading = mod.heading(context)
        self.assertTrue(heading.startswith("Công ty Cổ phần Masan MEATLife"))
        self.assertIn("Băng cân đối kế toán", heading)

    def test_period_aware_projection_pairs_table_header_and_value(self):
        rows = [
            ["Nguyên giá", "Hao mòn lũy kế", "Giá trị còn lại"],
            [
                "Số đầu năm",
                "385.187.149.316",
                "31.197.426.846",
                "353.989.722.470",
            ],
            ["Kết chuyển từ hàng tồn kho sang (*)", "32.673.139.654"],
            ["Khẩu hao trong năm", "8.105.920.291"],
            [
                "Số cuối năm",
                "417.860.288.970",
                "39.303.347.137",
                "378.556.941.833",
            ],
        ]
        q = (
            "Giá trị còn lại của bất động sản đầu tư của công ty mẹ IJC "
            "đến ngày 31 tháng 12 năm 2021 là bao nhiêu tỷ đồng?"
        )
        plan = {
            "family": "direct_lookup",
            "operands": [{"metric": "Giá trị còn lại của bất động sản đầu tư"}],
        }
        out = mod.projection(
            rows,
            "</table> 10. Bất động sản đầu tư",
            q,
            plan,
            "Giá trị còn lại của bất động sản đầu tư",
        )
        self.assertEqual(out["value_row_index"], 4)
        self.assertEqual(out["best_row_index"], 4)
        self.assertIn("Bất động sản đầu tư", out["direct_evidence"])
        self.assertIn("Giá trị còn lại", out["direct_evidence"])
        self.assertIn("378.556.941.833", out["direct_evidence"])
        self.assertTrue(out["evidence_features"]["numeric"])

    def test_table_heading_metric_uses_total_row(self):
        rows = [
            ["", "Số cuối năm", "Số đầu năm"],
            ["Tiền mặt tại quỹ", "437.903.500", "58.081.504"],
            ["Tiền gửi ngân hàng", "180.174.387.729", "82.021.502.584"],
            [
                "Các khoản tương đương tiền (*)",
                "1.700.000.000.000",
                "6.324.000.000.000",
            ],
            ["TỔNG CỘNG", "1.880.612.291.229", "6.406.079.584.088"],
        ]
        q = (
            "Tiền và các khoản tương đương tiền của công ty mẹ SAB "
            "vào cuối năm 2016 là bao nhiêu tỷ đồng?"
        )
        plan = {
            "family": "direct_lookup",
            "operands": [{"metric": "Tiền và các khoản tương đương tiền"}],
        }
        out = mod.projection(
            rows,
            "</table> 4. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN",
            q,
            plan,
            "Tiền và các khoản tương đương tiền",
        )
        self.assertEqual(out["value_row_index"], 4)
        self.assertIn("TỔNG CỘNG", out["direct_evidence"])
        self.assertIn("1.880.612.291.229", out["direct_evidence"])

    def test_formula_support_adds_only_explicit_entity_year_statement_function(self):
        formula = {
            "formula_id": "controlled_ratio",
            "operands": [
                {
                    "operand_id": "assets_2022",
                    "entity": "ABC",
                    "years": [2022],
                    "allowed_table_functions": ["balance_sheet"],
                },
                {
                    "operand_id": "profit_2022",
                    "entity": "ABC",
                    "years": [2022],
                    "allowed_table_functions": ["income_statement"],
                },
                # A generic slot must not cause every note for ABC to be
                # exported as formula context.
                {"operand_id": "untyped", "entity": "ABC", "years": [2022]},
            ],
        }
        plan = {"tickers": ["ABC"]}

        def asset(uid, year, kind, ticker="ABC"):
            return {
                "uid": uid,
                "ticker": ticker,
                "scope": "consolidated",
                "report_year": year,
                "document_id": f"{ticker}_{year}",
                "local_ordinal": 1,
                "table_function_json": {"kind": kind},
            }

        selected, available = mod.formula_support_assets(
            formula,
            plan,
            [
                asset("balance-2022", 2022, "balance_sheet"),
                asset("income-2023", 2023, "income_statement"),
                asset("note-2022", 2022, "financial_note_detail"),
                asset("other-company", 2022, "balance_sheet", "XYZ"),
            ],
            max_tables=8,
        )
        self.assertEqual(available, 2)
        self.assertEqual(
            [entry["asset"]["uid"] for entry in selected],
            ["balance-2022", "income-2023"],
        )
        self.assertEqual(selected[0]["operand_ids"], ["assets_2022"])
        self.assertEqual(selected[1]["operand_ids"], ["profit_2022"])

    def test_formula_support_marks_table_without_changing_review_candidates(self):
        asset = {
            "uid": "support-u1",
            "document_id": "ABC_2022",
            "ticker": "ABC",
            "scope": "consolidated",
            "report_year": 2022,
            "headers_json": "[]",
            "rows_json": "[]",
            "table_function_json": {"kind": "balance_sheet"},
        }
        cache = {}
        _, was_added = mod.add_formula_support_table(
            cache,
            {"asset": asset, "operand_ids": ["assets_2022"]},
            question_id=10,
            formula_id="controlled_ratio",
        )
        self.assertTrue(was_added)
        marker = cache["support-u1"]["bundle_inclusion"]["formula_metadata_support"]
        self.assertEqual(marker["question_ids"], [10])
        self.assertEqual(marker["operand_ids"], ["assets_2022"])

    def test_direct_support_requires_literal_metric_phrase_not_fuzzy_note_text(self):
        plan = {"family": "direct_lookup", "tickers": ["ABC"], "years": [2022]}

        def asset(uid, rows):
            return {
                "uid": uid,
                "ticker": "ABC",
                "scope": "consolidated",
                "report_year": 2022,
                "document_id": "ABC_2022",
                "local_ordinal": 1,
                "rows_json": json.dumps(rows),
            }

        selected, available = mod.direct_support_assets(
            plan,
            "Chi phí phạt",
            [
                asset("exact", [["Chi phí phạt", "100"]]),
                asset("nearby", [["Chi phí phạt vi phạm hợp đồng", "100"]]),
                asset("note", [["Thuyết minh về chi phí phạt", "100"]]),
            ],
            max_tables=8,
        )
        self.assertEqual(available, 1)
        self.assertEqual([value["asset"]["uid"] for value in selected], ["exact"])

    def test_direct_support_marks_table_as_ui_invisible_provenance(self):
        asset = {
            "uid": "direct-u1",
            "document_id": "ABC_2022",
            "ticker": "ABC",
            "scope": "consolidated",
            "report_year": 2022,
            "headers_json": "[]",
            "rows_json": "[]",
        }
        cache = {}
        _, was_added = mod.add_direct_support_table(
            cache,
            {"asset": asset, "matching_row_indices": [4]},
            question_id=11,
            metric="Chi phí phạt",
        )
        self.assertTrue(was_added)
        marker = cache["direct-u1"]["bundle_inclusion"]["direct_metadata_support"]
        self.assertEqual(marker["question_ids"], [11])
        self.assertEqual(marker["matching_row_indices"], [4])


if __name__ == "__main__":
    unittest.main()
