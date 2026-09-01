from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.exact_row_pre_unseen import validate_exact_row_pre_unseen


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exact_row_receipt_passes_then_fails_on_drift(tmp_path: Path) -> None:
    hypothesis = tmp_path / "h.json"
    development = tmp_path / "d.json"
    hh = _write(hypothesis, {"decision_thresholds": {
        "minimum_newly_correct_development": 5, "minimum_precision_on_predictions": 1.0,
        "maximum_false_confident_candidate": 0, "maximum_regressed_baseline_predictions": 0,
    }})
    dh = _write(development, {
        "arms": {"B_A_plus_normalized_unique_margin_linker": {
            "precision_on_predictions": 1.0, "false_confident_count": 0,
        }}, "candidate_outcome_counts": {"IMPROVED": 8},
    })
    receipt = tmp_path / "receipt.json"
    _write(receipt, {
        "protocol": "exact_row_metric_linking_pre_unseen_freeze_v1", "schema_version": 1,
        "inputs": {"hypothesis_freeze": {"path": str(hypothesis), "sha256": hh}, "development_report": {"path": str(development), "sha256": dh}},
        "development_gate": {"status": "PASS", "observed": {"newly_correct": 8, "precision_on_predictions": 1.0, "false_confident_candidate": 0, "regressed_baseline_predictions": 0}},
        "frozen_untouched_predictions": {"minimum_newly_correct_untouched": 5, "minimum_precision_on_predictions": 1.0, "maximum_false_confident_candidate": 0, "maximum_regressed_baseline_predictions": 0, "metric_holdout_prediction": {}, "composition_holdout_prediction": {}},
        "untouched_evaluation_allowed": True, "full_vifinqa_dataset_allowed": False,
        "source_contract": {"research_only": True, "submission_eligible": False, "vifinqa_answer_authority": False},
    })
    assert validate_exact_row_pre_unseen(receipt, tmp_path)["status"] == "PASS"
    development.write_text("{}\n", encoding="utf-8")
    assert validate_exact_row_pre_unseen(receipt, tmp_path)["status"] == "FAIL"
