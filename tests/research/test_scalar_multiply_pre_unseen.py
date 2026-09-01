from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.scalar_multiply_pre_unseen import validate_pre_unseen_freeze


def _write_json(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(tmp_path: Path) -> Path:
    hypothesis = tmp_path / "hypothesis.json"
    development = tmp_path / "development.json"
    hypothesis_hash = _write_json(hypothesis, {
        "decision_thresholds": {
            "minimum_newly_eligible_development": 5,
            "minimum_execution_accuracy_on_newly_eligible": 1.0,
            "maximum_false_confident_newly_eligible": 0,
            "maximum_regressed_previously_eligible": 0,
        }
    })
    development_hash = _write_json(development, {
        "arms": {"B_A_plus_named_constant_or_percent_scalar": {
            "accuracy_on_eligible": 1.0,
            "false_confident_count": 0,
        }},
        "candidate_outcome_counts": {"IMPROVED": 8},
    })
    receipt = tmp_path / "receipt.json"
    _write_json(receipt, {
        "protocol": "dimensionless_scalar_multiply_pre_unseen_freeze_v1",
        "schema_version": 1,
        "experiment_id": "dimensionless_scalar_multiply_v1",
        "inputs": {
            "hypothesis_freeze": {"path": str(hypothesis), "sha256": hypothesis_hash},
            "development_report": {"path": str(development), "sha256": development_hash},
        },
        "development_gate": {"status": "PASS", "observed": {
            "newly_eligible": 8,
            "execution_accuracy_on_newly_eligible": 1.0,
            "false_confident_newly_eligible": 0,
            "regressed_previously_eligible": 0,
        }},
        "frozen_untouched_predictions": {
            "minimum_newly_eligible_untouched": 5,
            "minimum_execution_accuracy_on_newly_eligible": 1.0,
            "maximum_false_confident_newly_eligible": 0,
            "maximum_regressed_previously_eligible": 0,
            "metric_holdout_prediction": {"family": "return_and_future_value", "minimum_improved": 5},
            "composition_holdout_prediction": {"signature": "multiply", "minimum_improved": 5},
        },
        "untouched_evaluation_allowed": True,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": {
            "research_only": True,
            "submission_eligible": False,
            "vifinqa_answer_authority": False,
        },
    })
    return receipt


def test_valid_receipt_passes(tmp_path: Path) -> None:
    result = validate_pre_unseen_freeze(_receipt(tmp_path), tmp_path)
    assert result["status"] == "PASS"
    assert result["untouched_evaluation_allowed"] is True


def test_hash_drift_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    (tmp_path / "development.json").write_text("{}\n", encoding="utf-8")
    result = validate_pre_unseen_freeze(receipt, tmp_path)
    assert result["status"] == "FAIL"
    assert result["untouched_evaluation_allowed"] is False
