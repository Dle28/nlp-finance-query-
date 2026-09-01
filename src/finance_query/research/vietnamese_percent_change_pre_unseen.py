"""Fail-closed validation for Vietnamese percent-change pre-unseen receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vietnamese_percent_change_semantics_pre_unseen_freeze_v1"
REQUIRED_PREDICTIONS = {
    "minimum_newly_correct_untouched",
    "minimum_precision_on_predictions",
    "maximum_false_confident_candidate",
    "maximum_regressed_baseline_predictions",
    "metric_holdout_prediction",
    "composition_holdout_prediction",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def validate_pre_unseen(receipt_path: Path, repo_root: Path) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if receipt.get("protocol") != PROTOCOL or receipt.get("schema_version") != 1:
        errors.append("receipt protocol mismatch")
    if receipt.get("experiment_id") != "vietnamese_percent_change_semantics_v1":
        errors.append("experiment ID mismatch")
    inputs = receipt.get("inputs")
    if not isinstance(inputs, Mapping) or not inputs:
        errors.append("receipt inputs missing")
        inputs = {}
    loaded: dict[str, Any] = {}
    for name, descriptor in inputs.items():
        if not isinstance(descriptor, Mapping):
            errors.append(f"invalid input descriptor: {name}")
            continue
        path = _resolve(repo_root, str(descriptor.get("path") or ""))
        if not path.is_file() or _sha(path) != descriptor.get("sha256"):
            errors.append(f"input hash mismatch: {name}")
            continue
        if path.suffix == ".json":
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
    hypothesis = loaded.get("hypothesis_freeze") or {}
    development = loaded.get("development_report") or {}
    thresholds = hypothesis.get("decision_thresholds") or {}
    candidate = (development.get("arms") or {}).get(
        "B_A_plus_narrow_relative_percent_change_contract"
    ) or {}
    outcomes = development.get("candidate_outcome_counts") or {}
    observed = {
        "newly_correct": int(outcomes.get("IMPROVED", 0)),
        "precision_on_predictions": candidate.get("precision_on_predictions"),
        "false_confident_candidate": int(candidate.get("false_confident_count", 0)),
        "regressed_baseline_predictions": int(outcomes.get("REGRESSED", 0)),
    }
    gate = receipt.get("development_gate") or {}
    if gate.get("status") != "PASS" or gate.get("observed") != observed:
        errors.append("development gate mismatch")
    if not (
        observed["newly_correct"] >= thresholds.get("minimum_newly_correct_development", 0)
        and observed["precision_on_predictions"] == thresholds.get("minimum_precision_on_predictions")
        and observed["false_confident_candidate"] <= thresholds.get("maximum_false_confident_candidate", 0)
        and observed["regressed_baseline_predictions"] <= thresholds.get("maximum_regressed_baseline_predictions", 0)
    ):
        errors.append("development thresholds failed")
    predictions = receipt.get("frozen_untouched_predictions")
    if not isinstance(predictions, Mapping) or not REQUIRED_PREDICTIONS.issubset(predictions):
        errors.append("untouched predictions incomplete")
    if receipt.get("untouched_evaluation_allowed") is not True:
        errors.append("untouched evaluation is not allowed")
    if receipt.get("full_vifinqa_dataset_allowed") is not False:
        errors.append("full ViFinQA must remain locked")
    contract = receipt.get("source_contract") or {}
    if not (
        contract.get("research_only") is True
        and contract.get("submission_eligible") is False
        and contract.get("vifinqa_answer_authority") is False
    ):
        errors.append("source contract is unsafe")
    return {
        "protocol": "vietnamese_percent_change_semantics_pre_unseen_validation_v1",
        "status": "PASS" if not errors else "FAIL",
        "error_count": len(errors),
        "errors": errors,
        "receipt_sha256": _sha(receipt_path),
        "untouched_evaluation_allowed": not errors,
        "full_vifinqa_dataset_allowed": False,
    }
