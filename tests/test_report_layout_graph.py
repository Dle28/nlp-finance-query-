import json
import tempfile
import unittest
from pathlib import Path

from finance_query.report_layout_graph import (
    LAYOUT_GRAPH_SOURCE_CONTRACT,
    REPORT_LAYOUT_GRAPH_PROTOCOL,
    REPORT_LAYOUT_GRAPH_VERSION,
    build_report_layout_graph,
    validate_report_layout_graph,
)
from finance_query.table_structure import sha256_file


def segment(uid, document, ordinal, heading, *, ticker="VJC", year=2022, scope="separate"):
    return {
        "internal_table_uid": uid,
        "document_id": document,
        "local_ordinal": ordinal,
        "ticker": ticker,
        "report_year": year,
        "scope": scope,
        "source_heading": heading,
        "source_parent_heading": "",
        "source_heading_kind": "numbered_heading",
        "source_context_sha256": f"context-{uid}",
        "table_function": {"kind": "financial_data_schedule"},
        "table_section": {"kind": "asset"},
        "evidence_eligible": False,
        "training_eligible": False,
    }


class ReportLayoutGraphTests(unittest.TestCase):
    def test_groups_contiguous_layout_and_only_connects_literal_matching_sections(self):
        segments = [
            segment("s1", "VJC_2022_separate", 1, "9. Các khoản đầu tư"),
            segment("s2", "VJC_2022_separate", 2, "9. Các khoản đầu tư"),
            segment("s3", "VJC_2022_separate", 3, "10. Tiền và tương đương tiền"),
            segment("c1", "VJC_2022_consolidated", 1, "9. Các khoản đầu tư", scope="consolidated"),
            segment("c2", "VJC_2022_consolidated", 2, "11. Tiền gửi", scope="consolidated"),
        ]
        edges = [
            {
                "relation_type": "parallel_scope_candidate",
                "source_document_id": "VJC_2022_separate",
                "target_document_id": "VJC_2022_consolidated",
                "issuer_ticker": "VJC",
                "source_report_year": 2022,
                "target_report_year": 2022,
                "source_scope": "separate",
                "target_scope": "consolidated",
                "period_alignment_status": "same_report_year_distinct_scope",
            }
        ]
        profiles, sections, sequence_edges, cross_edges = build_report_layout_graph(segments, edges)
        self.assertEqual(len(profiles), 2)
        self.assertEqual(len(sections), 4)
        separate_sections = [row for row in sections if row["document_id"] == "VJC_2022_separate"]
        self.assertEqual(separate_sections[0]["internal_table_uids"], ["s1", "s2"])
        self.assertEqual(len(sequence_edges), 2)
        self.assertEqual(len(cross_edges), 1)
        self.assertEqual(cross_edges[0]["relation_type"], "parallel_scope_section_candidate")
        self.assertTrue(cross_edges[0]["value_substitution_forbidden"])
        self.assertFalse(cross_edges[0]["source_contract"]["may_select_value_cell"])

    def test_heading_absence_is_explicit_and_never_cross_report_matched(self):
        segments = [
            segment("s1", "A", 1, ""),
            segment("s2", "B", 1, ""),
        ]
        _, sections, _, cross_edges = build_report_layout_graph(
            segments,
            [{"relation_type": "parallel_scope_candidate", "source_document_id": "A", "target_document_id": "B"}],
        )
        self.assertEqual([row["source_heading_status"] for row in sections], ["source_heading_absent"] * 2)
        self.assertEqual(cross_edges, [])

    def test_manifest_rejects_tampered_section_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            files = {
                "profiles": "report_layout_profiles_v1.jsonl",
                "sections": "report_layout_sections_v1.jsonl",
                "sequence_edges": "report_layout_sequence_edges_v1.jsonl",
                "cross_report_edges": "report_layout_cross_report_edges_v1.jsonl",
            }
            outputs = {}
            for key, filename in files.items():
                path = output / filename
                path.write_text(json.dumps({"source_contract": LAYOUT_GRAPH_SOURCE_CONTRACT}) + "\n", encoding="utf-8")
                outputs[key] = {"file": filename, "sha256": sha256_file(path)}
            (output / "report_layout_graph_v1.manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": REPORT_LAYOUT_GRAPH_VERSION,
                        "protocol": REPORT_LAYOUT_GRAPH_PROTOCOL,
                        "outputs": outputs,
                        "source_contract": LAYOUT_GRAPH_SOURCE_CONTRACT,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(validate_report_layout_graph(output)["protocol"], REPORT_LAYOUT_GRAPH_PROTOCOL)
            (output / files["sections"]).write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                validate_report_layout_graph(output)
