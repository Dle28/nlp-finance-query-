import unittest

from finance_query.financial_metrics import infer_formula_spec
from finance_query.query_program import (
    QueryProgramError,
    compile_query_program,
    evaluate_shadow_query_program,
    operand_values_from_selected_matches,
    shadow_readiness,
)


QUESTION = (
    "Trong nhóm HPG, HSG, MSR và NKG, xét các công ty có hệ số thanh toán nhanh "
    "năm 2022 thấp hơn trung vị. Công ty có mức thay đổi biên lợi nhuận gộp cao "
    "nhất từ năm 2022 sang năm 2023 có hệ số khả năng thanh toán lãi vay năm 2023 "
    "là bao nhiêu lần?"
)

CFO_NPM_QUESTION = (
    "Trong nhóm GEE, GEX và SAM, xét các công ty có lưu chuyển tiền thuần từ hoạt động "
    "kinh doanh dương trong cả ba năm 2022, 2023 và 2024, tỷ lệ lợi nhuận sau thuế trên "
    "doanh thu thuần cao nhất năm 2024 là bao nhiêu %?"
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


def cfo_program() -> object:
    formula = infer_formula_spec(CFO_NPM_QUESTION)
    assert formula is not None
    compiled = compile_query_program(formula)
    assert compiled is not None
    return compiled


def cfo_values() -> dict[str, str]:
    output: dict[str, str] = {}
    # GEE is filtered out (one negative CFO); GEX has the largest NPM (20%).
    cfo = {
        "gee": ("1", "-1", "1"),
        "gex": ("1", "1", "1"),
        "sam": ("1", "1", "1"),
    }
    for entity, values_by_year in cfo.items():
        for year, value in zip((2022, 2023, 2024), values_by_year, strict=True):
            output[f"{entity}_operating_cash_flow_{year}"] = value
    margins = {"gee": ("1", "10"), "gex": ("20", "100"), "sam": ("15", "100")}
    for entity, (profit, revenue) in margins.items():
        output[f"{entity}_net_profit_2024"] = profit
        output[f"{entity}_net_revenue_2024"] = revenue
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

    def test_compiles_and_shadows_cfo_filter_then_net_margin_rank(self):
        compiled = cfo_program()
        self.assertEqual(compiled.program_id, "cfo_positive_multiyear_max_net_margin_v1")
        self.assertEqual(compiled.screen_years, [2022, 2023, 2024])
        self.assertEqual(compiled.target_year, 2024)
        result = evaluate_shadow_query_program(compiled, cfo_values())
        self.assertEqual(result["status"], "shadow_complete")
        self.assertEqual(result["winner_entity"], "GEX")
        self.assertEqual(result["result_value"], "20.0")
        self.assertEqual(result["result_unit"], "percent")
        self.assertEqual(result["stage_trace"]["eligible_entities"], ["GEX", "SAM"])
        self.assertFalse(result["submission_eligible"])

    def test_staged_partial_with_all_selected_operands_is_shadow_ready_only(self):
        compiled = cfo_program()
        selected = {
            operand_id: {
                "ticker": operand_id.split("_", maxsplit=1)[0].upper(),
                "scope": "consolidated",
                "report_year": int(operand_id.rsplit("_", maxsplit=1)[1]),
                "source_unit": "vnd",
                "binding": {
                    "status": "cell_bound",
                    "parsed_value": value,
                    "parse_warnings": [],
                }
            }
            for operand_id, value in cfo_values().items()
        }
        evidence = {
            "formula": infer_formula_spec(CFO_NPM_QUESTION),
            "operand_coverage_status": "complete",
            "evidence_completeness": "partial",
            "selected_operand_matches": selected,
            "reason_codes": [
                "formula_requires_stage_binding",
                "question_family_requires_composed_execution",
            ],
        }
        readiness = shadow_readiness(evidence, compiled)
        self.assertEqual(readiness["status"], "shadow_ready")
        extracted = operand_values_from_selected_matches(compiled, evidence)
        self.assertEqual(extracted["status"], "shadow_values_ready")
        self.assertFalse(readiness["submission_eligible"])

    def test_staged_partial_blocks_mixed_selected_scopes(self):
        compiled = cfo_program()
        selected = {
            operand_id: {
                "ticker": operand_id.split("_", maxsplit=1)[0].upper(),
                "scope": "consolidated" if operand_id.startswith("gee_") else "separate",
                "report_year": int(operand_id.rsplit("_", maxsplit=1)[1]),
                "source_unit": "vnd",
                "binding": {
                    "status": "cell_bound",
                    "parsed_value": value,
                    "parse_warnings": [],
                },
            }
            for operand_id, value in cfo_values().items()
        }
        readiness = shadow_readiness(
            {
                "formula": infer_formula_spec(CFO_NPM_QUESTION),
                "operand_coverage_status": "complete",
                "evidence_completeness": "partial",
                "selected_operand_matches": selected,
                "reason_codes": [
                    "formula_requires_stage_binding",
                    "question_family_requires_composed_execution",
                ],
            },
            compiled,
        )
        self.assertEqual(readiness["status"], "shadow_blocked")
        self.assertIn("selected_operand_metadata_not_coherent", readiness["reason_codes"])

    def test_staged_partial_blocks_mismatched_net_margin_units(self):
        compiled = cfo_program()
        selected = {
            operand_id: {
                "ticker": operand_id.split("_", maxsplit=1)[0].upper(),
                "scope": "consolidated",
                "report_year": int(operand_id.rsplit("_", maxsplit=1)[1]),
                "source_unit": "million_vnd" if operand_id == "gex_net_profit_2024" else "vnd",
                "binding": {
                    "status": "cell_bound",
                    "parsed_value": value,
                    "parse_warnings": [],
                },
            }
            for operand_id, value in cfo_values().items()
        }
        readiness = shadow_readiness(
            {
                "formula": infer_formula_spec(CFO_NPM_QUESTION),
                "operand_coverage_status": "complete",
                "evidence_completeness": "partial",
                "selected_operand_matches": selected,
                "reason_codes": [
                    "formula_requires_stage_binding",
                    "question_family_requires_composed_execution",
                ],
            },
            compiled,
        )
        self.assertEqual(readiness["status"], "shadow_blocked")
        self.assertIn("selected_operand_metadata_not_coherent", readiness["reason_codes"])
