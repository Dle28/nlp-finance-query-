import unittest

from finance_query.financial_metrics import (
    formula_is_multi_operand,
    infer_formula_spec,
    operand_match_score,
)


class FinancialMetricFormulaTests(unittest.TestCase):
    def test_growth_formula_has_period_specific_operands(self):
        spec = infer_formula_spec(
            "Tỷ lệ tăng trưởng tổng nợ ngắn hạn của CTCP Masan MeatLife "
            "từ cuối năm 2019 đến cuối năm 2023 là bao nhiêu phần trăm?"
        )
        self.assertEqual(spec["formula_id"], "percentage_change")
        self.assertEqual(spec["expression"], "(x_new − x_old) / |x_old| × 100%")
        self.assertEqual(spec["operands"][0]["years"], [2019])
        self.assertEqual(spec["operands"][1]["years"], [2023])
        self.assertIn("tổng nợ ngắn hạn", spec["operands"][0]["label"].casefold())
        self.assertTrue(formula_is_multi_operand(spec))

    def test_growth_rule_preserves_metric_qualifier_and_orders_years(self):
        spec = infer_formula_spec(
            "Tốc độ tăng trưởng doanh thu thuần từ hoạt động xây dựng của DIG "
            "tính từ năm 2022 lên 2023 là bao nhiêu %?"
        )
        self.assertEqual(
            [operand["years"] for operand in spec["operands"]], [[2022], [2023]]
        )
        self.assertIn("từ hoạt động xây dựng", spec["operands"][0]["label"].casefold())

        reversed_years = infer_formula_spec(
            "Tỉ lệ tăng trưởng chi phí mua khí từ các chủ mỏ của GAS trong năm "
            "2022 so với năm 2020 là bao nhiêu %?"
        )
        self.assertEqual(
            [operand["years"] for operand in reversed_years["operands"]], [[2020], [2022]]
        )
        self.assertIn("từ các chủ mỏ", reversed_years["operands"][0]["label"].casefold())

    def test_growth_rule_extracts_metric_from_calculation_and_bare_growth_forms(self):
        calculated = infer_formula_spec(
            "Tính phần trăm tăng trưởng lãi tiền gửi có kỳ hạn của công ty mẹ "
            "CTCP Phát triển Hạ tầng Kỹ thuật từ năm 2016 sang năm 2021."
        )
        self.assertIn("lãi tiền gửi có kỳ hạn", calculated["operands"][0]["label"].casefold())
        self.assertEqual(
            [operand["years"] for operand in calculated["operands"]], [[2016], [2021]]
        )

        bare = infer_formula_spec(
            "Tăng trưởng khoản vay ngắn hạn của MCH từ cuối năm 2021 đến cuối năm 2023 "
            "là bao nhiêu %?"
        )
        self.assertIn("khoản vay ngắn hạn", bare["operands"][0]["label"].casefold())
        self.assertNotIn("chỉ tiêu cần so sánh", bare["operands"][0]["label"].casefold())

    def test_known_balance_sheet_ratio_is_explicit(self):
        spec = infer_formula_spec(
            "Tỷ lệ nợ ngắn hạn trên vốn chủ sở hữu của công ty mẹ VNM năm 2022 là bao nhiêu %?"
        )
        self.assertEqual(spec["formula_id"], "current_liabilities_to_equity")
        self.assertEqual(
            [operand["label"] for operand in spec["operands"]],
            ["Nợ ngắn hạn", "Vốn chủ sở hữu"],
        )

    def test_ambiguous_dividend_yield_is_not_silently_defined(self):
        spec = infer_formula_spec(
            "Tỷ suất sinh lời từ cổ tức đầu tư năm 2023 của công ty mẹ HHS là bao nhiêu %?"
        )
        self.assertEqual(spec["formula_id"], "dividend_investment_yield")
        self.assertEqual(spec["definition_status"], "ambiguous")
        self.assertTrue(spec["notes"])

    def test_net_service_result_uses_subtraction(self):
        spec = infer_formula_spec(
            "Kết quả thuần từ hoạt động dịch vụ của NCB trong năm tài chính 2015 là bao nhiêu tỷ đồng?"
        )
        self.assertEqual(spec["formula_id"], "net_service_result")
        self.assertIn("−", spec["expression"])
        self.assertEqual(len(spec["operands"]), 2)

    def test_operand_match_requires_the_requested_period(self):
        operand = {
            "metric_hints": ["tổng nợ ngắn hạn"],
            "years": [2023],
        }
        self.assertEqual(
            operand_match_score(
                operand,
                "Nợ ngắn hạn | 1.000",
                report_year=2019,
                period_labels=["31/12/2019"],
            ),
            0.0,
        )
        self.assertGreaterEqual(
            operand_match_score(
                operand,
                "Tổng nợ ngắn hạn | 1.000",
                report_year=2023,
                period_labels=["31/12/2023"],
            ),
            0.72,
        )

    def test_multi_stage_question_is_not_reduced_to_first_ratio_keyword(self):
        spec = infer_formula_spec(
            "Trong nhóm HPG, HSG, MSR và NKG, xét các công ty có hệ số "
            "thanh toán nhanh năm 2022 thấp hơn trung vị. Công ty có mức "
            "thay đổi biên lợi nhuận gộp cao nhất có hệ số khả năng thanh "
            "toán lãi vay năm 2023 là bao nhiêu lần?"
        )
        self.assertEqual(
            spec["formula_id"],
            "quick_ratio_gpm_interest_coverage_selection",
        )
        self.assertEqual(spec["definition_status"], "review_required")
        self.assertNotEqual(spec["formula_id"], "quick_ratio")
        self.assertTrue(formula_is_multi_operand(spec))
        self.assertEqual([stage["stage_id"] for stage in spec["stages"]], [
            "quick_ratio_filter",
            "gross_margin_rank",
            "interest_coverage_lookup",
        ])
        self.assertIn("q < median", spec["stages"][0]["decision"])
        self.assertIn("không dùng abs", spec["stages"][1]["decision"])
        self.assertEqual(len(spec["operands"]), 36)
        self.assertEqual(spec["output_unit"], "times")
        self.assertIn("Lợi nhuận trước thuế +", spec["stages"][2]["expression"])

    def test_cfo_positive_filter_then_max_net_margin_has_controlled_stage_plan(self):
        spec = infer_formula_spec(
            "Trong nhóm AAA, DCM, DPM, GVR và PRT có lưu chuyển tiền thuần từ "
            "hoạt động kinh doanh dương trong cả ba năm 2020–2022, tỷ lệ lợi "
            "nhuận sau thuế trên doanh thu thuần cao nhất năm 2022 là bao nhiêu %?"
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec["formula_id"], "cfo_positive_multiyear_max_net_margin")
        self.assertEqual(spec["definition_status"], "defined")
        self.assertEqual(spec["execution_status"], "stage_binding_required")
        self.assertEqual(spec["entities"], ["AAA", "DCM", "DPM", "GVR", "PRT"])
        self.assertEqual(len(spec["operands"]), 25)
        self.assertEqual(
            [stage["stage_id"] for stage in spec["stages"]],
            ["cfo_positive_filter", "net_margin_rank", "target_output"],
        )

    def test_cfo_filter_without_net_margin_target_stays_unresolved(self):
        spec = infer_formula_spec(
            "Trong nhóm AAA, DCM và DPM có lưu chuyển tiền thuần từ hoạt động "
            "kinh doanh dương trong cả hai năm 2020 và 2021, doanh nghiệp có "
            "tăng trưởng doanh thu thuần cao nhất đạt ROA năm 2021 là bao nhiêu phần trăm?"
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec["formula_id"], "multi_stage_selection_unresolved")

    def test_single_entity_multi_period_cfo_max_has_exact_year_operands(self):
        spec = infer_formula_spec(
            "Năm nào trong các năm 2017, 2019, 2020, 2021 và 2023 ghi nhận "
            "lưu chuyển tiền thuần từ hoạt động kinh doanh của QNS trên cơ sở công ty mẹ cao nhất?"
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec["formula_id"], "operating_cash_flow_argmax_period")
        self.assertEqual(spec["entity"], "QNS")
        self.assertEqual(spec["output_unit"], "year")
        self.assertEqual(spec["execution_status"], "period_argmax_required")
        self.assertEqual([operand["years"] for operand in spec["operands"]], [[2017], [2019], [2020], [2021], [2023]])
        self.assertTrue(all(operand["entity"] == "QNS" for operand in spec["operands"]))
        self.assertTrue(all(operand["allowed_table_functions"] == ["cash_flow_statement"] for operand in spec["operands"]))

    def test_entity_specific_operand_does_not_bind_other_ticker(self):
        operand = {
            "entity": "HPG",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2022],
        }
        self.assertEqual(
            operand_match_score(
                operand,
                "Tài sản ngắn hạn | 100",
                report_year=2022,
                period_labels=["31/12/2022"],
                ticker="HSG",
            ),
            0.0,
        )
        self.assertEqual(
            operand_match_score(
                operand,
                "Tài sản ngắn hạn | 100",
                report_year=2022,
                period_labels=["31/12/2022"],
                ticker="HPG",
            ),
            1.0,
        )

    def test_staged_operand_declares_trusted_statement_function(self):
        spec = infer_formula_spec(
            "Trong nhóm HPG, HSG, MSR và NKG, xét các công ty có hệ số "
            "thanh toán nhanh năm 2022 thấp hơn trung vị. Công ty có mức "
            "thay đổi biên lợi nhuận gộp cao nhất từ năm 2022 sang năm 2023 "
            "có hệ số khả năng thanh toán lãi vay năm 2023 là bao nhiêu lần?"
        )
        by_role = {operand["role"]: operand for operand in spec["operands"]}
        self.assertEqual(
            by_role["quick_ratio_numerator_base"]["allowed_table_functions"],
            ["balance_sheet"],
        )
        self.assertEqual(
            by_role["gross_margin_numerator"]["allowed_table_functions"],
            ["income_statement", "segment_reporting"],
        )
        self.assertEqual(
            by_role["gross_margin_numerator"]["table_function_column_hints"],
            {"segment_reporting": ["tổng cộng"]},
        )


if __name__ == "__main__":
    unittest.main()
