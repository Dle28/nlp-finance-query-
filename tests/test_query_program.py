import unittest

from finance_query.financial_metrics import infer_formula_spec
from finance_query.query_program import (
    QueryProgramError,
    compile_query_program,
    evaluate_shadow_query_program,
    shadow_readiness,
)


QUESTION = (
    "Trong nhóm HPG, HSG, MSR và NKG, xét các công ty có hệ số thanh toán nhanh "
    "năm 2022 thấp hơn trung vị. Công ty có mức thay đổi biên lợi nhuận gộp cao "
    "nhất từ năm 2022 sang năm 2023 có hệ số khả năng thanh toán lãi vay năm 2023 "
    "là bao nhiêu lần?"
)


def program() -> object:
    formula = infer_formula_spec(QUESTION)
    assert formula is not None
    compiled = compile_query_program(formula)
    assert compiled is not None
    return compiled


def values() -> dict[str, str]:
    output: dict[str, str] = {}
    # Quick ratios: HPG=2, HSG=1.5, MSR=2, NKG=1. Median=1.75, hence HSG/NKG.
    quick = {
        "hpg": ("100", "20", "40"),
        "hsg": ("80", "20", "40"),
        "msr": ("70", "20", "25"),
        "nkg": ("90", "30", "60"),
    }
    for entity, (assets, inventory, liabilities) in quick.items():
        output[f"{entity}_current_assets_2022"] = assets
        output[f"{entity}_inventory_2022"] = inventory
        output[f"{entity}_current_liabilities_2022"] = liabilities
    # HSG ΔGPM = 0.05; NKG ΔGPM = 0.02, so HSG wins.
    margins = {
        "hpg": ("20", "100", "20", "100"),
        "hsg": ("5", "100", "10", "100"),
        "msr": ("20", "100", "20", "100"),
        "nkg": ("18", "100", "20", "100"),
    }
    for entity, (old_profit, old_revenue, new_profit, new_revenue) in margins.items():
        output[f"{entity}_gross_profit_2022"] = old_profit
        output[f"{entity}_net_revenue_2022"] = old_revenue
        output[f"{entity}_gross_profit_2023"] = new_profit
        output[f"{entity}_net_revenue_2023"] = new_revenue
    for entity in ("hpg", "hsg", "msr", "nkg"):
        output[f"{entity}_profit_before_tax_2023"] = "30"
        output[f"{entity}_interest_expense_2023"] = "-10"
    return output


class QueryProgramTests(unittest.TestCase):
    def test_compiles_controlled_multistage_formula(self):
        compiled = program()
        self.assertEqual(compiled.program_id, "quick_ratio_gpm_interest_coverage_selection_v1")
        self.assertEqual([stage.stage_id for stage in compiled.stages], [
            "quick_ratio_filter",
            "gross_margin_rank",
            "interest_coverage_lookup",
        ])
        self.assertEqual(len(compiled.required_operand_ids), 36)
        self.assertFalse(compiled.submission_eligible)

    def test_shadow_evaluation_is_strict_and_returns_no_submission_eligibility(self):
        result = evaluate_shadow_query_program(program(), values())
        self.assertEqual(result["status"], "shadow_complete")
        self.assertEqual(result["winner_entity"], "HSG")
        self.assertEqual(result["result_value"], "4")
        self.assertEqual(result["result_unit"], "times")
        self.assertFalse(result["submission_eligible"])
        self.assertEqual(result["stage_trace"]["eligible_entities"], ["HSG", "NKG"])

    def test_shadow_evaluation_blocks_missing_values_and_ties(self):
        missing = values()
        del missing["hsg_interest_expense_2023"]
        blocked = evaluate_shadow_query_program(program(), missing)
        self.assertEqual(blocked["status"], "shadow_blocked")
        self.assertIn("hsg_interest_expense_2023", blocked["missing_operand_ids"])
        tied = values()
        tied["nkg_gross_profit_2023"] = "23"
        result = evaluate_shadow_query_program(program(), tied)
        self.assertEqual(result["status"], "shadow_blocked")
        self.assertIn("arithmetic_precondition", result["reason_codes"][0])

    def test_readiness_preserves_existing_formula_evidence_blocks(self):
        formula = infer_formula_spec(QUESTION)
        assert formula is not None
        readiness = shadow_readiness(
            {
                "formula": formula,
                "evidence_completeness": "partial",
                "selected_operand_matches": {},
                "reason_codes": ["no_common_scope_across_entity_operands"],
            },
            program(),
        )
        self.assertEqual(readiness["status"], "shadow_blocked")
        self.assertIn("formula_definition_not_defined", readiness["reason_codes"])
        self.assertIn("no_common_scope_across_entity_operands", readiness["reason_codes"])

    def test_unsupported_formula_is_not_compiled(self):
        self.assertIsNone(compile_query_program({"formula_id": "quick_ratio"}))
        with self.assertRaisesRegex(QueryProgramError, "stage_binding_required"):
            compile_query_program(
                {
                    "formula_id": "quick_ratio_gpm_interest_coverage_selection",
                    "execution_status": "not_executed",
                }
            )
