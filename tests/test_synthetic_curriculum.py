from __future__ import annotations

import unittest

from finance_query.synthetic_curriculum import (
    SYNTHETIC_CURRICULUM_PROTOCOL,
    build_synthetic_curriculum,
    build_candidate_index,
    build_hard_negatives,
    extract_numeric_cells,
    verify_synthetic_example,
)


def asset(
    uid: str,
    *,
    ticker: str,
    year: int,
    scope: str,
    document_id: str,
    rows: list[list[str]],
) -> dict:
    return {
        "internal_table_uid": uid,
        "ticker": ticker,
        "report_year": year,
        "scope": scope,
        "document_id": document_id,
        "headers": ["Nhãn dòng", "2023 VND", "2022 VND"],
        "rows": rows,
        "header_row_indices": [],
        "context_before": "Bảng kiểm thử",
    }


ASSETS = [
    asset(
        "vjc-separate-2023-a",
        ticker="VJC",
        year=2023,
        scope="separate",
        document_id="VJC_2023_separate",
        rows=[["Doanh thu thuần", "100", "80"], ["Chi phí lãi vay", "20", "10"]],
    ),
    asset(
        "vjc-consolidated-2023-a",
        ticker="VJC",
        year=2023,
        scope="consolidated",
        document_id="VJC_2023_consolidated",
        rows=[["Doanh thu thuần", "120", "90"]],
    ),
    asset(
        "vjc-separate-2022-a",
        ticker="VJC",
        year=2022,
        scope="separate",
        document_id="VJC_2022_separate",
        rows=[["Doanh thu thuần", "70", "60"]],
    ),
    asset(
        "vjc-separate-2023-b",
        ticker="VJC",
        year=2023,
        scope="separate",
        document_id="VJC_2023_separate",
        rows=[["Tài sản ngắn hạn", "50", "40"]],
    ),
    asset(
        "abc-separate-2023-a",
        ticker="ABC",
        year=2023,
        scope="separate",
        document_id="ABC_2023_separate",
        rows=[["Doanh thu thuần", "200", "160"]],
    ),
]


class SyntheticCurriculumTests(unittest.TestCase):
    def test_numeric_cells_keep_only_replayable_values(self):
        noisy = asset(
            "noisy",
            ticker="NOI",
            year=2023,
            scope="separate",
            document_id="NOI_2023_separate",
            rows=[["Doanh thu", "1.234", "(72.193)(27.471)"]],
        )
        cells = extract_numeric_cells(noisy, max_cells=10)
        self.assertEqual([cell.parsed_value for cell in cells], ["1234"])

    def test_hard_negatives_are_diverse_and_never_positive(self):
        index = build_candidate_index(ASSETS)
        negatives = build_hard_negatives(
            ASSETS[0],
            row_label="Doanh thu thuần",
            index=index,
            limit=6,
        )
        negative_uids = {row["internal_table_uid"] for row in negatives}
        self.assertNotIn("vjc-separate-2023-a", negative_uids)
        types = {row["negative_type"] for row in negatives}
        self.assertIn("wrong_scope", types)
        self.assertIn("wrong_year", types)
        self.assertIn("same_document", types)

    def test_examples_are_execution_verified_and_group_split_by_issuer(self):
        examples, summary = build_synthetic_curriculum(
            ASSETS,
            source_tables_sha256="a" * 64,
            max_examples=100,
            max_cells_per_table=4,
            hard_negatives_per_example=5,
            min_hard_negatives=2,
        )
        self.assertGreater(len(examples), 0)
        self.assertEqual(summary["example_count"], len(examples))
        self.assertTrue(all(row["protocol"] == SYNTHETIC_CURRICULUM_PROTOCOL for row in examples))
        self.assertTrue(all(not verify_synthetic_example(row) for row in examples))
        vjc_splits = {row["split"] for row in examples if row["source_lineage"]["ticker"] == "VJC"}
        self.assertEqual(len(vjc_splits), 1)
        self.assertTrue(all(row["hard_negative_table_uids"] for row in examples))


if __name__ == "__main__":
    unittest.main()
