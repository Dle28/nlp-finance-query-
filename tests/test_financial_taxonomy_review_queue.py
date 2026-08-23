import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_financial_taxonomy_review_queue import build_review_queue


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class FinancialTaxonomyReviewQueueTests(unittest.TestCase):
    def test_queue_is_stratified_hash_bound_and_non_promotable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row_candidates = root / "rows.jsonl"
            table_roles = root / "roles.jsonl"
            sectors = root / "sectors.jsonl"
            output = root / "queue.jsonl"
            write_jsonl(
                row_candidates,
                [
                    {
                        "record_kind": "row_semantic_candidate",
                        "document_id": "d1",
                        "internal_table_uid": "t1",
                        "row_index": index,
                        "match_status": "exact_unique",
                        "navigation_gate_status": "blocked",
                        "concept_candidates": [{"concept_id": "inventory"}],
                    }
                    for index in range(3)
                ],
            )
            write_jsonl(
                table_roles,
                [
                    {
                        "record_kind": "table_role_candidate",
                        "document_id": "d1",
                        "internal_table_uid": "t1",
                        "status": "semantic_candidate",
                        "existing_table_type": "other",
                        "proposed_table_type": "balance_sheet",
                    }
                ],
            )
            write_jsonl(
                sectors,
                [
                    {
                        "record_kind": "document_sector_candidate",
                        "document_id": "d1",
                        "status": "candidate",
                        "sector": "industrial",
                    }
                ],
            )
            manifest = build_review_queue(
                row_candidates=row_candidates,
                table_roles=table_roles,
                sectors=sectors,
                output=output,
                max_rows_per_stratum=2,
                max_sectors_per_sector=1,
            )
            queue = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(
                manifest["review_type_counts"],
                {"row_concept": 2, "table_role": 1, "document_sector": 1},
            )
            self.assertEqual(len(queue), 4)
            self.assertTrue(all(not row["source_contract"]["promotion_allowed"] for row in queue))
            self.assertTrue(all(row["review_decision"] is None for row in queue))


if __name__ == "__main__":
    unittest.main()
