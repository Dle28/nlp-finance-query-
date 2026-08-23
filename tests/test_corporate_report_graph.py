import json
import tempfile
import unittest
from pathlib import Path

from finance_query.corporate_report_graph import (
    CORPORATE_REPORT_GRAPH_PROTOCOL,
    CORPORATE_REPORT_GRAPH_VERSION,
    GRAPH_SOURCE_CONTRACT,
    build_corporate_relationship_edges,
    build_report_families,
    build_report_relationship_edges,
    document_ticker_map,
    validate_corporate_report_graph,
)
from finance_query.table_structure import sha256_file


def document(document_id, year, scope):
    return {
        "document_id": document_id,
        "company": "VJC",
        "report_year": year,
        "report_scope": scope,
        "report_type": "financial_statements",
        "reporting_period_end": {"day": 31, "month": 12, "year": year},
        "metadata_status": "source_derived",
    }


class CorporateReportGraphTests(unittest.TestCase):
    def test_report_family_and_edges_preserve_scope_and_period_boundaries(self):
        documents = [
            document("VJC_2022_separate", 2022, "separate"),
            document("VJC_2022_consolidated", 2022, "consolidated"),
            document("VJC_2023_separate", 2023, "separate"),
        ]
        tables = [
            {"document_id": row["document_id"], "ticker": "VJC"}
            for row in documents
        ]
        tickers = document_ticker_map(tables)
        families = build_report_families(documents, document_tickers=tickers)
        self.assertEqual(len(families), 2)
        family_2022 = next(row for row in families if row["report_year"] == 2022)
        self.assertTrue(family_2022["has_parallel_consolidated_and_separate"])
        self.assertFalse(family_2022["source_contract"]["may_select_value_cell"])
        edges = build_report_relationship_edges(documents, document_tickers=tickers)
        self.assertEqual(
            {row["relation_type"] for row in edges},
            {"parallel_scope_candidate", "adjacent_reporting_period_candidate"},
        )
        parallel = next(row for row in edges if row["relation_type"] == "parallel_scope_candidate")
        self.assertTrue(parallel["value_substitution_forbidden"])
        self.assertFalse(parallel["source_contract"]["may_infer_scope"])

    def test_graph_rejects_metadata_company_ticker_mismatch(self):
        with self.assertRaisesRegex(ValueError, "company/ticker"):
            build_report_families(
                [document("VJC_2022", 2022, "separate") | {"company": "OTHER"}],
                document_tickers={"VJC_2022": "VJC"},
            )

    def test_corporate_edges_require_explicit_source_relation_or_investment_category(self):
        tables = [
            {
                "internal_table_uid": "investment",
                "document_id": "VJC_2022_separate",
                "ticker": "VJC",
                "report_year": 2022,
                "scope": "separate",
                "rows": [
                    ["Đầu tư góp vốn vào các công ty con", ""],
                    ["▪ Công ty Cổ phần VietjetAir Cargo", "90%"],
                    ["Tổng Công ty nhận chia cổ tức từ các công ty này", "30%"],
                ],
            },
            {
                "internal_table_uid": "party",
                "document_id": "VJC_2022_separate",
                "ticker": "VJC",
                "report_year": 2022,
                "scope": "separate",
                "rows": [
                    ["Chi phí trả hộ Vietjet Air Ireland No. I Limited, một công ty con", "10"],
                    ["Cổ tức phải trả cho các cổ đông", "20"],
                ],
            },
        ]
        segments = [
            {
                "internal_table_uid": "investment",
                "document_id": "VJC_2022_separate",
                "source_context_sha256": "context-investment",
                "table_function": {"kind": "investment_schedule"},
            },
            {
                "internal_table_uid": "party",
                "document_id": "VJC_2022_separate",
                "source_context_sha256": "context-party",
                "table_function": {"kind": "related_party_schedule"},
            },
        ]
        aliases = [
            {
                "ticker": "VIR",
                "canonical_entity": "vietjet air ireland no i limited",
            }
        ]
        edges = build_corporate_relationship_edges(tables, segments, aliases)
        self.assertEqual(len(edges), 2)
        self.assertEqual({row["relation_type"] for row in edges}, {"subsidiary"})
        ireland = next(row for row in edges if "Ireland" in row["target_source_entity"])
        self.assertEqual(ireland["target_ticker"], "VIR")
        self.assertEqual(ireland["target_resolution_status"], "unique_source_title_alias")
        self.assertEqual(ireland["source_row_index"], 0)
        self.assertNotIn("10", ireland["source_row_label"])
        self.assertFalse(ireland["source_contract"]["may_compute_answer"])

    def test_graph_manifest_rejects_tampered_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            paths = {
                "report_families": output / "report_families_v1.jsonl",
                "report_relationship_edges": output / "report_relationship_edges_v1.jsonl",
                "corporate_relationship_edges": output / "corporate_relationship_edges_v1.jsonl",
            }
            for path in paths.values():
                path.write_text('{"id":"source-only"}\n', encoding="utf-8")
            manifest = {
                "schema_version": CORPORATE_REPORT_GRAPH_VERSION,
                "protocol": CORPORATE_REPORT_GRAPH_PROTOCOL,
                "source_contract": GRAPH_SOURCE_CONTRACT,
                "report_family_count": 1,
                "report_relationship_edge_count": 1,
                "corporate_relationship_edge_count": 1,
                "outputs": {
                    key: {"file": path.name, "sha256": sha256_file(path)}
                    for key, path in paths.items()
                },
            }
            (output / "corporate_report_graph_v1.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            self.assertEqual(
                validate_corporate_report_graph(output)["protocol"],
                CORPORATE_REPORT_GRAPH_PROTOCOL,
            )
            paths["report_families"].write_text('{"id":"mutated"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate_corporate_report_graph(output)


if __name__ == "__main__":
    unittest.main()
