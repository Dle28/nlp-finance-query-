import json
import tempfile
import unittest
from pathlib import Path

from finance_query.corporate_report_context_layout import (
    CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT,
    CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_PROTOCOL,
    CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_VERSION,
    build_corporate_report_context_layout_supplement,
    validate_corporate_report_context_layout_supplement,
)
from finance_query.report_layout_graph import LAYOUT_GRAPH_SOURCE_CONTRACT
from finance_query.table_structure import sha256_file


def packet():
    return {
        "question_id": 7,
        "immutable_review_context_sha256": "immutable-7",
        "review_context": {
            "question": "Lãi tiền gửi năm 2022 là bao nhiêu?",
            "retrieval_metadata_consensus": {
                "proposed_question_context": {"source_document_id": "VJC_2022_separate"}
            },
        },
    }


def section(section_id, heading):
    return {
        "layout_section_id": section_id,
        "document_id": "VJC_2022_separate",
        "section_ordinal": 1,
        "source_layout_label": heading,
        "source_contract": LAYOUT_GRAPH_SOURCE_CONTRACT,
    }


class CorporateReportContextLayoutSupplementTests(unittest.TestCase):
    def test_uses_exact_queue_document_and_exposes_heading_only(self):
        rows = build_corporate_report_context_layout_supplement(
            [packet()], [section("s1", "9. Lãi tiền gửi và cho vay")]
        )
        self.assertEqual(rows[0]["layout_supplement_status"], "literal_source_heading_candidates_found")
        self.assertEqual(rows[0]["source_heading_candidates"][0]["layout_section_id"], "s1")
        self.assertNotIn("internal_table_uids", rows[0]["source_heading_candidates"][0])
        self.assertFalse(rows[0]["materialization_allowed"])

    def test_manifest_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            supplement = output / "corporate_report_context_layout_supplement_v1.jsonl"
            supplement.write_text(
                json.dumps({"question_id": 7, "materialization_allowed": False, "source_contract": CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT}) + "\n",
                encoding="utf-8",
            )
            (output / "corporate_report_context_layout_supplement_v1.manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_VERSION,
                        "protocol": CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_PROTOCOL,
                        "question_count": 1,
                        "materialization_allowed": False,
                        "supplement_sha256": sha256_file(supplement),
                        "source_contract": CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(validate_corporate_report_context_layout_supplement(output)["question_count"], 1)
            supplement.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed"):
                validate_corporate_report_context_layout_supplement(output)
