from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.temporal_header_pre_unseen import validate_temporal_pre_unseen


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(tmp_path: Path) -> Path:
    hypothesis = tmp_path / "hypothesis.json"
    development = tmp_path / "development.json"
    h_hash = _write(hypothesis, {"decision_thresholds": {
        "minimum_newly_correct_development": 5,
        "minimum_accuracy_on_candidate_predictions": 1.0,
        "maximum_false_confident_candidate": 0,
        "maximum_regressed_question_only_predictions": 0,
    }})
    d_hash = _write(development, {
        "arms": {"B_A_plus_source_header_backoff": {
            "accuracy_on_predictions": 1.0, "false_confident_count": 0,
        }},
        "candidate_outcome_counts": {"IMPROVED": 8},
    })
    receipt = tmp_path / "receipt.json"
    _write(receipt, {
        "protocol": "temporal_header_semantics_pre_unseen_freeze_v1",
        "schema_version": 1,
        "inputs": {
            "hypothesis_freeze": {"path": str(hypothesis), "sha256": h_hash},
            "development_report": {"path": str(development), "sha256": d_hash},
        },
        "development_gate": {"status": "PASS", "observed": {
            "newly_correct": 8, "accuracy_on_candidate_predictions": 1.0,
            "false_confident_candidate": 0, "regressed_question_only_predictions": 0,
        }},
        "frozen_untouched_predictions": {
            "minimum_newly_correct_untouched": 5,
            "minimum_accuracy_on_candidate_predictions": 1.0,
            "maximum_false_confident_candidate": 0,
            "maximum_regressed_question_only_predictions": 0,
            "metric_holdout_prediction": {"family": "assets", "minimum_improved": 5},
            "composition_holdout_prediction": {"signature": "instant|divide", "minimum_improved": 5},
        },
        "untouched_evaluation_allowed": True,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": {
            "research_only": True, "submission_eligible": False,
            "vifinqa_answer_authority": False,
        },
    })
    return receipt


def test_temporal_receipt_passes_and_hash_drift_fails(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    assert validate_temporal_pre_unseen(receipt, tmp_path)["status"] == "PASS"
    (tmp_path / "development.json").write_text("{}\n", encoding="utf-8")
    assert validate_temporal_pre_unseen(receipt, tmp_path)["status"] == "FAIL"
