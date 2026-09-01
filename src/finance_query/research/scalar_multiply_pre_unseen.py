"""Fail-closed validation for the scalar-multiply pre-unseen freeze receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "dimensionless_scalar_multiply_pre_unseen_freeze_v1"
REQUIRED_PREDICTIONS = {
    "minimum_newly_eligible_untouched",
    "minimum_execution_accuracy_on_newly_eligible",
    "maximum_false_confident_newly_eligible",
    "maximum_regressed_previously_eligible",
    "metric_holdout_prediction",
    "composition_holdout_prediction",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def validate_pre_unseen_freeze(receipt_path: Path, repo_root: Path) -> dict[str, Any]:
    """Validate all referenced bytes and the already-observed development gate."""
    errors: list[str] = []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("protocol") != PROTOCOL:
        errors.append("protocol mismatch")
    if receipt.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if receipt.get("experiment_id") != "dimensionless_scalar_multiply_v1":
        errors.append("experiment_id mismatch")

    inputs = receipt.get("inputs")
    if not isinstance(inputs, Mapping) or not inputs:
        errors.append("inputs must be a non-empty mapping")
        inputs = {}
    loaded: dict[str, Any] = {}
    for name, reference in inputs.items():
        if not isinstance(reference, Mapping):
            errors.append(f"input {name} must be a mapping")
            continue
        path_value = reference.get("path")
        expected = reference.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected, str):
            errors.append(f"input {name} requires path and sha256")
            continue
        path = _resolve(repo_root, path_value)
        if not path.is_file():
            errors.append(f"input {name} does not exist: {path}")
            continue
        if _sha256(path) != expected:
            errors.append(f"input {name} hash mismatch")
            continue
        if path.suffix == ".json":
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))

    hypothesis = loaded.get("hypothesis_freeze")
    development = loaded.get("development_report")
    if not isinstance(hypothesis, Mapping):
        errors.append("validated hypothesis_freeze JSON is required")
    if not isinstance(development, Mapping):
        errors.append("validated development_report JSON is required")

    gate = receipt.get("development_gate")
    if not isinstance(gate, Mapping) or gate.get("status") != "PASS":
        errors.append("development_gate must be frozen as PASS")
    elif isinstance(hypothesis, Mapping) and isinstance(development, Mapping):
        thresholds = hypothesis.get("decision_thresholds") or {}
        candidate = (development.get("arms") or {}).get(
            "B_A_plus_named_constant_or_percent_scalar"
        ) or {}
        outcomes = development.get("candidate_outcome_counts") or {}
        observed = {
            "newly_eligible": int(outcomes.get("IMPROVED", 0)),
            "execution_accuracy_on_newly_eligible": candidate.get("accuracy_on_eligible"),
            "false_confident_newly_eligible": int(candidate.get("false_confident_count", 0)),
            "regressed_previously_eligible": int(outcomes.get("REGRESSED", 0)),
        }
        if gate.get("observed") != observed:
            errors.append("development_gate observed values do not match hashed report")
        passes = (
            observed["newly_eligible"] >= thresholds.get("minimum_newly_eligible_development", 0)
            and observed["execution_accuracy_on_newly_eligible"]
            == thresholds.get("minimum_execution_accuracy_on_newly_eligible")
            and observed["false_confident_newly_eligible"]
            <= thresholds.get("maximum_false_confident_newly_eligible", 0)
            and observed["regressed_previously_eligible"]
            <= thresholds.get("maximum_regressed_previously_eligible", 0)
        )
        if not passes:
            errors.append("hashed development result does not pass frozen thresholds")

    predictions = receipt.get("frozen_untouched_predictions")
    if not isinstance(predictions, Mapping):
        errors.append("frozen_untouched_predictions must be a mapping")
    else:
        missing = sorted(REQUIRED_PREDICTIONS - set(predictions))
        if missing:
            errors.append(f"missing untouched predictions: {missing}")

    if receipt.get("untouched_evaluation_allowed") is not True:
        errors.append("untouched_evaluation_allowed must be true")
    if receipt.get("full_vifinqa_dataset_allowed") is not False:
        errors.append("full_vifinqa_dataset_allowed must remain false")
    source_contract = receipt.get("source_contract") or {}
    if not (
        source_contract.get("research_only") is True
        and source_contract.get("submission_eligible") is False
        and source_contract.get("vifinqa_answer_authority") is False
    ):
        errors.append("source contract must remain research-only and non-authorizing")

    return {
        "protocol": "dimensionless_scalar_multiply_pre_unseen_validation_v1",
        "status": "PASS" if not errors else "FAIL",
        "error_count": len(errors),
        "errors": errors,
        "receipt_sha256": _sha256(receipt_path),
        "untouched_evaluation_allowed": not errors,
        "full_vifinqa_dataset_allowed": False,
    }
