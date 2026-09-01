"""Fail-closed pre-unseen receipt validation for exact-row metric linking."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "exact_row_metric_linking_pre_unseen_freeze_v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_exact_row_pre_unseen(receipt_path: Path, repo_root: Path) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if receipt.get("protocol") != PROTOCOL or receipt.get("schema_version") != 1:
        errors.append("receipt protocol mismatch")
    loaded: dict[str, Any] = {}
    inputs = receipt.get("inputs") or {}
    if not isinstance(inputs, Mapping) or not inputs:
        errors.append("receipt inputs missing")
        inputs = {}
    for name, descriptor in inputs.items():
        path = Path(str((descriptor or {}).get("path") or ""))
        if not path.is_absolute():
            path = repo_root / path
        if not path.is_file() or _sha(path) != (descriptor or {}).get("sha256"):
            errors.append(f"input hash mismatch: {name}")
            continue
        if path.suffix == ".json":
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
    hypothesis = loaded.get("hypothesis_freeze") or {}
    development = loaded.get("development_report") or {}
    thresholds = hypothesis.get("decision_thresholds") or {}
    candidate = (development.get("arms") or {}).get(
        "B_A_plus_normalized_unique_margin_linker"
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
        errors.append("development gate does not match hashed report")
    if not (
        observed["newly_correct"] >= thresholds.get("minimum_newly_correct_development", 0)
        and observed["precision_on_predictions"] == thresholds.get("minimum_precision_on_predictions")
        and observed["false_confident_candidate"] <= thresholds.get("maximum_false_confident_candidate", 0)
        and observed["regressed_baseline_predictions"] <= thresholds.get("maximum_regressed_baseline_predictions", 0)
    ):
        errors.append("hashed development report does not pass frozen thresholds")
    predictions = receipt.get("frozen_untouched_predictions") or {}
    required = {
        "minimum_newly_correct_untouched", "minimum_precision_on_predictions",
        "maximum_false_confident_candidate", "maximum_regressed_baseline_predictions",
        "metric_holdout_prediction", "composition_holdout_prediction",
    }
    if not isinstance(predictions, Mapping) or not required.issubset(predictions):
        errors.append("untouched predictions are incomplete")
    if receipt.get("untouched_evaluation_allowed") is not True:
        errors.append("untouched evaluation is not allowed")
    if receipt.get("full_vifinqa_dataset_allowed") is not False:
        errors.append("full ViFinQA dataset must remain locked")
    contract = receipt.get("source_contract") or {}
    if not (contract.get("research_only") is True and contract.get("submission_eligible") is False and contract.get("vifinqa_answer_authority") is False):
        errors.append("source contract is unsafe")
    return {
        "protocol": "exact_row_metric_linking_pre_unseen_validation_v1",
        "status": "PASS" if not errors else "FAIL",
        "error_count": len(errors),
        "errors": errors,
        "receipt_sha256": _sha(receipt_path),
        "untouched_evaluation_allowed": not errors,
        "full_vifinqa_dataset_allowed": False,
    }
