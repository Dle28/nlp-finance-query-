import unittest

from finance_query.report_normalization import (
    build_staged_route_plan,
    build_table_normalization_entry,
    document_metadata_rows,
    normalize_financial_variable,
    route_stage_candidates,
)


def table(*, uid: str = "table-1", function: str = "balance_sheet") -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": "HPG_financial_statements_2022_consolidated",
        "ticker": "HPG",
        "report_year": 2022,
        "scope": "consolidated",
        "context_before": "CTCP Tập đoàn Hòa Phát Báo cáo tài chính hợp nhất năm 2022",
        "table_function": {"kind": function, "specificity": "structural"},
        "rows": [
            ["Chỉ tiêu", "31/12/2022", "1/1/2022"],
            ["I. Tài sản ngắn hạn (100 = 110 + 120 + 130)", "100", "90"],
            ["Hàng tồn kho (Thuyết minh VI.06)", "140", "18"],
            ["Nợ ngắn hạn", "310", "45"],
        ],
    }


def context(*, uid: str = "table-1", status: str = "review_ready") -> dict:
    return {
        "internal_table_uid": uid,
        "table_function": {"kind": "balance_sheet", "specificity": "structural"},
        "quality": {"status": status, "reason_codes": []},
        "canonical_headers": {
            "raw_header_row_indices": [0, 3],
            "header_row_indices": [0],
            "excluded_header_row_indices": [3],
        },
        "row_profiles": [
            {"row_index": 0, "role": "header"},
            {"row_index": 1, "role": "data"},
            {"row_index": 2, "role": "data"},
            {"row_index": 3, "role": "data"},
        ],
    }


class ReportNormalizationTests(unittest.TestCase):
    def test_document_metadata_keeps_explicit_company_year_scope_and_type(self):
        row = document_metadata_rows([table()])[0]
        self.assertEqual(row["company"], "HPG")
        self.assertEqual(row["report_year"], 2022)
        self.assertEqual(row["report_scope"], "consolidated")
        self.assertEqual(row["report_type"], "financial_statements")

    def test_normalization_uses_data_rows_only_and_preserves_header_anomaly(self):
        document = document_metadata_rows([table()])[0]
        entry = build_table_normalization_entry(table(), context(), document)
        self.assertEqual(entry["table_type"], "balance_sheet")
        self.assertTrue(entry["routing_eligible"])
        self.assertEqual(entry["header_integrity"]["excluded_header_row_indices"], [3])
        variables = {row["variable_id"]: row for row in entry["canonical_variables"]}
        self.assertEqual(set(variables), {"current_assets", "inventory", "current_liabilities"})
        self.assertEqual(variables["current_assets"]["normalized_row_label"], "tai san ngan han")
        self.assertEqual(variables["inventory"]["row_index"], 2)
        self.assertIn("Thuyết minh", variables["inventory"]["raw_row_label"])

    def test_normalization_recovers_only_escaped_structural_equation(self):
        match = normalize_financial_variable(
            [r"Tài sản ngắn hạn(\( 100 = 110 + 120 + 130 + 140 + 150 \))", "100", "80"]
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match["variable_id"], "current_assets")
        self.assertEqual(match["normalized_row_label"], "tai san ngan han")

    def test_inventory_detail_account_code_is_not_normalized_as_total_inventory(self):
        total = normalize_financial_variable(["Hàng tồn kho", "140", "100"])
        detail = normalize_financial_variable(["Hàng tồn kho", "141", "90"])
        self.assertIsNotNone(total)
        self.assertEqual(total["variable_id"], "inventory")
        self.assertIsNone(detail)

    def test_net_income_accepts_exact_tndn_source_labels(self):
        for source_label in (
            "Lợi nhuận sau thuế TNDN (60 = 50 - 51 - 52)",
            "Lợi nhuận thuần sau thuế TNDN (60 = 50 - 51 - 52) (mang sang trang sau)",
            "Lợi nhuận sau thuế TNDN (60 = 50 - 51 - 52) (chuyển sang trang sau)",
        ):
            match = normalize_financial_variable([source_label, "60", "105"])
            self.assertIsNotNone(match)
            assert match is not None
            self.assertEqual(match["variable_id"], "net_income")

    def test_interest_coverage_operands_accept_exact_statement_variants(self):
        interest = normalize_financial_variable(["Trong đó: Chi phí lãi vay", "23", "105"])
        loss_before_tax = normalize_financial_variable(
            ["Lỗ kế toán trước thuế (50 = 30 + 40)", "50", "-12"]
        )
        self.assertIsNotNone(interest)
        self.assertIsNotNone(loss_before_tax)
        assert interest is not None and loss_before_tax is not None
        self.assertEqual(interest["variable_id"], "interest_expense")
        self.assertEqual(loss_before_tax["variable_id"], "profit_before_tax")

    def test_document_metadata_extracts_non_calendar_fiscal_year_end_from_source(self):
        fiscal_table = table()
        fiscal_table["document_id"] = "HSG_financial_statements_2022_consolidated"
        fiscal_table["ticker"] = "HSG"
        fiscal_table["context_before"] = (
            "Công ty Cổ phần Tập đoàn Hoa Sen và các công ty con "
            "Báo cáo tài chính hợp nhất cho năm kết thúc ngày 30 tháng 9 năm 2022"
        )
        row = document_metadata_rows([fiscal_table])[0]
        self.assertEqual(row["reporting_period_end"], {"day": 30, "month": 9, "year": 2022})

    def test_unsafe_header_is_visible_but_not_routable(self):
        document = document_metadata_rows([table()])[0]
        entry = build_table_normalization_entry(table(), context(status="needs_processing"), document)
        self.assertFalse(entry["routing_eligible"])
        self.assertEqual(entry["header_integrity"]["status"], "needs_processing")

    def test_user_quick_ratio_then_margin_question_compiles_to_two_routes(self):
        route = build_staged_route_plan(
            "Năm 2022, trong nhóm HPG, HSG, MSR và NKG, các công ty có hệ số "
            "thanh toán nhanh thấp hơn trung vị. Tính trung bình tỷ lệ lợi nhuận "
            "sau thuế trên doanh thu thuần của các công ty đó."
        )
        self.assertEqual(route["routing_status"], "planned")
        self.assertEqual(route["family"], "quick_ratio_median_then_net_profit_margin")
        first, second = route["stages"]
        self.assertEqual(first["table_types"], ["balance_sheet"])
        self.assertEqual(first["required_variables"], ["current_assets", "inventory", "current_liabilities"])
        self.assertEqual(second["table_types"], ["income_statement"])
        self.assertEqual(second["required_variables"], ["net_income", "net_revenue"])
        self.assertEqual(second["entity_source"], "@eligible_entities")
        self.assertEqual(second["aggregate"], "average")

    def test_no_candidate_returns_actionable_feedback_instead_of_a_guess(self):
        route = build_staged_route_plan(
            "Năm 2022, trong nhóm HPG, HSG, MSR và NKG, các công ty có hệ số "
            "thanh toán nhanh thấp hơn trung vị. Tính trung bình tỷ lệ lợi nhuận "
            "sau thuế trên doanh thu thuần của các công ty đó."
        )
        result = route_stage_candidates([], route["stages"][0])
        self.assertEqual(result["status"], "no_candidate")
        self.assertEqual(
            result["feedback"]["reason_code"],
            "all_tables_blocked_by_header_or_table_type_quality",
        )
        self.assertIn("never infer", result["feedback"]["next_action"])

    def test_route_can_bind_ratio_variables_from_separate_source_tables(self):
        route = build_staged_route_plan(
            "Năm 2022, trong nhóm HPG và HSG, các công ty có hệ số thanh toán "
            "nhanh thấp hơn trung vị. Tính trung bình tỷ lệ lợi nhuận sau thuế "
            "trên doanh thu thuần của các công ty đó."
        )
        stage = route["stages"][0]
        records = []
        for company, suffix, report_year, header_years in (
            ("HPG", "hpg", 2023, [2022, 2023]),
            ("HSG", "hsg", 2022, [2022]),
        ):
            for variable_id in ("current_assets", "inventory", "current_liabilities"):
                records.append(
                    {
                        "internal_table_uid": f"{suffix}-{variable_id}",
                        "company": company,
                        "report_year": report_year,
                        "available_period_years": header_years,
                        "report_scope": "consolidated",
                        "table_type": "balance_sheet",
                        "routing_eligible": True,
                        "canonical_variables": [{"variable_id": variable_id}],
                    }
                )
        result = route_stage_candidates(records, stage)
        self.assertEqual(result["status"], "candidate_tables_found")
        self.assertEqual(
            set(result["candidate_table_uids_by_entity_variable"]["HPG"]),
            {"current_assets", "inventory", "current_liabilities"},
        )

    def test_later_stage_waits_for_the_previous_entity_selection(self):
        route = build_staged_route_plan(
            "Năm 2022, trong nhóm HPG và HSG, các công ty có hệ số thanh toán "
            "nhanh thấp hơn trung vị. Tính trung bình tỷ lệ lợi nhuận sau thuế "
            "trên doanh thu thuần của các công ty đó."
        )
        result = route_stage_candidates([], route["stages"][1])
        self.assertEqual(result["status"], "awaiting_prior_stage")
        self.assertEqual(
            result["feedback"]["reason_code"], "prior_stage_entity_selection_not_available"
        )


if __name__ == "__main__":
    unittest.main()
