import unittest

from finance_query.route_packets import build_navigation_index, build_route_packet


def source_contract() -> dict[str, bool]:
    return {
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }


def source_records(entries):
    """Create mutually consistent source records for metadata-only packet tests."""
    candidates = []
    routing = []
    documents = []
    tables = []
    roles = []
    sectors = []
    for entry in entries:
        uid = entry["uid"]
        document_id = entry["document_id"]
        row = [entry.get("label", "Tài sản ngắn hạn"), entry.get("amount", "100")]
        table_type = entry.get("table_type", "balance_sheet")
        table_type_status = entry.get("table_type_status", "source_structural")
        routing_eligible = entry.get("routing_eligible", True)
        navigation_gate_status = entry.get("navigation_gate_status", "ready")
        routing.append(
            {
                "internal_table_uid": uid,
                "document_id": document_id,
                "company": entry.get("company", "HPG"),
                "report_year": entry.get("year", 2022),
                "report_scope": entry.get("scope", "consolidated"),
                "available_period_years": entry.get("available_period_years", []),
                "routing_eligible": routing_eligible,
                "table_type": table_type,
                "table_type_status": table_type_status,
            }
        )
        documents.append(
            {
                "document_id": document_id,
                "company": entry.get("company", "HPG"),
                "report_year": entry.get("year", 2022),
                "report_scope": entry.get("scope", "consolidated"),
            }
        )
        tables.append(
            {
                "internal_table_uid": uid,
                "document_id": document_id,
                "rows": [["Chỉ tiêu", "Số tiền"], row],
            }
        )
        roles.append(
            {
                "internal_table_uid": uid,
                "document_id": document_id,
                "existing_table_type": table_type,
                "existing_table_type_status": table_type_status,
                "proposed_table_type": entry.get("proposed_table_type"),
                "status": entry.get("role_status", "source_structural"),
                "reason_codes": entry.get("role_reason_codes", []),
            }
        )
        sectors.append(
            {
                "document_id": document_id,
                "sector": entry.get("sector", "unknown"),
            }
        )
        candidates.append(
            {
                "internal_table_uid": uid,
                "document_id": document_id,
                "row_index": 1,
                "raw_source_row": row,
                "table_type": table_type,
                "table_type_status": table_type_status,
                "routing_eligible": routing_eligible,
                "navigation_gate_status": navigation_gate_status,
                "navigation_reason_codes": entry.get("navigation_reason_codes", []),
                "match_status": "exact_unique",
                "source_account_codes": entry.get("source_account_codes", []),
                "concept_candidates": [
                    {
                        "concept_id": entry.get("concept_id", "current_assets"),
                        "constraints_applied": entry.get("constraints_applied", []),
                    }
                ],
                "source_contract": source_contract(),
            }
        )
    return {
        "candidate_rows": candidates,
        "routing_rows": routing,
        "document_rows": documents,
        "structured_rows": tables,
        "table_role_rows": roles,
        "sector_rows": sectors,
    }


def route(*, status="metric_candidate", scope="consolidated", entities=None, years=None, sectors=None):
    return {
        "question_id": 7,
        "question": "Hệ số thanh toán nhanh của HPG năm 2022 là bao nhiêu?",
        "route_status": status,
        "question_context": {
            "entities": entities if entities is not None else ["HPG"],
            "years": years if years is not None else [2022],
            "scope": scope,
        },
        "stages": [
            {
                "stage_id": "stage_1_quick_ratio",
                "route_kind": "metric",
                "metric_id": "quick_ratio",
                "retrieval_filters": {"sectors": sectors if sectors is not None else []},
                "required_operands": [
                    {
                        "role": "current_assets",
                        "concept_id": "current_assets",
                        "concept_path": ["assets", "current_assets"],
                        "period_type": "instant",
                        "statement_types": ["balance_sheet"],
                    }
                ],
            }
        ],
        "feedback": {"suggested_next_action": "supply source-bound context"},
    }


class RoutePacketTests(unittest.TestCase):
    def test_exact_company_year_scope_table_and_concept_filter(self):
        index = build_navigation_index(
            **source_records(
                [
                    {"uid": "t-good", "document_id": "doc-good"},
                    {"uid": "t-entity", "document_id": "doc-entity", "company": "HSG"},
                    {"uid": "t-year", "document_id": "doc-year", "year": 2021},
                    {"uid": "t-scope", "document_id": "doc-scope", "scope": "separate"},
                    {"uid": "t-note", "document_id": "doc-note", "table_type": "notes"},
                    {
                        "uid": "t-other-concept",
                        "document_id": "doc-other-concept",
                        "concept_id": "inventory",
                    },
                ]
            )
        )
        packet = build_route_packet(route(), index=index)
        operand = packet["stages"][0]["required_operands"][0]
        self.assertEqual(packet["packet_status"], "bounded")
        self.assertEqual(operand["candidate_count_total"], 1)
        self.assertEqual(
            [candidate["internal_table_uid"] for candidate in operand["navigation_candidates"]],
            ["t-good"],
        )
        self.assertEqual(operand["navigation_candidates"][0]["period_match_basis"], "report_year")

    def test_scope_mismatch_is_no_candidate_without_fallback(self):
        index = build_navigation_index(
            **source_records(
                [{"uid": "t-separate", "document_id": "doc-separate", "scope": "separate"}]
            )
        )
        packet = build_route_packet(route(scope="consolidated"), index=index)
        operand = packet["stages"][0]["required_operands"][0]
        self.assertEqual(packet["packet_status"], "no_candidate")
        self.assertEqual(operand["candidate_count_total"], 0)
        self.assertEqual(operand["navigation_candidates"], [])
        self.assertEqual(operand["blocked_candidate_reason_counts"], {"SCOPE_MISMATCH": 1})
        self.assertEqual(
            packet["feedback"]["missing_operands"][0]["required_context"],
            {"entities": ["HPG"], "years": [2022], "scope": "consolidated"},
        )

    def test_available_period_year_is_accepted_without_column_selection(self):
        index = build_navigation_index(
            **source_records(
                [
                    {
                        "uid": "t-available-period",
                        "document_id": "doc-available-period",
                        "year": 2023,
                        "available_period_years": [2022],
                    }
                ]
            )
        )
        packet = build_route_packet(route(), index=index)
        candidate = packet["stages"][0]["required_operands"][0]["navigation_candidates"][0]
        self.assertEqual(packet["packet_status"], "bounded")
        self.assertEqual(candidate["period_match_basis"], "available_period_year")
        self.assertNotIn("column_index", candidate)

    def test_unknown_sector_fallback_requires_source_structural_account_code(self):
        index = build_navigation_index(
            **source_records(
                [
                    {
                        "uid": "t-account-code",
                        "document_id": "doc-account-code",
                        "table_type": "balance_sheet",
                        "navigation_gate_status": "blocked",
                        "navigation_reason_codes": ["sector_unresolved"],
                        "source_account_codes": ["100"],
                        "constraints_applied": ["statement_type", "sector", "account_code"],
                    }
                ]
            )
        )
        packet = build_route_packet(route(sectors=["industrial"]), index=index)
        candidate = packet["stages"][0]["required_operands"][0]["navigation_candidates"][0]
        self.assertEqual(packet["packet_status"], "bounded")
        self.assertEqual(
            candidate["navigation_admission_basis"],
            "source_structural_account_code_unknown_sector_fallback",
        )
        self.assertEqual(candidate["navigation_gate_status"], "blocked")

    def test_unknown_sector_fallback_rejects_missing_code_or_extra_gate_failure(self):
        entries = [
            {
                "uid": "t-no-code",
                "document_id": "doc-no-code",
                "table_type": "balance_sheet",
                "navigation_gate_status": "blocked",
                "navigation_reason_codes": ["sector_unresolved"],
                "constraints_applied": ["statement_type", "sector"],
            },
            {
                "uid": "t-extra-gate",
                "document_id": "doc-extra-gate",
                "table_type": "balance_sheet",
                "navigation_gate_status": "blocked",
                "navigation_reason_codes": ["sector_unresolved", "table_not_routing_eligible"],
                "source_account_codes": ["100"],
                "constraints_applied": ["statement_type", "sector", "account_code"],
            },
        ]
        index = build_navigation_index(**source_records(entries))
        packet = build_route_packet(route(sectors=["industrial"]), index=index)
        operand = packet["stages"][0]["required_operands"][0]
        self.assertEqual(packet["packet_status"], "no_candidate")
        self.assertEqual(operand["navigation_candidates"], [])
        self.assertEqual(operand["blocked_candidate_reason_counts"], {"NAVIGATION_GATE_NOT_READY": 2, "SECTOR_MISMATCH": 2})

    def test_abstain_route_is_blocked_and_never_looks_up_candidates(self):
        index = build_navigation_index(
            **source_records([{"uid": "t-good", "document_id": "doc-good"}])
        )
        packet = build_route_packet(route(status="abstain", scope=None), index=index)
        operand = packet["stages"][0]["required_operands"][0]
        self.assertEqual(packet["packet_status"], "route_blocked")
        self.assertEqual(operand["candidate_count_total"], 0)
        self.assertEqual(operand["navigation_candidates"], [])
        self.assertIn("ROUTE_STATUS_ABSTAIN", packet["feedback"]["reason_codes"])

    def test_auxiliary_role_is_vetoed_not_reinterpreted_as_primary_statement(self):
        index = build_navigation_index(
            **source_records(
                [
                    {
                        "uid": "t-auxiliary",
                        "document_id": "doc-auxiliary",
                        "table_type": "other",
                        "table_type_status": "metadata_provisional",
                        "role_status": "semantic_candidate",
                        "proposed_table_type": "income_statement",
                    }
                ]
            )
        )
        packet = build_route_packet(route(), index=index)
        operand = packet["stages"][0]["required_operands"][0]
        self.assertEqual(packet["packet_status"], "no_candidate")
        self.assertEqual(operand["navigation_candidates"], [])
        self.assertIn("AUXILIARY_OR_RESTATEMENT_TABLE_VETO", operand["blocked_candidate_reason_counts"])
        self.assertIn(
            "AUXILIARY_OR_RESTATEMENT_TABLE_ROLE_VETO",
            operand["blocked_candidate_reason_counts"],
        )

    def test_cap_and_order_are_deterministic(self):
        entries = [
            {"uid": "u3", "document_id": "document-c"},
            {"uid": "u1", "document_id": "document-a"},
            {"uid": "u2", "document_id": "document-b"},
        ]
        index = build_navigation_index(**source_records(entries))
        first = build_route_packet(route(), index=index, cap=2)
        second = build_route_packet(route(), index=index, cap=2)
        first_operand = first["stages"][0]["required_operands"][0]
        second_operand = second["stages"][0]["required_operands"][0]
        self.assertEqual(first_operand["candidate_count_total"], 3)
        self.assertEqual(first_operand["candidate_count_in_packet"], 2)
        self.assertTrue(first_operand["truncated"])
        self.assertEqual(
            [candidate["document_id"] for candidate in first_operand["navigation_candidates"]],
            ["document-a", "document-b"],
        )
        self.assertEqual(first_operand["navigation_candidates"], second_operand["navigation_candidates"])

    def test_source_coordinates_and_contract_remain_exact_and_non_promotable(self):
        index = build_navigation_index(
            **source_records([{"uid": "t-good", "document_id": "doc-good", "amount": "123"}])
        )
        packet = build_route_packet(route(), index=index)
        candidate = packet["stages"][0]["required_operands"][0]["navigation_candidates"][0]
        self.assertEqual(candidate["row_index"], 1)
        self.assertEqual(candidate["raw_source_row"], ["Tài sản ngắn hạn", "123"])
        self.assertNotIn("selected_candidate", candidate)
        self.assertNotIn("value", candidate)
        for key, value in packet["source_contract"].items():
            if key != "navigation_metadata_only":
                self.assertFalse(value)
        for key, value in candidate["source_contract"].items():
            if key != "navigation_metadata_only":
                self.assertFalse(value)

    def test_uid_coverage_mismatch_fails_closed(self):
        records = source_records([{"uid": "t-good", "document_id": "doc-good"}])
        records["structured_rows"][0]["internal_table_uid"] = "t-other"
        with self.assertRaisesRegex(ValueError, "UID coverage mismatch"):
            build_navigation_index(**records)


if __name__ == "__main__":
    unittest.main()
