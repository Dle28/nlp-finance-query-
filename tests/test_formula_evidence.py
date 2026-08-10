import unittest

from finance_query.evidence_context import build_evidence_context
from finance_query.formula_evidence import (
    FORMULA_BUNDLE_SUPPORT_CANDIDATE_SOURCE,
    FORMULA_SOURCE_DISCOVERY_CANDIDATE_SOURCE,
    bind_operand_cell,
    formula_evidence_set,
    multi_entity_scope_diagnostics,
    operand_evidence_matches,
    select_coherent_operand_matches,
    source_discovery_candidates,
)


def _table() -> dict:
    return {
        "internal_table_uid": "u1",
        "document_id": "ABC_financial_statements_2023_separate",
        "ticker": "ABC",
        "rows": [
            ["Chỉ tiêu", "Năm 2023", "Năm 2022"],
            ["Tài sản ngắn hạn", "100", "90"],
            ["Nợ ngắn hạn", "50", "45"],
        ],
        "header_row_indices": [0],
        "cell_provenance": [
            [
                {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                for column in range(3)
            ]
            for row in range(3)
        ],
        "source_provenance": {},
    }


def _entity_table(uid: str, ticker: str) -> dict:
    table = _table()
    table["internal_table_uid"] = uid
    table["document_id"] = f"{ticker}_financial_statements_2023_consolidated"
    table["ticker"] = ticker
    return table


class FormulaEvidenceTests(unittest.TestCase):

    def test_balance_sheet_year_chooses_closing_header_not_opening_balance(self):
        operand = {
            "operand_id": "current_assets",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2022],
        }
        candidate = {"report_year": 2022}
        table = {
            "rows": [
                ["Chỉ tiêu", "Mã số", "31/12/2022 VND", "1/1/2022 VND"],
                ["Tài sản ngắn hạn", "100", "120", "100"],
            ],
            "cell_provenance": [
                [{}, {}, {}, {}],
                [{}, {}, {"source_cell": 2}, {"source_cell": 3}],
            ],
        }
        context = {
            "table_function": {"kind": "balance_sheet"},
            "canonical_headers": {
                "columns": [
                    {"column_index": 0, "source_label": "Chỉ tiêu"},
                    {"column_index": 1, "source_label": "Mã số"},
                    {"column_index": 2, "source_label": "31/12/2022 VND"},
                    {"column_index": 3, "source_label": "1/1/2022 VND"},
                ]
            },
            "row_profiles": [
                {"row_index": 1, "role": "data", "numeric_columns": [1, 2, 3]}
            ],
        }
        binding = bind_operand_cell(operand, candidate, table, context, 1)
        self.assertEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["column_index"], 2)
        self.assertEqual(
            binding["binding_reason"],
            "balance_sheet_closing_header_excludes_opening_date",
        )

    def test_balance_sheet_two_closing_dates_remain_ambiguous(self):
        operand = {"operand_id": "current_assets", "years": [2022]}
        table = {
            "rows": [["Chỉ tiêu", "", ""], ["Tài sản ngắn hạn", "100", "120"]],
            "cell_provenance": [[{}, {}, {}], [{}, {}, {}]],
        }
        context = {
            "table_function": {"kind": "balance_sheet"},
            "canonical_headers": {
                "columns": [
                    {"column_index": 1, "source_label": "30/6/2022 VND"},
                    {"column_index": 2, "source_label": "31/12/2022 VND"},
                ]
            },
            "row_profiles": [{"row_index": 1, "role": "data", "numeric_columns": [1, 2]}],
        }
        binding = bind_operand_cell(operand, {"report_year": 2022}, table, context, 1)
        self.assertEqual(binding["status"], "ambiguous_period_column")

    def test_balance_sheet_unique_closing_balance_uses_matching_document_year(self):
        operand = {"operand_id": "cash", "years": [2022]}
        table = {
            "rows": [["Chỉ tiêu", "Mã số", "Số cuối năm", "Số đầu năm"], ["Tiền", "110", "100", "80"]],
            "cell_provenance": [[{}, {}, {}, {}], [{}, {}, {}, {}]],
        }
        context = {
            "table_function": {"kind": "balance_sheet"},
            "canonical_headers": {"columns": [
                {"column_index": 0, "source_label": "Chỉ tiêu"},
                {"column_index": 1, "source_label": "Mã số"},
                {"column_index": 2, "source_label": "Số cuối năm VND"},
                {"column_index": 3, "source_label": "Số đầu năm VND"},
            ]},
            "row_profiles": [{"row_index": 1, "role": "data", "numeric_columns": [1, 2, 3]}],
        }
        binding = bind_operand_cell(operand, {"report_year": 2022}, table, context, 1)
        self.assertEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["column_index"], 2)
        self.assertEqual(
            binding["binding_reason"], "balance_sheet_closing_balance_matches_report_year"
        )

    def test_balance_sheet_metric_rejects_subtotal_suffix_but_keeps_code_formula(self):
        operand = {
            "operand_id": "current_assets",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2022],
            "allowed_table_functions": ["balance_sheet"],
        }
        table = {
            "internal_table_uid": "u1",
            "ticker": "ABC",
            "rows": [
                ["Chỉ tiêu", "Mã số", "31/12/2022 VND"],
                ["Tài sản ngắn hạn(100 = 110 + 130 + 140)", "100", "1000"],
                ["I. Tiền và các khoản tương đương tiền", "110", "200"],
                ["Tài sản ngắn hạn khác", "150", "50"],
            ],
            "cell_provenance": [[{}, {}, {}], [{}, {}, {}], [{}, {}, {}]],
        }
        context = {
            "quality": {"status": "review_ready"},
            "table_function": {"kind": "balance_sheet"},
            "canonical_headers": {
                "columns": [
                    {"column_index": 0, "source_label": "Chỉ tiêu"},
                    {"column_index": 1, "source_label": "Mã số"},
                    {"column_index": 2, "source_label": "31/12/2022 VND"},
                ]
            },
            "row_profiles": [
                {"row_index": index, "role": "data", "numeric_columns": [1, 2]}
                for index in (1, 2, 3)
            ],
        }
        matches = operand_evidence_matches(
            operand,
            {"internal_table_uid": "u1", "ticker": "ABC", "scope": "separate", "report_year": 2022, "rank": 1},
            table,
            context,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["row_index"], 1)

    def test_balance_sheet_metric_accepts_only_structural_roman_prefix(self):
        operand = {
            "operand_id": "cash",
            "metric_hints": ["tiền và các khoản tương đương tiền"],
            "years": [2022],
            "allowed_table_functions": ["balance_sheet"],
        }
        table = {
            "internal_table_uid": "u1",
            "ticker": "ABC",
            "rows": [
                ["Chỉ tiêu", "Mã số", "31/12/2022 VND"],
                ["I. Tiền và các khoản tương đương tiền", "110", "200"],
                ["I. Tiền và các khoản tương đương tiền khác", "111", "50"],
            ],
            "cell_provenance": [[{}, {}, {}] for _ in range(3)],
        }
        context = {
            "quality": {"status": "review_ready"},
            "table_function": {"kind": "balance_sheet"},
            "canonical_headers": {"columns": [
                {"column_index": 0, "source_label": "Chỉ tiêu"},
                {"column_index": 1, "source_label": "Mã số"},
                {"column_index": 2, "source_label": "31/12/2022 VND"},
            ]},
            "row_profiles": [
                {"row_index": index, "role": "data", "numeric_columns": [1, 2]}
                for index in (1, 2)
            ],
        }
        matches = operand_evidence_matches(
            operand,
            {"internal_table_uid": "u1", "ticker": "ABC", "scope": "separate", "report_year": 2022, "rank": 1},
            table,
            context,
        )
        self.assertEqual([match["row_index"] for match in matches], [1])

    def test_income_statement_net_margin_uses_total_not_parent_or_nci_components(self):
        operand = {
            "operand_id": "net_profit",
            "metric_hints": ["lợi nhuận sau thuế", "lợi nhuận ròng"],
            "years": [2022],
            "role": "net_margin_numerator",
            "allowed_table_functions": ["income_statement"],
        }
        table = {
            "internal_table_uid": "u1",
            "ticker": "ABC",
            "rows": [
                ["Chỉ tiêu", "Mã số", "Năm 2022 VND"],
                ["18. Lợi nhuận sau thuế thu nhập doanh nghiệp (50-51-52)", "60", "100"],
                ["18.1 Lợi nhuận sau thuế của Công ty mẹ", "61", "90"],
                ["18.2 Lợi nhuận sau thuế của cổ đông không kiểm soát", "62", "10"],
            ],
            "cell_provenance": [[{}, {}, {}] for _ in range(4)],
        }
        context = {
            "quality": {"status": "review_ready"},
            "table_function": {"kind": "income_statement"},
            "canonical_headers": {
                "columns": [
                    {"column_index": 0, "source_label": "Chỉ tiêu"},
                    {"column_index": 1, "source_label": "Mã số"},
                    {"column_index": 2, "source_label": "Năm 2022 VND"},
                ]
            },
            "row_profiles": [
                {"row_index": index, "role": "data", "numeric_columns": [1, 2]}
                for index in (1, 2, 3)
            ],
        }
        matches = operand_evidence_matches(
            operand,
            {"internal_table_uid": "u1", "ticker": "ABC", "scope": "consolidated", "report_year": 2022, "rank": 1},
            table,
            context,
        )
        self.assertEqual([match["row_index"] for match in matches], [1])

    def test_equivalent_comparative_witnesses_need_same_year_primary_value_and_unit(self):
        operands = [{"operand_id": "cfo_2021", "entity": "ABC", "years": [2021]}]
        primary = {
            "internal_table_uid": "u2021", "ticker": "ABC", "scope": "separate", "report_year": 2021,
            "source_unit": "vnd", "binding": {"raw_value": "100", "parsed_value": "100"},
        }
        comparative = {
            "internal_table_uid": "u2022", "ticker": "ABC", "scope": "separate", "report_year": 2022,
            "source_unit": "vnd", "binding": {"raw_value": "100", "parsed_value": "100"},
        }
        selected, reasons = select_coherent_operand_matches(
            {"cfo_2021": [primary, comparative]},
            operands,
            allow_equivalent_comparative_witnesses=True,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(selected["cfo_2021"]["internal_table_uid"], "u2021")
        self.assertEqual(len(selected["cfo_2021"]["equivalent_comparative_witnesses"]), 1)
        comparative["binding"]["raw_value"] = "101"
        selected, reasons = select_coherent_operand_matches(
            {"cfo_2021": [primary, comparative]},
            operands,
            allow_equivalent_comparative_witnesses=True,
        )
        self.assertEqual(selected, {})
        self.assertEqual(reasons, ["ambiguous_operand_bindings"])

    def test_controlled_multistage_formula_can_select_identical_comparative_witness(self):
        operands = [
            {"operand_id": "aaa_cfo_2021", "entity": "AAA", "years": [2021]},
            {"operand_id": "aaa_profit_2021", "entity": "AAA", "years": [2021]},
            {"operand_id": "bbb_cfo_2021", "entity": "BBB", "years": [2021]},
            {"operand_id": "bbb_profit_2021", "entity": "BBB", "years": [2021]},
        ]

        def match(entity, operand_id, raw, report_year):
            return {
                "ticker": entity,
                "scope": "consolidated",
                "report_year": report_year,
                "internal_table_uid": f"{entity}-{operand_id}-{report_year}",
                "source_unit": "vnd",
                "binding": {"raw_value": raw, "parsed_value": raw},
            }

        coverage = {
            "aaa_cfo_2021": [
                match("AAA", "aaa_cfo_2021", "100", 2021),
                match("AAA", "aaa_cfo_2021", "100", 2022),
            ],
            "aaa_profit_2021": [match("AAA", "aaa_profit_2021", "10", 2021)],
            "bbb_cfo_2021": [match("BBB", "bbb_cfo_2021", "200", 2021)],
            "bbb_profit_2021": [match("BBB", "bbb_profit_2021", "20", 2021)],
        }
        selected, reasons = select_coherent_operand_matches(
            coverage,
            operands,
            allow_equivalent_comparative_witnesses=True,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(set(selected), set(coverage))
        self.assertEqual(selected["aaa_cfo_2021"]["report_year"], 2021)
        self.assertEqual(
            selected["aaa_cfo_2021"]["selection_policy"],
            "same_year_primary_with_identical_comparative_witnesses_v1",
        )

    def test_source_discovery_uses_only_resolved_source_metadata(self):
        formula = {
            "operands": [
                {"operand_id": "assets", "years": [2022], "required": True},
            ]
        }
        item = {
            "question_plan": {"tickers": ["ABC"], "scope": "separate"},
            "candidates": [{"internal_table_uid": "already_retrieved"}],
        }
        tables = {
            "already_retrieved": {
                "internal_table_uid": "already_retrieved",
                "ticker": "ABC",
                "scope": "separate",
                "report_year": 2022,
            },
            "same_year": {
                "internal_table_uid": "same_year",
                "ticker": "ABC",
                "scope": "separate",
                "report_year": 2022,
            },
            "following_year": {
                "internal_table_uid": "following_year",
                "ticker": "ABC",
                "scope": "separate",
                "report_year": 2023,
            },
            "other_year": {
                "internal_table_uid": "other_year",
                "ticker": "ABC",
                "scope": "separate",
                "report_year": 2024,
            },
            "other_scope": {
                "internal_table_uid": "other_scope",
                "ticker": "ABC",
                "scope": "consolidated",
                "report_year": 2022,
            },
            "other_ticker": {
                "internal_table_uid": "other_ticker",
                "ticker": "XYZ",
                "scope": "separate",
                "report_year": 2022,
            },
        }
        discovered = source_discovery_candidates(item, formula, tables)
        self.assertEqual(
            [candidate["internal_table_uid"] for candidate in discovered],
            ["same_year", "following_year"],
        )
        self.assertTrue(
            all(
                candidate["candidate_source"] == FORMULA_SOURCE_DISCOVERY_CANDIDATE_SOURCE
                for candidate in discovered
            )
        )

    def test_source_discovery_requires_explicit_ticker_and_operand_year(self):
        table = {
            "internal_table_uid": "u1",
            "ticker": "ABC",
            "report_year": 2023,
        }
        self.assertEqual(
            source_discovery_candidates(
                {"question_plan": {"tickers": []}},
                {"operands": [{"years": [2023]}]},
                {"u1": table},
            ),
            [],
        )
        self.assertEqual(
            source_discovery_candidates(
                {"question_plan": {"tickers": ["ABC"]}},
                {"operands": [{"years": []}]},
                {"u1": table},
            ),
            [],
        )

    def test_source_completion_candidate_keeps_explicit_provenance(self):
        formula = {"operands": [{"operand_id": "profit", "years": [2023], "required": True}]}
        item = {"question_plan": {"tickers": ["ABC"]}, "candidates": []}
        candidates = source_discovery_candidates(
            item,
            formula,
            {
                "raw-u1": {
                    "internal_table_uid": "raw-u1",
                    "ticker": "ABC",
                    "scope": "consolidated",
                    "report_year": 2023,
                    "document_id": "ABC_financial_statements_2023_consolidated",
                    "local_ordinal": 8,
                    "source_completion": {"candidate_source": "raw_source_completion_v1"},
                }
            },
        )
        self.assertEqual(candidates[0]["candidate_source"], "raw_source_completion_v1")

    def test_bundle_formula_support_keeps_explicit_provenance(self):
        formula = {"operands": [{"operand_id": "profit", "years": [2023], "required": True}]}
        item = {"question_plan": {"tickers": ["ABC"]}, "candidates": []}
        candidates = source_discovery_candidates(
            item,
            formula,
            {
                "support-u1": {
                    "internal_table_uid": "support-u1",
                    "ticker": "ABC",
                    "scope": "consolidated",
                    "report_year": 2023,
                    "document_id": "ABC_financial_statements_2023_consolidated",
                    "local_ordinal": 8,
                    "bundle_inclusion": {
                        "formula_metadata_support": {
                            "policy": "resolved_operand_entity_year_or_following_statement_function_v1"
                        }
                    },
                }
            },
        )
        self.assertEqual(
            candidates[0]["candidate_source"],
            FORMULA_BUNDLE_SUPPORT_CANDIDATE_SOURCE,
        )

    def test_operand_binds_exact_row_and_explicit_year_cell(self):
        table = _table()
        context = build_evidence_context(table)
        operand = {
            "operand_id": "assets",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2023],
            "required": True,
        }
        candidate = {"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}
        matches = operand_evidence_matches(operand, candidate, table, context)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["row_index"], 1)
        self.assertEqual(matches[0]["binding"]["column_index"], 1)
        self.assertEqual(matches[0]["binding"]["raw_value"], "100")

    def test_missing_year_is_not_implicitly_bound(self):
        table = _table()
        context = build_evidence_context(table)
        operand = {
            "operand_id": "assets",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2021],
            "required": True,
        }
        candidate = {"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}
        self.assertEqual(operand_evidence_matches(operand, candidate, table, context), [])

    def test_ocr_concatenated_numeric_cell_cannot_become_formula_operand(self):
        table = _table()
        table["rows"][1][1] = "(72.193.585.614)(27.471.160.925)"
        context = build_evidence_context(table)
        operand = {
            "operand_id": "assets",
            "metric_hints": ["tài sản ngắn hạn"],
            "years": [2023],
            "required": True,
        }
        candidate = {"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}
        self.assertEqual(operand_evidence_matches(operand, candidate, table, context), [])

    def test_set_stays_partial_when_a_required_operand_has_no_exact_cell(self):
        table = _table()
        context = build_evidence_context(table)
        formula = {
            "formula_id": "ratio",
            "operands": [
                {"operand_id": "assets", "metric_hints": ["tài sản ngắn hạn"], "years": [2023], "required": True},
                {"operand_id": "inventory", "metric_hints": ["hàng tồn kho"], "years": [2023], "required": True},
            ],
        }
        item = {
            "id": 1,
            "question": "test",
            "question_plan": {"family": "ratio_or_derived", "tickers": ["ABC"]},
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}],
        }
        evidence = formula_evidence_set(formula, item, {"u1": table}, {"u1": context})
        self.assertEqual(evidence["evidence_completeness"], "partial")
        self.assertEqual(evidence["missing_operand_ids"], ["inventory"])

    def test_complete_operands_do_not_complete_a_conditional_question(self):
        table = _table()
        context = build_evidence_context(table)
        formula = {
            "formula_id": "ratio",
            "definition_status": "defined",
            "operands": [
                {"operand_id": "assets", "metric_hints": ["tài sản ngắn hạn"], "years": [2023], "required": True},
                {"operand_id": "liabilities", "metric_hints": ["nợ ngắn hạn"], "years": [2023], "required": True},
            ],
        }
        item = {
            "id": 1,
            "question": "test",
            "question_plan": {"family": "conditional_analytical", "tickers": ["ABC"]},
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}],
        }
        evidence = formula_evidence_set(formula, item, {"u1": table}, {"u1": context})
        self.assertEqual(evidence["operand_coverage_status"], "complete")
        self.assertEqual(evidence["evidence_completeness"], "partial")
        self.assertIn("question_family_requires_composed_execution", evidence["reason_codes"])

    def test_unique_single_entity_operands_can_be_complete_source_evidence(self):
        table = _table()
        context = build_evidence_context(table)
        formula = {
            "formula_id": "ratio",
            "definition_status": "defined",
            "operands": [
                {"operand_id": "assets", "metric_hints": ["tài sản ngắn hạn"], "years": [2023], "required": True},
                {"operand_id": "liabilities", "metric_hints": ["nợ ngắn hạn"], "years": [2023], "required": True},
            ],
        }
        item = {
            "id": 1,
            "question": "test",
            "question_plan": {"family": "ratio_or_derived", "tickers": ["ABC"]},
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "scope": "separate", "report_year": 2023}],
        }
        evidence = formula_evidence_set(formula, item, {"u1": table}, {"u1": context})
        self.assertEqual(evidence["evidence_completeness"], "complete")
        self.assertEqual(set(evidence["selected_operand_matches"]), {"assets", "liabilities"})

    def test_multi_entity_operands_require_one_common_scope_not_one_ticker(self):
        aaa = _entity_table("aaa", "AAA")
        dcm = _entity_table("dcm", "DCM")
        contexts = {"aaa": build_evidence_context(aaa), "dcm": build_evidence_context(dcm)}
        formula = {
            "formula_id": "controlled_group_program",
            "definition_status": "defined",
            "operands": [
                {
                    "operand_id": "aaa_assets",
                    "entity": "AAA",
                    "metric_hints": ["tài sản ngắn hạn"],
                    "years": [2023],
                    "required": True,
                },
                {
                    "operand_id": "dcm_liabilities",
                    "entity": "DCM",
                    "metric_hints": ["nợ ngắn hạn"],
                    "years": [2023],
                    "required": True,
                },
            ],
        }
        item = {
            "id": 1,
            "question": "test",
            "question_plan": {"family": "ratio_or_derived", "tickers": ["AAA", "DCM"]},
            "candidates": [
                {"internal_table_uid": "aaa", "rank": 1, "ticker": "AAA", "scope": "consolidated", "report_year": 2023},
                {"internal_table_uid": "dcm", "rank": 2, "ticker": "DCM", "scope": "consolidated", "report_year": 2023},
            ],
        }
        evidence = formula_evidence_set(formula, item, {"aaa": aaa, "dcm": dcm}, contexts)
        self.assertEqual(evidence["evidence_completeness"], "complete")
        self.assertEqual(set(evidence["selected_operand_matches"]), {"aaa_assets", "dcm_liabilities"})

    def test_multi_entity_operands_fail_closed_without_a_common_scope(self):
        aaa = _entity_table("aaa", "AAA")
        dcm = _entity_table("dcm", "DCM")
        contexts = {"aaa": build_evidence_context(aaa), "dcm": build_evidence_context(dcm)}
        formula = {
            "formula_id": "controlled_group_program",
            "definition_status": "defined",
            "operands": [
                {"operand_id": "aaa_assets", "entity": "AAA", "metric_hints": ["tài sản ngắn hạn"], "years": [2023], "required": True},
                {"operand_id": "dcm_liabilities", "entity": "DCM", "metric_hints": ["nợ ngắn hạn"], "years": [2023], "required": True},
            ],
        }
        item = {
            "id": 1,
            "question": "test",
            "question_plan": {"family": "ratio_or_derived", "tickers": ["AAA", "DCM"]},
            "candidates": [
                {"internal_table_uid": "aaa", "rank": 1, "ticker": "AAA", "scope": "separate", "report_year": 2023},
                {"internal_table_uid": "dcm", "rank": 2, "ticker": "DCM", "scope": "consolidated", "report_year": 2023},
            ],
        }
        evidence = formula_evidence_set(formula, item, {"aaa": aaa, "dcm": dcm}, contexts)
        self.assertEqual(evidence["evidence_completeness"], "partial")
        self.assertEqual(evidence["selected_operand_matches"], {})
        self.assertIn("no_common_scope_across_entity_operands", evidence["reason_codes"])
        self.assertEqual(
            evidence["scope_diagnostics"]["entity_common_scopes"],
            {"AAA": ["separate"], "DCM": ["consolidated"]},
        )
        self.assertEqual(evidence["scope_diagnostics"]["global_common_scopes"], [])

    def test_scope_diagnostics_lists_each_operand_without_selecting_a_scope(self):
        diagnostics = multi_entity_scope_diagnostics(
            {
                "a_cash": [{"ticker": "AAA", "scope": "separate"}],
                "a_debt": [{"ticker": "AAA", "scope": "consolidated"}],
                "b_cash": [{"ticker": "BBB", "scope": "consolidated"}],
            },
            [
                {"operand_id": "a_cash", "entity": "AAA"},
                {"operand_id": "a_debt", "entity": "AAA"},
                {"operand_id": "b_cash", "entity": "BBB"},
            ],
        )
        self.assertEqual(diagnostics["entity_common_scopes"], {"AAA": [], "BBB": ["consolidated"]})
        self.assertEqual(diagnostics["global_common_scopes"], [])
        self.assertEqual(
            diagnostics["operand_scope_sets"]["AAA"],
            {"a_cash": ["separate"], "a_debt": ["consolidated"]},
        )

    def test_candidate_with_wrong_ticker_cannot_supply_formula_evidence(self):
        table = _table()
        context = build_evidence_context(table)
        formula = {
            "formula_id": "ratio",
            "operands": [{"operand_id": "assets", "metric_hints": ["tài sản ngắn hạn"], "years": [2023], "required": True}],
        }
        item = {
            "id": 1,
            "question": "test",
            "question_plan": {"family": "ratio_or_derived", "tickers": ["XYZ"]},
            "candidates": [{"internal_table_uid": "u1", "rank": 1, "ticker": "ABC", "report_year": 2023}],
        }
        evidence = formula_evidence_set(formula, item, {"u1": table}, {"u1": context})
        self.assertEqual(evidence["evidence_completeness"], "partial")
        self.assertIn("candidate_ticker_mismatch", evidence["candidate_gate_rejections"])
