from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "evaluate_synthetic_retriever",
    Path(__file__).parents[1] / "scripts" / "evaluate_synthetic_retriever_v1.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


ASSETS = {
    "positive": {"ticker": "AAA", "report_year": 2023, "scope": "separate", "document_id": "aaa-2023-s"},
    "wrong_scope": {"ticker": "AAA", "report_year": 2023, "scope": "consolidated", "document_id": "aaa-2023-c"},
    "wrong_year": {"ticker": "AAA", "report_year": 2022, "scope": "separate", "document_id": "aaa-2022-s"},
    "wrong_entity": {"ticker": "BBB", "report_year": 2023, "scope": "separate", "document_id": "bbb-2023-s"},
    "same_context": {"ticker": "AAA", "report_year": 2023, "scope": "separate", "document_id": "aaa-2023-s"},
}


class SyntheticRetrieverEvaluationTests(unittest.TestCase):
    def test_only_held_out_splits_are_allowed(self):
        with self.assertRaisesRegex(ValueError, "issuer-held-out"):
            mod.load_held_out_rows(Path("unused.jsonl"), splits=["train"], expected_tables_sha256="x")

    def test_ranking_summary_separates_context_errors(self):
        rows = [
            {"curriculum_id": "a", "positive_table_uids": ["positive"]},
            {"curriculum_id": "b", "positive_table_uids": ["positive"]},
            {"curriculum_id": "c", "positive_table_uids": ["positive"]},
            {"curriculum_id": "d", "positive_table_uids": ["positive"]},
        ]
        rankings = {
            "a": ["positive", "wrong_scope"],
            "b": ["wrong_scope", "positive"],
            "c": ["wrong_year", "positive"],
            "d": ["wrong_entity", "same_context"],
        }
        metrics, details = mod.summarise_rankings(rows, rankings, ASSETS, ks=[1, 2])
        self.assertEqual(metrics["recall_at_k"], {"1": 0.25, "2": 0.75})
        self.assertEqual(metrics["top1_error_counts"]["wrong_scope"], 1)
        self.assertEqual(metrics["top1_error_counts"]["wrong_year"], 1)
        self.assertEqual(metrics["top1_error_counts"]["wrong_entity"], 1)
        self.assertEqual([row["top1_error_bucket"] for row in details], ["correct", "wrong_scope", "wrong_year", "wrong_entity"])

    def test_model_parser_rejects_ambiguous_or_duplicate_labels(self):
        self.assertEqual(mod.parse_models(["base=BAAI/bge-m3", "finetuned=/model"]), {"base": "BAAI/bge-m3", "finetuned": "/model"})
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            mod.parse_models(["base=one", "base=two"])
        with self.assertRaisesRegex(ValueError, "at least"):
            mod.parse_models(["base=one"])


if __name__ == "__main__":
    unittest.main()
