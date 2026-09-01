"""Fail-closed validation for the ViFinQA generalization research protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vifinqa_generalization_experiment_protocol_v1"
STAGES = ["discovery", "development", "untouched_evaluation", "full_dataset"]
VERDICTS = ["KEEP", "REJECT", "INVESTIGATE FURTHER"]
FORBIDDEN_AUTHORITY = (
    "evidence_eligible",
    "may_materialize_answer",
    "training_eligible",
    "submission_eligible",
    "promotion_allowed",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_generalization_protocol(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(value.get("protocol") == PROTOCOL and value.get("schema_version") == 1, "invalid protocol identity")
    baseline = value.get("baseline_policy") or {}
    _require(baseline.get("must_replay_unchanged") is True, "baseline must remain unchanged")
    _require(baseline.get("byte_match_required") is True, "baseline replay must byte-match")
    taxonomy = value.get("taxonomy_policy") or {}
    _require(taxonomy.get("question_id_role") == "tracking_only", "Question ID must be tracking-only")
    _require(taxonomy.get("baseline_result_must_not_influence_taxonomy") is True, "taxonomy must be baseline-independent")
    hypothesis = value.get("hypothesis_policy") or {}
    _require(hypothesis.get("prediction_must_be_frozen_before_run") is True, "prediction must be frozen before run")
    _require(hypothesis.get("one_primary_change") is True, "one-primary-change policy missing")
    _require(len(hypothesis.get("required_fields") or []) == 6, "hypothesis contract must retain six predictive fields")
    subsets = value.get("subset_policy") or {}
    _require(subsets.get("stages_in_order") == STAGES, "subset stage order changed")
    for name, lower, upper in (("discovery_size", 5, 10), ("development_size", 20, 30), ("untouched_evaluation_size", 20, 30)):
        bounds = subsets.get(name) or {}
        _require(bounds.get("minimum") == lower and bounds.get("maximum") == upper, f"invalid {name}")
    for field in ("pairwise_disjoint_before_full_dataset", "freeze_before_untouched_evaluation", "full_dataset_requires_unseen_signal", "full_dataset_may_not_be_reused_as_development"):
        _require(subsets.get(field) is True, f"subset invariant missing: {field}")
    _require(set((value.get("holdout_policy") or {}).get("required") or []) == {"company", "wording", "metric", "composition"}, "all four holdouts are required")
    _require((value.get("ablation_policy") or {}).get("required") is True, "ablation is required")
    human = value.get("human_policy") or {}
    _require(human.get("per_question_answer_review_forbidden") is True, "per-question human review must be forbidden")
    _require(human.get("per_question_correction_forbidden") is True, "per-question human correction must be forbidden")
    _require(human.get("allowed_verdicts") == VERDICTS, "human verdict vocabulary changed")
    evaluator = value.get("evaluator_gate") or {}
    _require(evaluator.get("independent_gold_or_official_evaluator_required") is True, "independent evaluator gate missing")
    _require(evaluator.get("missing_evaluator_action") == "STOP_BEFORE_MODEL_CHANGE", "missing evaluator must stop model changes")
    contract = value.get("source_contract") or {}
    _require(contract.get("research_only") is True, "protocol must remain research-only")
    for field in FORBIDDEN_AUTHORITY:
        _require(contract.get(field) is False, f"protocol cannot authorize {field}")
    _require(len(value.get("required_report_sections") or []) == 19, "research report schema is incomplete")
    _require(len(value.get("overfitting_checklist") or []) == 10, "overfitting checklist is incomplete")
    return {
        "status": "VALIDATION_PASSED",
        "protocol": PROTOCOL,
        "subset_stages": STAGES,
        "holdouts": ["company", "wording", "metric", "composition"],
        "missing_evaluator_action": "STOP_BEFORE_MODEL_CHANGE",
    }


def validate_generalization_protocol_file(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("protocol config must contain one JSON object")
    return validate_generalization_protocol(value)
