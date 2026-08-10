import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "review_bundle_widget",
    Path(__file__).parents[1] / "local" / "review_bundle_widget.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReviewBundleWidgetFormatTests(unittest.TestCase):
    def test_legacy_positive_annotation_requires_v2_refresh(self):
        self.assertTrue(
            mod.annotation_needs_structure_refresh(
                {
                    "annotation_status": "human_verified",
                    "positive_table_uids": ["u1"],
                }
            )
        )
        self.assertFalse(
            mod.annotation_needs_structure_refresh(
                {
                    "annotation_status": "human_verified",
                    "positive_table_uids": ["u1"],
                    "structure_validation": {"complete": True},
                }
            )
        )

    def test_evidence_parts_preserve_exact_exported_rows(self):
        direct = (
            "TABLE: Báo cáo lưu chuyển tiền tệ || "
            "COLUMNS: Chỉ tiêu | Mã số | Năm nay | Năm trước || "
            "VALUE: Lưu chuyển tiền thuần từ hoạt động kinh doanh | 20 | "
            "1.448.611.560.410 | 1.223.996.483.808"
        )
        self.assertEqual(
            mod.evidence_parts(direct),
            [
                ("Ngữ cảnh bảng", "Báo cáo lưu chuyển tiền tệ"),
                ("Dòng tiêu đề/cột", "Chỉ tiêu | Mã số | Năm nay | Năm trước"),
                (
                    "Dòng giá trị",
                    "Lưu chuyển tiền thuần từ hoạt động kinh doanh | 20 | "
                    "1.448.611.560.410 | 1.223.996.483.808",
                ),
            ],
        )

    def test_report_context_uses_clean_projected_heading(self):
        table = {"context_before": "<table><tr><td>noise</td></tr></table> fallback"}
        candidate = {
            "context_heading": "BÁO CÁO LƯU CHUYỂN TIỀN TỆ <b>năm 2021</b>"
        }
        self.assertEqual(
            mod.report_context_text(table, candidate),
            "BÁO CÁO LƯU CHUYỂN TIỀN TỆ năm 2021",
        )

    def test_report_context_prefers_normalized_segment_over_projected_heading(self):
        self.assertEqual(
            mod.report_context_text(
                {"report_segment": {"source_heading": "5. Chi phí phạt"}},
                {"context_heading": "OCR heading không đúng"},
            ),
            "5. Chi phí phạt",
        )

    def test_report_context_uses_descriptor_when_reader_title_is_ambiguous(self):
        self.assertEqual(
            mod.report_context_text(
                {
                    "report_segment": {
                        "reader_heading": "",
                        "compact_descriptor": "Thuyết minh báo cáo tài chính · kỳ: 2023",
                        "source_heading": "Một đoạn OCR dài không đủ chắc là tiêu đề",
                    }
                },
                {"context_heading": "Nguồn cũ"},
            ),
            "Thuyết minh báo cáo tài chính · kỳ: 2023",
        )

    def test_summary_uses_metadata_and_exact_value_row(self):
        candidate = {
            "document_id": "QNS_financial_statements_2021_separate",
            "report_year": 2021,
            "scope": "separate",
            "direct_evidence": (
                "VALUE: Lưu chuyển tiền thuần từ hoạt động kinh doanh | 20 | "
                "1.448.611.560.410 | 1.223.996.483.808"
            ),
        }
        summary = mod.candidate_summary(candidate)
        self.assertIn("QNS_financial_statements_2021_separate · 2021 · separate", summary)
        self.assertIn("1.448.611.560.410 | 1.223.996.483.808", summary)

    def test_semantic_mismatch_hides_value_summary(self):
        table = {
            "table_function": {"kind": "balance_sheet", "label": "Bảng cân đối kế toán"},
            "table_section": {"kind": "asset", "label": "Tài sản"},
            "structure_quality": {"status": "reconstructed_from_raw_html"},
        }
        candidate = {
            "document_id": "MML_financial_statements_2019_consolidated",
            "report_year": 2019,
            "scope": "consolidated",
            "direct_evidence": "VALUE: Tài sản ngắn hạn | 4.326",
        }
        question = "Tổng nợ ngắn hạn của MML là bao nhiêu?"
        assessment = mod.relevance_assessment(table, question)
        summary = mod.candidate_summary(candidate, table, question)
        self.assertEqual(assessment["status"], "mismatch")
        self.assertIn("Câu hỏi cần phần Nợ phải trả", summary)
        self.assertNotIn("dòng candidate", summary)

    def test_cash_flow_function_is_not_rejected_by_asset_false_positive(self):
        table = {
            "table_function": {
                "kind": "cash_flow_statement",
                "label": "Báo cáo lưu chuyển tiền tệ",
            },
            "table_section": {
                "kind": "asset",
                "label": "Tài sản",
                "matched_evidence": "tài sản cố định",
            },
        }
        question = "Năm nào có lưu chuyển tiền thuần từ hoạt động kinh doanh cao nhất?"
        assessment = mod.relevance_assessment(table, question)
        self.assertEqual(assessment["status"], "not_blocked")
        self.assertEqual(assessment["label"], "Đúng chức năng bảng")

    def test_aligned_table_uses_v2_column_labels(self):
        rendered = mod.aligned_table_html(
            [["", "Mã số", "Thuyết minh"], ["Tài sản", "100", ""]],
            "Tài sản",
            {"best_row_index": 1},
            5,
            column_labels=["Nhãn dòng", "Mã số", "Thuyết minh"],
            header_row_indices=[0],
            structure_quality={"status": "reconstructed_from_raw_html"},
        )
        self.assertIn("Bảng nguồn đã tái dựng (V2) từ raw HTML", rendered)
        self.assertIn(">Mã số</th>", rendered)
        self.assertNotIn(">c0</th>", rendered)

    def test_compact_preview_uses_semantic_labels_and_limits_rows(self):
        rendered = mod.compact_source_table_html(
            [
                ["", "Mã số", "2022", "2021"],
                ["Nợ ngắn hạn", "310", "120", "100"],
                ["Vốn chủ sở hữu", "400", "80", "70"],
                ["Dòng nhiễu 1", "", "1", "2"],
                ["Dòng nhiễu 2", "", "3", "4"],
                ["Dòng nhiễu 3", "", "5", "6"],
            ],
            "Tỷ lệ nợ ngắn hạn trên vốn chủ sở hữu năm 2022",
            {"best_row_index": 1},
            column_labels=["Chỉ tiêu", "Mã số", "2022", "2021"],
            header_row_indices=[0],
            max_rows=2,
        )
        self.assertIn(">Chỉ tiêu</th>", rendered)
        self.assertIn(">2022</th>", rendered)
        self.assertNotIn(">c0</th>", rendered)
        self.assertLessEqual(rendered.count("<tr"), 3)  # header + at most two data rows

    def test_formula_coverage_keeps_exact_rows_and_column_labels(self):
        formula = mod.infer_formula_spec(
            "Tỷ lệ nợ ngắn hạn trên vốn chủ sở hữu của VNM năm 2022 là bao nhiêu %?"
        )
        uid = "table-balance-sheet"
        candidate = {
            "rank": 1,
            "internal_table_uid": uid,
            "report_year": 2022,
            "direct_evidence": "VALUE: Nợ ngắn hạn | 120",
        }
        table = {
            "rows": [
                ["Nợ ngắn hạn", "310", "120"],
                ["Vốn chủ sở hữu", "400", "80"],
            ],
            "column_labels": ["Chỉ tiêu", "Mã số", "31/12/2022"],
            "context_trace": {"period_labels": ["31/12/2022"]},
            "structure_quality": {"status": "reconstructed_from_raw_html"},
        }
        coverage = mod.formula_coverage(formula, [candidate], {uid: table})
        self.assertTrue(coverage["complete"])
        numerator = coverage["operands"]["numerator"][0]["source_rows"][0]
        denominator = coverage["operands"]["denominator"][0]["source_rows"][0]
        self.assertEqual(numerator["row_index"], 0)
        self.assertEqual(numerator["row"][0], "Nợ ngắn hạn")
        self.assertEqual(denominator["row_index"], 1)
        self.assertEqual(denominator["column_labels"][2], "31/12/2022")

    def test_formula_coverage_is_not_complete_on_legacy_grid(self):
        formula = mod.infer_formula_spec(
            "Tỷ lệ nợ ngắn hạn trên vốn chủ sở hữu của VNM năm 2022 là bao nhiêu %?"
        )
        uid = "legacy-table"
        candidate = {
            "rank": 1,
            "internal_table_uid": uid,
            "report_year": 2022,
            "direct_evidence": "VALUE: Nợ ngắn hạn | Vốn chủ sở hữu | 120 | 80",
        }
        table = {
            "rows": [["Nợ ngắn hạn", "120"], ["Vốn chủ sở hữu", "80"]],
            "column_labels": ["Chỉ tiêu", "31/12/2022"],
            "context_trace": {"period_labels": ["31/12/2022"]},
            "structure_quality": {"status": "legacy_bundle_rows"},
        }
        coverage = mod.formula_coverage(formula, [candidate], {uid: table})
        self.assertFalse(coverage["complete"])
        self.assertFalse(
            coverage["operands"]["numerator"][0]["structure_validated"]
        )

    def test_multi_operand_formula_does_not_reject_equity_support_table(self):
        question = (
            "Tỷ lệ nợ ngắn hạn trên vốn chủ sở hữu của VNM năm 2022 "
            "là bao nhiêu %?"
        )
        formula = mod.infer_formula_spec(question)
        candidate = {
            "rank": 2,
            "internal_table_uid": "equity-table",
            "report_year": 2022,
            "direct_evidence": "VALUE: Vốn chủ sở hữu | 80",
        }
        table = {
            "rows": [["Vốn chủ sở hữu", "400", "80"]],
            "column_labels": ["Chỉ tiêu", "Mã số", "31/12/2022"],
            "context_trace": {"period_labels": ["31/12/2022"]},
            "table_function": {"kind": "balance_sheet", "label": "Bảng cân đối kế toán"},
            "table_section": {"kind": "equity", "label": "Vốn chủ sở hữu"},
        }
        assessment = mod.relevance_assessment(table, question, formula, candidate)
        self.assertEqual(assessment["status"], "not_blocked")
        self.assertIn("Vốn chủ sở hữu", assessment["reason"])

    def test_staged_segment_operand_requires_total_column(self):
        formula = mod.infer_formula_spec(
            "Trong nhóm HPG, HSG, MSR và NKG, xét các công ty có hệ số "
            "thanh toán nhanh năm 2022 thấp hơn trung vị. Công ty có mức "
            "thay đổi biên lợi nhuận gộp cao nhất từ năm 2022 sang năm 2023 "
            "có hệ số khả năng thanh toán lãi vay năm 2023 là bao nhiêu lần?"
        )
        candidate = {
            "rank": 1,
            "internal_table_uid": "segment-note",
            "ticker": "HPG",
            "report_year": 2022,
            "direct_evidence": "VALUE: Lợi nhuận gộp | 100",
        }
        table = {
            "rows": [["Lợi nhuận gộp", "100"]],
            "column_labels": ["Chỉ tiêu", "2022"],
            "context_trace": {"period_labels": ["2022"]},
            "table_function": {
                "kind": "segment_reporting",
                "label": "Bảng thông tin bộ phận",
            },
        }
        self.assertEqual(
            mod.candidate_operand_matches(formula, candidate, table),
            [],
        )

    def test_scattered_context_tokens_cannot_bind_unrelated_value_row(self):
        formula = mod.infer_formula_spec(
            "Trong nhóm HPG, HSG, MSR và NKG, xét các công ty có hệ số "
            "thanh toán nhanh năm 2022 thấp hơn trung vị. Công ty có mức "
            "thay đổi biên lợi nhuận gộp cao nhất từ năm 2022 sang năm 2023 "
            "có hệ số khả năng thanh toán lãi vay năm 2023 là bao nhiêu lần?"
        )
        candidate = {
            "rank": 14,
            "internal_table_uid": "receivables-note",
            "ticker": "HPG",
            "report_year": 2023,
            "best_row_index": 1,
            "context_heading": "Chi tiết khoản phải thu từ cho vay hưởng lãi suất",
            "direct_evidence": "VALUE: Phải thu khác | 100",
        }
        table = {
            "rows": [["", "2023"], ["Phải thu khác", "100"]],
            "column_labels": ["Chỉ tiêu", "2023"],
            "context_trace": {
                "topic": {"label": "8.1. Phải thu ngắn hạn khác"},
                "period_labels": ["2023"],
                "source_title": "Chi tiết khoản cho vay hưởng lãi suất",
            },
            "table_function": {
                "kind": "financial_note_detail",
                "label": "Bảng chi tiết thuyết minh",
            },
        }
        matches = mod.candidate_operand_matches(formula, candidate, table)
        self.assertNotIn(
            "hpg_interest_expense_2023",
            [match["operand_id"] for match in matches],
        )

    def test_context_trace_prefers_exact_numbered_topic(self):
        trace = mod.context_trace(
            {
                "context_trace": {
                    "source_title": "BÁO CÁO TÀI CHÍNH năm 2022 và đoạn nguồn dài",
                    "topic": {"label": "4. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN"},
                }
            }
        )
        self.assertEqual(
            trace["topic_label"],
            "4. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN",
        )

    def test_context_trace_prefers_hash_bound_normalized_segment_heading(self):
        trace = mod.context_trace(
            {
                "context_trace": {
                    "source_title": "OCR context noisy 999",
                    "period_labels": ["legacy"],
                },
                "report_segment": {
                    "source_heading": "Báo cáo lưu chuyển tiền tệ năm 2023",
                    "period_labels": ["31/12/2023"],
                    "unit_labels": ["VND"],
                    "compact_descriptor": "Báo cáo lưu chuyển tiền tệ — Lưu chuyển tiền tệ",
                },
            }
        )
        self.assertEqual(trace["source_title"], "Báo cáo lưu chuyển tiền tệ năm 2023")
        self.assertEqual(trace["period_labels"], ["31/12/2023"])
        self.assertEqual(trace["unit_labels"], ["VND"])

    def test_machine_review_formats_evidence_and_collapses_votes(self):
        rendered = mod.machine_html(
            {
                "consensus_status": "machine_provisional",
                "machine_candidate_rank": 1,
                "machine_candidate_source": "retrieved",
                "structure_validation": {"validated": True, "row_index": 3},
                "machine_candidate_direct_evidence": (
                    "VALUE: Lưu chuyển tiền thuần từ hoạt động kinh doanh | "
                    "1.448.611.560.410"
                ),
                "verifier": {"verdict": "PARTIAL", "reason": "One operand."},
                "agent_votes": {"evidence_agent": "table-1"},
            }
        )
        self.assertIn("Dòng giá trị", rendered)
        self.assertIn("exact V2 row r3", rendered)
        self.assertIn("Agent votes", rendered)
        self.assertNotIn("<code>VALUE:", rendered)

    def test_assistant_review_renders_exact_source_rows(self):
        rendered = mod.assistant_html(
            {
                "reviewer_type": "codex_assisted",
                "review_round": 1,
                "annotation_status": "machine_provisional",
                "evidence_completeness": "complete",
                "review_confidence": 0.99,
                "proposed_ranks": [2],
                "evidence_refs": [
                    {
                        "rank": 2,
                        "row_indices": [1],
                        "direct_evidence": "VALUE: exact",
                        "source_rows": [
                            {"row_index": 1, "row": ["Lãi tiền gửi", "11.456"]}
                        ],
                    }
                ],
            }
        )
        self.assertIn("Codex review round", rendered)
        self.assertIn("r1", rendered)
        self.assertIn("Lãi tiền gửi | 11.456", rendered)


if __name__ == "__main__":
    unittest.main()
