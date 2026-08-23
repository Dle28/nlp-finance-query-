import json
import tempfile
import unittest
from pathlib import Path

from finance_query.report_layout_graph import LAYOUT_GRAPH_SOURCE_CONTRACT
from finance_query.report_layout_routes import (
    LAYOUT_ROUTE_HINT_SOURCE_CONTRACT,
    REPORT_LAYOUT_ROUTE_HINT_PROTOCOL,
    REPORT_LAYOUT_ROUTE_HINT_VERSION,
    build_report_layout_navigation_hints,
    validate_report_layout_navigation_hints,
)
from finance_query.table_structure import sha256_file


def section(section_id, document_id, ordinal, heading):
    return {
        "layout_section_id": section_id,
        "document_id": document_id,
        "section_ordinal": ordinal,
        "source_layout_label": heading,
        "internal_table_uids": [f"table-{section_id}"],
        "source_contract": LAYOUT_GRAPH_SOURCE_CONTRACT,
    }


class ReportLayoutRouteHintTests(unittest.TestCase):
    def test_requires_known_document_and_exposes_only_literal_heading_candidates(self):
        rows = build_report_layout_navigation_hints(
            [{"id": 1, "question": "Lãi tiền gửi của công ty", "effective_metric": "Lãi tiền gửi"}],
            [{"question_id": 1, "route_hint_status": "exact_scope_source_document_found", "primary_document_ids": ["D1"]}],
            [section("a", "D1", 1, "9. Lãi tiền gửi và cho vay"), section("b", "D1", 2, "10. Tiền mặt")],
        )
        self.assertEqual(rows[0]["layout_route_hint_status"], "layout_navigation_candidates_found")
        self.assertEqual(rows[0]["candidate_sections"][0]["layout_section_id"], "a")
        self.assertEqual(rows[0]["candidate_sections"][0]["literal_heading_overlap_tokens"], ["gui", "lai", "tien"])
        self.assertFalse(rows[0]["layout_section_selection_allowed"])
        self.assertFalse(rows[0]["source_contract"]["may_select_value_cell"])

    def test_context_blocked_question_cannot_use_layout(self):
        rows = build_report_layout_navigation_hints(
            [{"id": 1, "question": "Lãi tiền gửi"}],
            [{"question_id": 1, "route_hint_status": "question_context_incomplete_or_ambiguous", "primary_document_ids": []}],
            [section("a", "D1", 1, "9. Lãi tiền gửi")],
        )
        self.assertEqual(rows[0]["layout_route_hint_status"], "question_context_incomplete_or_ambiguous")
        self.assertEqual(rows[0]["candidate_sections"], [])

    def test_manifest_rejects_tampered_hints(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            hints = output / "report_layout_navigation_hints_v1.jsonl"
            hints.write_text(
                json.dumps({"question_id": 1, "layout_section_selection_allowed": False, "source_contract": LAYOUT_ROUTE_HINT_SOURCE_CONTRACT}) + "\n",
                encoding="utf-8",
            )
            (output / "report_layout_navigation_hints_v1.manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": REPORT_LAYOUT_ROUTE_HINT_VERSION,
                        "protocol": REPORT_LAYOUT_ROUTE_HINT_PROTOCOL,
                        "question_count": 1,
                        "hints_sha256": sha256_file(hints),
                        "source_contract": LAYOUT_ROUTE_HINT_SOURCE_CONTRACT,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(validate_report_layout_navigation_hints(output)["question_count"], 1)
            hints.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed"):
                validate_report_layout_navigation_hints(output)
