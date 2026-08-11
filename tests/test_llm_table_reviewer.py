import unittest

from finance_query.llm_table_reviewer import (
    LLM_TABLE_REVIEW_PROTOCOL,
    build_review_packet,
    verify_llm_decision,
)


def packet() -> dict:
    value = {
        "schema_version": 1,
        "protocol": LLM_TABLE_REVIEW_PROTOCOL,
        "question_id": 1,
        "required_bindings": [
            {"company": "HPG", "variable_id": "current_assets"},
            {"company": "HPG", "variable_id": "inventory"},
        ],
        "candidates": [
            {
                "candidate_id": "HPG|current_assets|asset-table|2",
                "company": "HPG",
                "variable_id": "current_assets",
                "internal_table_uid": "asset-table",
                "row_index": 2,
                "raw_row_label": "Tài sản ngắn hạn",
                "available_value_cells": [
                    {"column_index": 3, "canonical_header": "31/12/2022 VND", "raw_value": "100"}
                ],
            },
            {
                "candidate_id": "HPG|inventory|asset-table|14",
                "company": "HPG",
                "variable_id": "inventory",
                "internal_table_uid": "asset-table",
                "row_index": 14,
                "raw_row_label": "Hàng tồn kho",
                "available_value_cells": [
                    {"column_index": 3, "canonical_header": "31/12/2022 VND", "raw_value": "20"}
                ],
            },
        ],
        "source_contract": {},
    }
    from finance_query.llm_table_reviewer import _sha256_payload

    value["packet_sha256"] = _sha256_payload(value)
    return value


class LlmTableReviewerTests(unittest.TestCase):
    def test_literal_citations_become_machine_provisional_only(self):
        decision = {
            "verdict": "supported",
            "final_answer": None,
            "selected_bindings": [
                {
                    "candidate_id": "HPG|current_assets|asset-table|2",
                    "column_index": 3,
                    "canonical_header": "31/12/2022 VND",
                    "raw_value": "100",
                },
                {
                    "candidate_id": "HPG|inventory|asset-table|14",
                    "column_index": 3,
                    "canonical_header": "31/12/2022 VND",
                    "raw_value": "20",
                },
            ],
        }
        result = verify_llm_decision(packet(), decision, self_critique={"verdict": "approve"})
        self.assertEqual(result["annotation_status"], "machine_provisional")
        self.assertFalse(result["human_verified"])
        self.assertTrue(result["requires_independent_replay"])

    def test_final_answer_without_a_literal_citation_is_blocked(self):
        decision = {"verdict": "supported", "final_answer": "80", "selected_bindings": []}
        result = verify_llm_decision(packet(), decision)
        self.assertEqual(result["annotation_status"], "needs_human")
        self.assertEqual(result["reason_codes"], ["llm_self_inference_detected"])

    def test_wrong_raw_value_is_blocked(self):
        decision = {
            "verdict": "supported",
            "final_answer": None,
            "selected_bindings": [
                {
                    "candidate_id": "HPG|current_assets|asset-table|2",
                    "column_index": 3,
                    "canonical_header": "31/12/2022 VND",
                    "raw_value": "101",
                }
            ],
        }
        result = verify_llm_decision(packet(), decision)
        self.assertEqual(result["reason_codes"], ["llm_citation_not_literal"])

    def test_abstention_preserves_feedback_without_promoting_provenance(self):
        decision = {
            "verdict": "no_candidate",
            "final_answer": None,
            "selected_bindings": [],
            "feedback": {"reason_code": "missing_current_assets", "detail": "No exact row"},
        }
        result = verify_llm_decision(packet(), decision)
        self.assertEqual(result["provenance_status"], "machine_abstained")
        self.assertEqual(result["feedback"]["reason_code"], "missing_current_assets")

    def test_non_target_period_is_rejected_even_when_the_citation_is_literal(self):
        source_packet = packet()
        source_packet["stage"] = {"year": 2022}
        for candidate in source_packet["candidates"]:
            for cell in candidate["available_value_cells"]:
                cell["canonical_header"] = "31/12/2023 VND"
                cell["period_labels"] = ["31/12/2023"]
        from finance_query.llm_table_reviewer import _sha256_payload

        source_packet["packet_sha256"] = _sha256_payload(
            {key: value for key, value in source_packet.items() if key != "packet_sha256"}
        )
        decision = {
            "verdict": "supported",
            "final_answer": None,
            "selected_bindings": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "column_index": 3,
                    "canonical_header": "31/12/2023 VND",
                    "raw_value": candidate["available_value_cells"][0]["raw_value"],
                }
                for candidate in source_packet["candidates"]
            ],
        }
        result = verify_llm_decision(source_packet, decision)
        self.assertEqual(result["reason_codes"], ["llm_non_target_period_cell"])

    def test_mixed_scope_bindings_are_rejected_under_a_resolved_scope_contract(self):
        source_packet = packet()
        source_packet["candidates"][0]["report_scope"] = "consolidated"
        source_packet["candidates"][1]["report_scope"] = "separate"
        source_packet["scope_contract"] = {
            "status": "resolved",
            "viable_report_scopes": ["consolidated"],
            "must_use_one_uniform_scope": True,
        }
        from finance_query.llm_table_reviewer import _sha256_payload

        source_packet["packet_sha256"] = _sha256_payload(
            {key: value for key, value in source_packet.items() if key != "packet_sha256"}
        )
        decision = {
            "verdict": "supported",
            "final_answer": None,
            "selected_bindings": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "column_index": 3,
                    "canonical_header": "31/12/2022 VND",
                    "raw_value": candidate["available_value_cells"][0]["raw_value"],
                }
                for candidate in source_packet["candidates"]
            ],
        }
        result = verify_llm_decision(source_packet, decision)
        self.assertEqual(result["reason_codes"], ["llm_inconsistent_report_scope"])

    def test_packet_excludes_reference_and_opening_balance_columns(self):
        candidate_result = {
            "status": "candidate_tables_found",
            "candidate_table_uids_by_entity_variable": {"HPG": {"current_assets": ["table-1"]}},
        }
        catalog = {
            "table-1": {
                "table_type": "balance_sheet",
                "canonical_variables": [
                    {
                        "variable_id": "current_assets",
                        "row_index": 1,
                        "raw_row_label": "Tài sản ngắn hạn",
                    }
                ],
            }
        }
        tables = {
            "table-1": {
                "internal_table_uid": "table-1",
                "document_id": "doc-1",
                "rows": [["Chỉ tiêu", "Mã số", "31/12", "30/09", "01/01"], ["Tài sản", "100", "80", "70", "90"]],
            }
        }
        contexts = {
            "table-1": {
                "canonical_headers": {
                    "columns": [
                        {"source_label": "Chỉ tiêu", "role": "row_label"},
                        {"source_label": "Mã số", "role": "reference"},
                        {
                            "source_label": "31/12/2022 VND",
                            "role": "value_or_text",
                            "period_labels": ["31/12/2022"],
                            "unit_labels": ["VND"],
                        },
                        {
                            "source_label": "30/09/2022 VND",
                            "role": "value_or_text",
                            "period_labels": ["30/09/2022"],
                            "unit_labels": ["VND"],
                        },
                        {
                            "source_label": "01/01/2022 VND",
                            "role": "value_or_text",
                            "period_labels": ["01/01/2022"],
                            "unit_labels": ["VND"],
                        },
                    ]
                }
            }
        }
        review_packet = build_review_packet(
            question_id=1,
            question="Tài sản ngắn hạn năm 2022 của HPG",
            stage={"stage_id": "assets", "year": 2022},
            candidate_result=candidate_result,
            catalog_by_uid=catalog,
            tables_by_uid=tables,
            contexts_by_uid=contexts,
        )
        cells = review_packet["candidates"][0]["available_value_cells"]
        self.assertEqual(cells, [{"column_index": 2, "canonical_header": "31/12/2022 VND", "raw_value": "80", "period_labels": ["31/12/2022"], "unit_labels": ["VND"]}])
        self.assertEqual(review_packet["missing_source_cell_bindings"], [])

    def test_packet_accepts_documented_non_calendar_fiscal_year_end(self):
        candidate_result = {
            "status": "candidate_tables_found",
            "candidate_table_uids_by_entity_variable": {"HSG": {"current_assets": ["table-1"]}},
        }
        catalog = {
            "table-1": {
                "table_type": "balance_sheet",
                "reporting_period_end": {"day": 30, "month": 9, "year": 2022},
                "canonical_variables": [
                    {
                        "variable_id": "current_assets",
                        "row_index": 1,
                        "raw_row_label": "Tài sản ngắn hạn",
                    }
                ],
            }
        }
        tables = {
            "table-1": {
                "internal_table_uid": "table-1",
                "document_id": "doc-1",
                "rows": [["Chỉ tiêu", "Mã số", "30/09", "01/10"], ["Tài sản", "100", "80", "90"]],
            }
        }
        contexts = {
            "table-1": {
                "canonical_headers": {
                    "columns": [
                        {"source_label": "Chỉ tiêu", "role": "row_label"},
                        {"source_label": "Mã số", "role": "reference"},
                        {
                            "source_label": "30/9/2022 VND",
                            "role": "value_or_text",
                            "period_labels": ["2022"],
                            "unit_labels": ["VND"],
                        },
                        {
                            "source_label": "1/10/2021 VND",
                            "role": "value_or_text",
                            "period_labels": ["2021"],
                            "unit_labels": ["VND"],
                        },
                    ]
                }
            }
        }
        review_packet = build_review_packet(
            question_id=1,
            question="Tài sản ngắn hạn năm 2022 của HSG",
            stage={"stage_id": "assets", "year": 2022},
            candidate_result=candidate_result,
            catalog_by_uid=catalog,
            tables_by_uid=tables,
            contexts_by_uid=contexts,
        )
        self.assertEqual(
            review_packet["candidates"][0]["available_value_cells"][0]["canonical_header"],
            "30/9/2022 VND",
        )
        self.assertEqual(review_packet["missing_source_cell_bindings"], [])

    def test_packet_accepts_year_only_header_when_source_document_has_fiscal_end(self):
        candidate_result = {
            "status": "candidate_tables_found",
            "candidate_table_uids_by_entity_variable": {"MSR": {"net_income": ["table-1"]}},
        }
        catalog = {
            "table-1": {
                "table_type": "income_statement",
                "reporting_period_end": {"day": 31, "month": 12, "year": 2022},
                "canonical_variables": [
                    {"variable_id": "net_income", "row_index": 1, "raw_row_label": "Lợi nhuận sau thuế"}
                ],
            }
        }
        tables = {
            "table-1": {
                "internal_table_uid": "table-1",
                "document_id": "doc-1",
                "rows": [["Chỉ tiêu", "Năm"], ["Lợi nhuận sau thuế", "105"]],
            }
        }
        contexts = {
            "table-1": {
                "canonical_headers": {
                    "columns": [
                        {"source_label": "Chỉ tiêu", "role": "row_label"},
                        {
                            "source_label": "2022 Nghìn VND",
                            "role": "value_or_text",
                            "period_labels": ["2022"],
                            "unit_labels": ["Nghìn VND"],
                        },
                    ]
                }
            }
        }
        review_packet = build_review_packet(
            question_id=1,
            question="Lợi nhuận sau thuế năm 2022 của MSR",
            stage={"stage_id": "income", "year": 2022},
            candidate_result=candidate_result,
            catalog_by_uid=catalog,
            tables_by_uid=tables,
            contexts_by_uid=contexts,
        )
        self.assertEqual(review_packet["missing_source_cell_bindings"], [])
        self.assertEqual(
            review_packet["candidates"][0]["available_value_cells"][0]["canonical_header"],
            "2022 Nghìn VND",
        )

    def test_packet_cap_is_applied_after_invalid_periods_are_filtered(self):
        candidate_result = {
            "status": "candidate_tables_found",
            "candidate_table_uids_by_entity_variable": {"HPG": {"current_assets": ["interim", "annual"]}},
        }
        catalog = {
            uid: {
                "table_type": "balance_sheet",
                "report_scope": "consolidated",
                "canonical_variables": [
                    {"variable_id": "current_assets", "row_index": 1, "raw_row_label": "Tài sản ngắn hạn"}
                ],
            }
            for uid in ("interim", "annual")
        }
        tables = {
            uid: {"internal_table_uid": uid, "document_id": uid, "rows": [["Chỉ tiêu", "Kỳ"], ["Tài sản", value]]}
            for uid, value in (("interim", "70"), ("annual", "80"))
        }
        contexts = {
            "interim": {
                "canonical_headers": {
                    "columns": [
                        {"source_label": "Chỉ tiêu", "role": "row_label"},
                        {"source_label": "30/09/2022 VND", "role": "value_or_text", "period_labels": ["30/09/2022"]},
                    ]
                }
            },
            "annual": {
                "canonical_headers": {
                    "columns": [
                        {"source_label": "Chỉ tiêu", "role": "row_label"},
                        {"source_label": "31/12/2022 VND", "role": "value_or_text", "period_labels": ["31/12/2022"]},
                    ]
                }
            },
        }
        review_packet = build_review_packet(
            question_id=1,
            question="Tài sản ngắn hạn năm 2022 của HPG",
            stage={"stage_id": "assets", "year": 2022},
            candidate_result=candidate_result,
            catalog_by_uid=catalog,
            tables_by_uid=tables,
            contexts_by_uid=contexts,
            max_tables_per_binding=1,
        )
        self.assertEqual([candidate["internal_table_uid"] for candidate in review_packet["candidates"]], ["annual"])

    def test_packet_requires_one_target_year_document_per_company(self):
        """Never let one ratio combine a target report with a comparative one."""
        variables = ("current_assets", "inventory", "current_liabilities")
        candidate_result = {
            "status": "candidate_tables_found",
            "candidate_table_uids_by_entity_variable": {
                "HPG": {
                    variable: [f"target-{variable}", f"comparative-{variable}"]
                    for variable in variables
                }
            },
        }
        catalog = {}
        tables = {}
        contexts = {}
        values = {"current_assets": "100", "inventory": "20", "current_liabilities": "40"}
        for source, document_id, report_year in (
            ("target", "hpg-financial-statements-2022", 2022),
            ("comparative", "hpg-financial-statements-2023", 2023),
        ):
            for variable in variables:
                uid = f"{source}-{variable}"
                catalog[uid] = {
                    "table_type": "balance_sheet",
                    "report_scope": "consolidated",
                    "report_year": report_year,
                    "canonical_variables": [
                        {"variable_id": variable, "row_index": 1, "raw_row_label": variable}
                    ],
                }
                tables[uid] = {
                    "internal_table_uid": uid,
                    "document_id": document_id,
                    "rows": [["Chỉ tiêu", "Kỳ"], [variable, values[variable]]],
                }
                contexts[uid] = {
                    "canonical_headers": {
                        "columns": [
                            {"source_label": "Chỉ tiêu", "role": "row_label"},
                            {
                                "source_label": "31/12/2022 VND",
                                "role": "value_or_text",
                                "period_labels": ["31/12/2022"],
                                "unit_labels": ["VND"],
                            },
                        ]
                    }
                }
        review_packet = build_review_packet(
            question_id=1,
            question="Quick ratio HPG năm 2022",
            stage={"stage_id": "quick_ratio", "year": 2022},
            candidate_result=candidate_result,
            catalog_by_uid=catalog,
            tables_by_uid=tables,
            contexts_by_uid=contexts,
        )
        self.assertEqual(review_packet["scope_contract"]["status"], "resolved")
        self.assertEqual(review_packet["document_contract"]["status"], "resolved")
        self.assertEqual(
            review_packet["document_contract"]["selected_document_ids"],
            {"HPG": "hpg-financial-statements-2022"},
        )
        self.assertEqual(
            {candidate["document_id"] for candidate in review_packet["candidates"]},
            {"hpg-financial-statements-2022"},
        )

    def test_verifier_blocks_a_literal_from_an_unselected_source_document(self):
        source_packet = packet()
        source_packet["candidates"][0].update(
            {"report_scope": "consolidated", "document_id": "hpg-2022"}
        )
        source_packet["candidates"][1].update(
            {"report_scope": "consolidated", "document_id": "hpg-2023"}
        )
        source_packet["scope_contract"] = {
            "status": "resolved",
            "viable_report_scopes": ["consolidated"],
            "must_use_one_uniform_scope": True,
        }
        source_packet["document_contract"] = {
            "status": "resolved",
            "selected_document_ids": {"HPG": "hpg-2022"},
            "viable_document_ids_by_company": {"HPG": ["hpg-2022"]},
            "must_use_one_document_per_company": True,
        }
        from finance_query.llm_table_reviewer import _sha256_payload

        source_packet["packet_sha256"] = _sha256_payload(
            {key: value for key, value in source_packet.items() if key != "packet_sha256"}
        )
        decision = {
            "verdict": "supported",
            "final_answer": None,
            "selected_bindings": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "column_index": 3,
                    "canonical_header": "31/12/2022 VND",
                    "raw_value": candidate["available_value_cells"][0]["raw_value"],
                }
                for candidate in source_packet["candidates"]
            ],
        }
        result = verify_llm_decision(source_packet, decision)
        self.assertEqual(result["reason_codes"], ["llm_inconsistent_source_document"])


if __name__ == "__main__":
    unittest.main()
