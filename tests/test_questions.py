from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from finance_query.questions import RuleQuestionPlanner, load_ticker_aliases, weak_family_from_id


class QuestionRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = RuleQuestionPlanner()

    def test_observed_id_ranges(self) -> None:
        self.assertEqual(weak_family_from_id(1), "direct_lookup")
        self.assertEqual(weak_family_from_id(362), "conditional_analytical")
        self.assertEqual(weak_family_from_id(578), "temporal_change")
        self.assertEqual(weak_family_from_id(656), "ratio_or_derived")
        self.assertEqual(weak_family_from_id(733), "cross_entity_comparison")
        self.assertEqual(
            weak_family_from_id(813),
            "multi_entity_or_period_aggregation",
        )

    def test_direct_lookup_slots(self) -> None:
        plan = self.planner.plan(
            "Lợi nhuận sau thuế của VIB năm 2020 là bao nhiêu triệu đồng?",
            question_id=40,
        )
        self.assertEqual(plan.family, "direct_lookup")
        self.assertEqual(plan.years, [2020])
        self.assertEqual(plan.requested_unit, "million_vnd")
        self.assertIn("VIB", plan.tickers)
        self.assertEqual(plan.operation_ast["op"], "lookup")

    def test_temporal_change(self) -> None:
        plan = self.planner.plan(
            "Doanh thu thuần tăng bao nhiêu từ năm 2022 đến năm 2024?",
            question_id=578,
        )
        self.assertEqual(plan.family, "temporal_change")
        self.assertEqual(plan.years, [2022, 2024])
        self.assertEqual(len(plan.operands), 2)

    def test_disclosed_ownership_ratio_is_a_direct_lookup(self) -> None:
        plan = self.planner.plan(
            "Tỷ lệ sở hữu Công ty CP Gang thép Hòa Phát của HPG đến ngày 31/12/2023 là bao nhiêu %?",
            question_id=97,
        )
        self.assertEqual(plan.family, "direct_lookup")
        self.assertEqual(plan.operation_ast["op"], "lookup")
        self.assertEqual(len(plan.operands), 1)

    def test_disclosed_total_row_is_a_direct_lookup(self) -> None:
        plan = self.planner.plan(
            "Tổng cộng tài sản của SSH cuối năm 2025 là bao nhiêu nghìn tỷ đồng?",
            question_id=92,
        )
        self.assertEqual(plan.family, "direct_lookup")
        self.assertEqual(plan.operation_ast["op"], "lookup")

    def test_multi_entity_total_cannot_be_reclassified_as_direct(self) -> None:
        plan = self.planner.plan(
            "Tổng cộng số dư dự phòng của SHB, VIB và BID vào cuối năm 2016 là bao nhiêu triệu đồng?",
            question_id=991,
        )
        self.assertNotEqual(plan.family, "direct_lookup")

    def test_alias_can_drop_only_terminal_legal_suffix(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stocks.csv"
            path.write_text(
                "Mã CK,Tên công ty\nACV,Tổng Công ty Cảng Hàng không Việt Nam - CTCP\n",
                encoding="utf-8",
            )
            aliases = load_ticker_aliases(path)
        self.assertEqual(aliases["tổng công ty cảng hàng không việt nam"], "ACV")

    def test_alias_collision_is_not_assigned_to_an_arbitrary_ticker(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stocks.csv"
            path.write_text(
                "Mã CK,Tên công ty\nAAA,Công ty Ví dụ - CTCP\nBBB,Công ty Ví dụ - TMCP\n",
                encoding="utf-8",
            )
            aliases = load_ticker_aliases(path)
        self.assertNotIn("công ty ví dụ", aliases)

    def test_real_question_can_resolve_company_without_ctcp_suffix(self) -> None:
        planner = RuleQuestionPlanner(Path("data/ViFinQA/code_stock.csv"))
        plan = planner.plan(
            "Tốc độ tăng trưởng tiền và các khoản tương đương tiền của Tổng Công ty Cảng Hàng không Việt Nam từ cuối năm 2021 đến cuối năm 2022 là bao nhiêu phần trăm?",
            question_id=586,
        )
        self.assertIn("ACV", plan.tickers)


if __name__ == "__main__":
    unittest.main()
