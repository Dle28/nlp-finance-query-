import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.analyze_financial_taxonomy_review_queue import analyze


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "configs" / "vietnamese_financial_taxonomy_v1.yaml"


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FinancialTaxonomySourceAuditTests(unittest.TestCase):
    def build_fixture(self, root: Path, *, mismatch: bool = False) -> dict[str, Path]:
        bundle = root / "bundle"
        bundle.mkdir()
        uid = "t1"
        document_id = "HPG_financial_statements_2022_consolidated"
        source_rows = [["Chỉ tiêu", "Mã số", "2022"], ["Tài sản ngắn hạn", "100", "120"]]
        write_json(bundle / "manifest.json", {"schema_version": 3})
        write_jsonl(bundle / "review_items.jsonl", [{"id": 1, "question": "Q"}])
        write_jsonl(bundle / "tables.jsonl", [{"internal_table_uid": uid, "document_id": document_id}])
        write_jsonl(
            bundle / "tables_structured_v2.jsonl",
            [
                {
                    "internal_table_uid": uid,
                    "document_id": document_id,
                    "header_row_indices": [0],
                    "rows": source_rows,
                    "source_provenance": {"source_sha256": "s"},
                }
            ],
        )
        write_jsonl(
            bundle / "tables_evidence_context_v3.jsonl",
            [
                {
                    "internal_table_uid": uid,
                    "document_id": document_id,
                    "quality": {"status": "review_ready"},
                    "canonical_headers": {"columns": []},
                    "context_trace": {"source_title": "Bảng cân đối kế toán"},
                }
            ],
        )
        write_jsonl(
            bundle / "table_routing_catalog_v1.jsonl",
            [
                {
                    "internal_table_uid": uid,
                    "document_id": document_id,
                    "table_type": "balance_sheet",
                    "table_type_status": "metadata_provisional",
                    "report_scope": "consolidated",
                }
            ],
        )
        write_jsonl(
            bundle / "document_metadata_v1.jsonl",
            [
                {
                    "document_id": document_id,
                    "company": "HPG",
                    "report_scope": "consolidated",
                    "report_year": 2022,
                }
            ],
        )
        write_json(
            bundle / "table_structure_v2.manifest.json",
            {
                "input_bundle_tables_sha256": sha(bundle / "tables.jsonl"),
                "sidecar_sha256": sha(bundle / "tables_structured_v2.jsonl"),
            },
        )
        write_json(
            bundle / "table_evidence_context_v3.manifest.json",
            {
                "input_structure_sha256": sha(bundle / "tables_structured_v2.jsonl"),
                "sidecar_sha256": sha(bundle / "tables_evidence_context_v3.jsonl"),
            },
        )
        write_json(
            bundle / "table_routing_catalog_v1.manifest.json",
            {
                "input_structure_sha256": sha(bundle / "tables_structured_v2.jsonl"),
                "input_evidence_context_sha256": sha(bundle / "tables_evidence_context_v3.jsonl"),
                "table_catalog_sha256": sha(bundle / "table_routing_catalog_v1.jsonl"),
            },
        )
        sum_files = [
            "manifest.json",
            "review_items.jsonl",
            "tables.jsonl",
            "tables_structured_v2.jsonl",
            "tables_evidence_context_v3.jsonl",
        ]
        (bundle / "SHA256SUMS").write_text(
            "".join(f"{sha(bundle / name)}  {name}\n" for name in sum_files),
            encoding="utf-8",
        )

        rows = root / "rows.jsonl"
        roles = root / "roles.jsonl"
        sectors = root / "sectors.jsonl"
        queue = root / "queue.jsonl"
        row_candidate = {
            "record_kind": "row_semantic_candidate",
            "internal_table_uid": uid,
            "document_id": document_id,
            "row_index": 1,
            "raw_source_row": ["WRONG"] if mismatch else source_rows[1],
            "match_status": "exact_unique",
            "concept_candidates": [
                {"concept_id": "current_assets", "period_type": "instant"}
            ],
            "source_account_codes": ["100"],
            "navigation_gate_status": "blocked",
            "navigation_reason_codes": ["table_not_routing_eligible"],
            "table_type": "balance_sheet",
            "table_type_status": "metadata_provisional",
            "sector_candidate": "industrial",
        }
        role_candidate = {
            "record_kind": "table_role_candidate",
            "internal_table_uid": uid,
            "document_id": document_id,
            "status": "semantic_candidate",
            "existing_table_type": "other",
            "proposed_table_type": "balance_sheet",
            "context_path": ["Bảng cân đối kế toán"],
        }
        sector_candidate = {
            "record_kind": "document_sector_candidate",
            "document_id": document_id,
            "status": "candidate",
            "sector": "industrial",
            "matched_signals": {"industrial": ["inventory", "cost_of_goods_sold"]},
            "scores": {"industrial": 2},
        }
        write_jsonl(rows, [row_candidate])
        write_jsonl(roles, [role_candidate])
        write_jsonl(sectors, [sector_candidate])
        write_jsonl(
            queue,
            [
                {
                    "review_type": review_type,
                    "candidate": candidate,
                    "review_decision": None,
                    "reviewer_notes": None,
                }
                for review_type, candidate in (
                    ("row_concept", row_candidate),
                    ("table_role", role_candidate),
                    ("document_sector", sector_candidate),
                )
            ],
        )
        write_json(
            rows.with_suffix(".manifest.json"),
            {
                "inputs": {"taxonomy": {"sha256": sha(TAXONOMY)}},
                "outputs": {
                    "row_candidates": {"sha256": sha(rows)},
                    "table_role_candidates": {"sha256": sha(roles)},
                    "sector_candidates": {"sha256": sha(sectors)},
                },
            },
        )
        write_json(
            queue.with_suffix(".manifest.json"),
            {
                "inputs": {
                    "row_candidates_sha256": sha(rows),
                    "table_roles_sha256": sha(roles),
                    "sectors_sha256": sha(sectors),
                },
                "output_sha256": sha(queue),
            },
        )
        return {
            "bundle": bundle,
            "rows": rows,
            "roles": roles,
            "sectors": sectors,
            "queue": queue,
            "summary": root / "financial_taxonomy_audit_summary_v1.json",
        }

    def test_audit_verifies_source_and_keeps_decisions_blank(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.build_fixture(Path(temp))
            summary, manifest = analyze(
                bundle=paths["bundle"],
                taxonomy_path=TAXONOMY,
                row_candidates_path=paths["rows"],
                table_roles_path=paths["roles"],
                sectors_path=paths["sectors"],
                review_queue_path=paths["queue"],
                output_summary=paths["summary"],
                max_examples=3,
            )
            self.assertEqual(summary["snapshot_verification"]["status"], "verified")
            self.assertEqual(
                summary["candidate_integrity"]["raw_source_row_mismatch_count"], 0
            )
            self.assertEqual(
                summary["candidate_integrity"]["review_queue_non_blank_decision_count"], 0
            )
            self.assertFalse(summary["promotion_allowed"])
            self.assertFalse(manifest["promotion_allowed"])
            examples = load_jsonl(paths["summary"].with_name("financial_taxonomy_audit_examples_v1.jsonl"))
            self.assertTrue(all(row["review_decision"] is None for row in examples))

    def test_audit_rejects_raw_source_row_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.build_fixture(Path(temp), mismatch=True)
            with self.assertRaisesRegex(ValueError, "raw source mismatch"):
                analyze(
                    bundle=paths["bundle"],
                    taxonomy_path=TAXONOMY,
                    row_candidates_path=paths["rows"],
                    table_roles_path=paths["roles"],
                    sectors_path=paths["sectors"],
                    review_queue_path=paths["queue"],
                    output_summary=paths["summary"],
                )


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
