import json
from pathlib import Path
import tempfile
import unittest

from finance_query.financial_taxonomy import FinancialTaxonomy
from scripts.build_financial_taxonomy_candidates import build_candidates


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "configs" / "vietnamese_financial_taxonomy_v1.yaml"


class FinancialTaxonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taxonomy = FinancialTaxonomy.load(TAXONOMY)

    def test_exact_concept_requires_source_account_code_when_declared(self):
        accepted = self.taxonomy.classify_row(
            ["Tổng tài sản ngắn hạn", "100", "20"],
            source_label="Tổng tài sản ngắn hạn",
            table_type="balance_sheet",
            sector="industrial",
        )
        blocked = self.taxonomy.classify_row(
            ["Tổng tài sản ngắn hạn", "110", "20"],
            source_label="Tổng tài sản ngắn hạn",
            table_type="balance_sheet",
            sector="industrial",
        )
        self.assertEqual(accepted["match_status"], "exact_unique")
        self.assertEqual(accepted["concept_candidates"][0]["concept_id"], "current_assets")
        self.assertEqual(blocked["match_status"], "constraint_blocked")
        self.assertEqual(blocked["rejected_constraint_counts"], {"required_account_code_missing": 1})

    def test_expanded_taxonomy_covers_literal_total_profit_before_tax(self):
        result = self.taxonomy.classify_row(
            ["Tổng lợi nhuận kế toán trước thuế", "50", "120"],
            source_label="Tổng lợi nhuận kế toán trước thuế",
            table_type="income_statement",
            sector="industrial",
        )
        self.assertEqual(result["match_status"], "exact_unique")
        self.assertEqual(result["concept_candidates"][0]["concept_id"], "profit_before_tax")
        self.assertEqual(result["concept_candidates"][0]["period_type"], "duration")

    def test_concept_candidate_retains_root_to_leaf_hierarchy(self):
        result = self.taxonomy.classify_row(
            ["Các khoản phải thu ngắn hạn", "130", "80"],
            source_label="Các khoản phải thu ngắn hạn",
            table_type="balance_sheet",
            sector="industrial",
        )
        self.assertEqual(result["match_status"], "exact_unique")
        self.assertEqual(
            result["concept_candidates"][0]["concept_path"],
            ["assets", "current_assets", "current_receivables"],
        )

    def test_measurement_and_movement_are_axes_not_forced_concepts(self):
        measurement = self.taxonomy.classify_row(
            ["Giá trị hao mòn lũy kế", "100"],
            source_label="Giá trị hao mòn lũy kế",
            table_type="notes",
            sector="unknown",
        )
        movement = self.taxonomy.classify_row(
            ["Số dư cuối năm", "100"],
            source_label="Số dư cuối năm",
            table_type="notes",
            sector="unknown",
        )
        self.assertEqual(measurement["match_status"], "axis_only")
        self.assertEqual(
            measurement["semantic_axes"]["measurement_basis"], ["accumulated_depreciation"]
        )
        self.assertEqual(movement["semantic_axes"]["movement"], ["closing_balance"])

    def test_sector_inference_needs_multiple_independent_signals(self):
        weak = self.taxonomy.infer_sector(["Cho vay khách hàng"])
        banking = self.taxonomy.infer_sector(
            ["Cho vay khách hàng", "Tiền gửi của khách hàng", "Thu nhập lãi thuần"]
        )
        self.assertEqual(weak["sector"], "unknown")
        self.assertEqual(weak["status"], "insufficient_signal")
        self.assertEqual(banking["sector"], "banking")
        self.assertEqual(banking["status"], "candidate")

    def test_materializer_is_hash_bound_and_never_promotable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            structured = root / "tables_structured_v2.jsonl"
            catalog = root / "table_routing_catalog_v1.jsonl"
            output = root / "financial_taxonomy_candidates_v1.jsonl"
            structured.write_text(
                json.dumps(
                    {
                        "internal_table_uid": "t1",
                        "document_id": "HPG_2022_consolidated",
                        "header_row_indices": [0],
                        "rows": [
                            ["Chỉ tiêu", "Mã số", "2022"],
                            ["Tổng tài sản ngắn hạn", "100", "120"],
                            ["Số dư cuối năm", "", "120"],
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            catalog.write_text(
                json.dumps(
                    {
                        "internal_table_uid": "t1",
                        "document_id": "HPG_2022_consolidated",
                        "table_type": "balance_sheet",
                        "table_type_status": "source_structural",
                        "routing_eligible": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest = build_candidates(
                structured_tables=structured,
                routing_catalog=catalog,
                taxonomy_path=TAXONOMY,
                output=output,
            )
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(manifest["taxonomy_concept_candidate_row_count"], 1)
            self.assertEqual(manifest["match_status_counts"]["axis_only"], 1)
            self.assertEqual(manifest["strict_navigation_ready_row_count"], 0)
            self.assertFalse(manifest["submission_eligible"])
            self.assertFalse(manifest["promotion_allowed"])
            self.assertTrue(all(not row["source_contract"]["evidence_eligible"] for row in rows))

    def test_table_role_is_only_a_non_promotable_semantic_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            structured = root / "tables_structured_v2.jsonl"
            catalog = root / "table_routing_catalog_v1.jsonl"
            output = root / "financial_taxonomy_candidates_v1.jsonl"
            structured.write_text(
                json.dumps(
                    {
                        "internal_table_uid": "t1",
                        "document_id": "HPG_2022_consolidated",
                        "header_row_indices": [0],
                        "rows": [
                            ["Chỉ tiêu", "Mã số", "2022"],
                            ["Tổng tài sản ngắn hạn", "100", "120"],
                            ["Tổng cộng tài sản", "270", "500"],
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            catalog.write_text(
                json.dumps(
                    {
                        "internal_table_uid": "t1",
                        "document_id": "HPG_2022_consolidated",
                        "table_type": "other",
                        "table_type_status": "metadata_provisional",
                        "routing_eligible": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest = build_candidates(
                structured_tables=structured,
                routing_catalog=catalog,
                taxonomy_path=TAXONOMY,
                output=output,
            )
            roles_path = output.with_name(output.stem + ".table_roles.jsonl")
            role = json.loads(roles_path.read_text(encoding="utf-8").strip())
            self.assertEqual(manifest["table_role_status_counts"], {"semantic_candidate": 1})
            self.assertEqual(role["proposed_table_type"], "balance_sheet")
            self.assertEqual(
                role["concept_votes"]["balance_sheet"], ["current_assets", "total_assets"]
            )
            self.assertFalse(role["source_contract"]["promotion_allowed"])
            self.assertFalse(role["source_contract"]["evidence_eligible"])


if __name__ == "__main__":
    unittest.main()
