from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from finance_query.direct_replay import replay_direct_review
from scripts.build_direct_evidence_replay import build_replay


def table(uid: str, value: str) -> dict:
    return {
        "internal_table_uid": uid,
        "ticker": "AAA",
        "report_year": 2023,
        "scope": "consolidated",
        "unit_hint": "million_vnd",
        "rows": [["Metric", value]],
        "cell_provenance": [[{"r": 0, "c": 0}, {"r": 0, "c": 1}]],
    }


def context(uid: str) -> dict:
    return {
        "internal_table_uid": uid,
        "canonical_headers": {"columns": [{"column_index": 1, "source_label": "2023"}]},
        "row_profiles": [{"row_index": 0, "role": "data", "numeric_columns": [1]}],
    }


def assessment(uid: str, value: str) -> dict:
    return {
        "uid": uid,
        "source_ready": True,
        "exact_row": True,
        "row_bound": True,
        "metadata_score": 1.0,
        "raw_metric_identity": {"exact": True},
        "grounding": {"guard_pass": True},
        "value_binding": {
            "status": "cell_bound",
            "row_index": 0,
            "column_index": 1,
            "column_label": "2023",
            "value": value,
            "source_cell": {"r": 0, "c": 1},
        },
    }


def review(*assessments: dict) -> dict:
    return {
        "id": 1,
        "family": "direct_lookup",
        "consensus_status": "machine_provisional",
        "machine_candidate_uid": assessments[0]["uid"],
        "effective_question_plan": {
            "family": "direct_lookup",
            "tickers": ["AAA"],
            "years": [2023],
            "scope": "consolidated",
            "requested_unit": "million_vnd",
        },
        "machine_self_review": {"candidate_assessments": list(assessments)},
    }


class DirectReplayTests(unittest.TestCase):
    def test_unique_exact_value_is_shadow_ready_only(self) -> None:
        result = replay_direct_review(
            review(assessment("a", "10")),
            {"a": table("a", "10")},
            {"a": context("a")},
        )
        self.assertEqual(result["status"], "shadow_replay_ready")
        self.assertEqual(result["replay_value"], "10")
        self.assertFalse(result["review_status_promotion_allowed"])

    def test_equivalent_duplicate_sources_are_not_a_conflict(self) -> None:
        result = replay_direct_review(
            review(assessment("a", "10"), assessment("b", "10")),
            {"a": table("a", "10"), "b": table("b", "10")},
            {"a": context("a"), "b": context("b")},
        )
        self.assertEqual(result["status"], "shadow_replay_ready")
        self.assertEqual(result["valid_exact_candidate_count"], 2)
        self.assertEqual(result["distinct_exact_value_count"], 1)

    def test_conflicting_exact_sources_are_ambiguous(self) -> None:
        result = replay_direct_review(
            review(assessment("a", "10"), assessment("b", "11")),
            {"a": table("a", "10"), "b": table("b", "11")},
            {"a": context("a"), "b": context("b")},
        )
        self.assertEqual(result["status"], "shadow_ambiguous")
        self.assertIn("conflicting_exact_source_values", result["reason_codes"])

    def test_tampered_raw_cell_fails_closed(self) -> None:
        result = replay_direct_review(
            review(assessment("a", "10")),
            {"a": table("a", "99")},
            {"a": context("a")},
        )
        self.assertEqual(result["status"], "shadow_blocked")
        self.assertEqual(result["rejection_counts"]["stored_value_differs_from_v2"], 1)

    def test_builder_rejects_review_from_another_bundle(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "review_items.jsonl").write_text(
                '{"id": 1, "question": "Current question"}\n', encoding="utf-8"
            )
            (bundle / "tables.jsonl").write_text("{}\n", encoding="utf-8")
            (bundle / "tables_structured_v2.jsonl").write_text("{}\n", encoding="utf-8")
            context_path = bundle / "tables_evidence_context_v3.jsonl"
            context_path.write_text("{}\n", encoding="utf-8")
            reviews_path = root / "reviews.jsonl"
            reviews_path.write_text(
                '{"id": 1, "question": "Stale question", "family": "direct_lookup"}\n',
                encoding="utf-8",
            )
            with (
                patch("scripts.build_direct_evidence_replay.load_v2_tables", return_value={}),
                patch("scripts.build_direct_evidence_replay.load_evidence_contexts", return_value={}),
            ):
                with self.assertRaisesRegex(ValueError, "differs from bundle"):
                    build_replay(bundle, reviews_path, context_path, root / "output.jsonl")


if __name__ == "__main__":
    unittest.main()
