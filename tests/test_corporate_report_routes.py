import json
import tempfile
import unittest
from pathlib import Path

from finance_query.corporate_report_routes import (
    CORPORATE_REPORT_ROUTE_HINT_PROTOCOL,
    CORPORATE_REPORT_ROUTE_HINT_VERSION,
    ROUTE_HINT_SOURCE_CONTRACT,
    build_corporate_report_route_hints,
    validate_corporate_report_route_hints,
)
from finance_query.table_structure import sha256_file


def review(question_id, *, scope="separate", question="Chỉ tiêu của VJC năm 2022"):
    return {
        "id": question_id,
        "question": question,
        "question_plan": {"tickers": ["VJC"], "years": [2022], "scope": scope},
    }


class CorporateReportRouteHintTests(unittest.TestCase):
    def test_hints_select_one_same_scope_document_and_exclude_parallel_scope(self):
        families = [
            {
                "issuer_ticker": "VJC",
                "report_year": 2022,
                "report_type": "financial_statements",
                "family_status": "source_derived_unique_scope_members",
                "members": [
                    {"document_id": "VJC_2022_separate", "report_scope": "separate"},
                    {"document_id": "VJC_2022_consolidated", "report_scope": "consolidated"},
                ],
            }
        ]
        edges = [
            {
                "relation_type": "adjacent_reporting_period_candidate",
                "period_alignment_status": "matching_source_period_end",
                "source_document_id": "VJC_2022_separate",
                "target_document_id": "VJC_2023_separate",
            }
        ]
        corporate = [
            {
                "issuer_ticker": "VJC",
                "report_year": 2022,
                "report_scope": "separate",
                "document_id": "VJC_2022_separate",
                "target_canonical_entity": "vietjetair cargo",
                "internal_table_uid": "investment-table",
            }
        ]
        hint = build_corporate_report_route_hints(
            [review(1, question="Khoản đầu tư của Công ty Cổ phần VietjetAir Cargo năm 2022 của VJC")],
            families,
            edges,
            corporate,
        )[0]
        self.assertEqual(hint["route_hint_status"], "exact_scope_source_document_found")
        self.assertEqual(hint["primary_document_ids"], ["VJC_2022_separate"])
        self.assertEqual(hint["scope_exclusion_document_ids"], ["VJC_2022_consolidated"])
        self.assertEqual(hint["adjacent_period_document_ids"], ["VJC_2023_separate"])
        self.assertEqual(hint["relationship_table_uids"], ["investment-table"])
        self.assertFalse(hint["source_contract"]["may_select_value_cell"])

    def test_missing_scope_does_not_select_a_report(self):
        hint = build_corporate_report_route_hints(
            [review(1, scope="unknown")], [], [], []
        )[0]
        self.assertEqual(hint["route_hint_status"], "question_context_incomplete_or_ambiguous")
        self.assertEqual(hint["primary_document_ids"], [])

    def test_manifest_rejects_tampered_route_hint(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            hints = output / "corporate_report_route_hints_v1.jsonl"
            hints.write_text(
                json.dumps(
                    {
                        "question_id": 1,
                        "source_contract": ROUTE_HINT_SOURCE_CONTRACT,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest = {
                "schema_version": CORPORATE_REPORT_ROUTE_HINT_VERSION,
                "protocol": CORPORATE_REPORT_ROUTE_HINT_PROTOCOL,
                "question_count": 1,
                "hints_sha256": sha256_file(hints),
                "source_contract": ROUTE_HINT_SOURCE_CONTRACT,
            }
            (output / "corporate_report_route_hints_v1.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            self.assertEqual(
                validate_corporate_report_route_hints(output)["question_count"], 1
            )
            hints.write_text('{"question_id": 1}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                validate_corporate_report_route_hints(output)


if __name__ == "__main__":
    unittest.main()
