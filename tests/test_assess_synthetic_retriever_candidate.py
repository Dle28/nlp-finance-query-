from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "assess_synthetic_retriever_candidate",
    Path(__file__).parents[1] / "scripts" / "assess_synthetic_retriever_candidate_v1.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def metrics(recall: float, *, wrong_year: float = 0.0, wrong_scope: float = 0.0) -> dict[str, object]:
    return {
        "recall_at_k": {"10": recall},
        "top1_error_rates": {"wrong_year": wrong_year, "wrong_scope": wrong_scope},
    }


class AssessSyntheticRetrieverCandidateTests(unittest.TestCase):
    def test_candidate_must_win_recall_and_preserve_safety_on_both_splits(self):
        models = {
            "base": {"splits": {"validation": metrics(0.2, wrong_year=0.1), "test": metrics(0.1, wrong_scope=0.2)}},
            "candidate": {"splits": {"validation": metrics(0.3, wrong_year=0.1), "test": metrics(0.2, wrong_scope=0.1)}},
        }
        result = mod.assess_candidate(
            models, base_label="base", candidate_label="candidate", required_k=10
        )
        self.assertEqual(
            result["candidate_status"], "eligible_for_followup_experiment_not_promoted"
        )
        self.assertEqual(result["promotion_status"], "not_promoted")

    def test_recall_regression_or_scope_regression_rejects_candidate(self):
        models = {
            "base": {"splits": {"validation": metrics(0.2), "test": metrics(0.3, wrong_scope=0.1)}},
            "candidate": {"splits": {"validation": metrics(0.2), "test": metrics(0.4, wrong_scope=0.2)}},
        }
        result = mod.assess_candidate(
            models, base_label="base", candidate_label="candidate", required_k=10
        )
        self.assertEqual(
            result["candidate_status"], "rejected_by_issuer_heldout_gate_not_promoted"
        )
        self.assertFalse(result["reasons"]["recall_improved_on_all_held_out_splits"])
        self.assertFalse(
            result["reasons"]["wrong_year_and_scope_not_worse_on_all_held_out_splits"]
        )

    def test_manifest_requires_non_promoted_held_out_protocol(self):
        with self.assertRaisesRegex(ValueError, "held-out"):
            mod.validate_evaluation_manifest(
                {
                    "protocol": mod.EVALUATION_PROTOCOL,
                    "promotion_status": "offline_evaluation_complete_not_promoted",
                    "source_contract": {
                        "benchmark_questions_read": False,
                        "issuer_held_out_splits_only": False,
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
