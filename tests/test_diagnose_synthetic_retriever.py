from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np


spec = importlib.util.spec_from_file_location(
    "diagnose_synthetic_retriever",
    Path(__file__).parents[1] / "scripts" / "diagnose_synthetic_retriever_v1.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


ASSETS = {
    "positive": {"ticker": "AAA", "report_year": 2023, "scope": "separate", "document_id": "aaa-2023-s"},
    "negative": {"ticker": "AAA", "report_year": 2022, "scope": "separate", "document_id": "aaa-2022-s"},
    "wrong_entity": {"ticker": "BBB", "report_year": 2023, "scope": "separate", "document_id": "bbb-2023-s"},
}


class SyntheticRetrieverDiagnosticTests(unittest.TestCase):
    def test_train_requires_explicit_diagnostic_opt_in(self):
        with self.assertRaisesRegex(ValueError, "diagnostic-allow-train"):
            mod.normalise_splits(["train", "validation"], diagnostic_allow_train=False)
        self.assertEqual(
            mod.normalise_splits(["train", "validation"], diagnostic_allow_train=True),
            ("train", "validation"),
        )

    def test_duplicate_and_unknown_splits_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            mod.normalise_splits(["validation", "validation"], diagnostic_allow_train=False)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            mod.normalise_splits(["benchmark"], diagnostic_allow_train=False)

    def test_pairwise_summary_reports_margin_and_hard_negative_win_rate(self):
        rows = [
            {
                "curriculum_id": "wins",
                "positive_table_uids": ["positive"],
                "hard_negative_table_uids": ["negative"],
            },
            {
                "curriculum_id": "loses",
                "positive_table_uids": ["positive"],
                "hard_negative_table_uids": ["negative"],
            },
        ]
        query_embeddings = {
            "wins": np.array([1.0, 0.0]),
            "loses": np.array([0.0, 1.0]),
        }
        passage_embeddings = np.array(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [1.0, 0.0],
            ]
        )
        metrics, details = mod.summarise_pairwise_scores(
            rows,
            query_embeddings,
            passage_embeddings,
            {"positive": 0, "negative": 1, "wrong_entity": 2},
            ASSETS,
        )
        self.assertEqual(metrics["questions"], 2)
        self.assertEqual(metrics["positive_beats_hard_negative_rate"], 0.5)
        self.assertEqual(metrics["positive_minus_hard_negative"]["mean"], 0.0)
        self.assertTrue(details["wins"]["positive_beats_hard_negative"])
        self.assertFalse(details["loses"]["positive_beats_hard_negative"])

    def test_pairwise_unknown_hard_negative_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Unknown hard-negative"):
            mod._hard_negative_uid(
                {"curriculum_id": "x", "positive_table_uids": ["positive"], "hard_negative_table_uids": ["missing"]},
                ASSETS,
            )


if __name__ == "__main__":
    unittest.main()
