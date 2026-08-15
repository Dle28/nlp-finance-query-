#!/usr/bin/env python3
"""Fail closed when deciding whether a retriever may enter a next experiment.

This script reads a hash-bound issuer-held-out evaluation manifest.  It does
not promote any model.  A candidate only becomes eligible for a *follow-up
experiment* when it strictly improves the chosen Recall@K over the base on
both validation and test and does not worsen Top-1 wrong-year/scope rates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


EVALUATION_PROTOCOL = "synthetic_issuer_heldout_retriever_evaluation_v1"
ASSESSMENT_PROTOCOL = "synthetic_retriever_candidate_assessment_v1"
REQUIRED_SPLITS = ("validation", "test")
SAFETY_BUCKETS = ("wrong_year", "wrong_scope")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-manifest", type=Path, required=True)
    parser.add_argument("--base-label", default="base")
    parser.add_argument("--candidate-label", default="finetuned")
    parser.add_argument("--required-k", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _metric_rate(metrics: Mapping[str, Any], bucket: str) -> float:
    rates = metrics.get("top1_error_rates")
    if not isinstance(rates, Mapping):
        raise ValueError("Evaluation metrics omit top1_error_rates")
    value = rates.get(bucket, 0.0)
    if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"Invalid top1 error rate for {bucket!r}")
    return float(value)


def _recall_at_k(metrics: Mapping[str, Any], required_k: int) -> float:
    recalls = metrics.get("recall_at_k")
    value = recalls.get(str(required_k)) if isinstance(recalls, Mapping) else None
    if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"Evaluation metrics omit a valid Recall@{required_k}")
    return float(value)


def validate_evaluation_manifest(payload: Mapping[str, Any]) -> None:
    if payload.get("protocol") != EVALUATION_PROTOCOL:
        raise ValueError("Unexpected evaluation protocol")
    if payload.get("promotion_status") != "offline_evaluation_complete_not_promoted":
        raise ValueError("Evaluation manifest must remain offline and not promoted")
    source_contract = payload.get("source_contract")
    if not isinstance(source_contract, Mapping) or source_contract.get("benchmark_questions_read") is not False:
        raise ValueError("Candidate assessment requires a benchmark-free evaluation manifest")
    if source_contract.get("issuer_held_out_splits_only") is not True:
        raise ValueError("Candidate assessment requires issuer-held-out-only evaluation")


def assess_candidate(
    models: Mapping[str, Any],
    *,
    base_label: str,
    candidate_label: str,
    required_k: int,
) -> dict[str, Any]:
    if required_k < 1:
        raise ValueError("required-k must be positive")
    if base_label == candidate_label:
        raise ValueError("base-label and candidate-label must differ")
    base = models.get(base_label)
    candidate = models.get(candidate_label)
    if not isinstance(base, Mapping) or not isinstance(candidate, Mapping):
        raise ValueError("Both base and candidate model labels must exist")
    base_splits, candidate_splits = base.get("splits"), candidate.get("splits")
    if not isinstance(base_splits, Mapping) or not isinstance(candidate_splits, Mapping):
        raise ValueError("Model result is missing split metrics")

    split_results: dict[str, Any] = {}
    all_recall_improved = True
    all_safety_preserved = True
    for split in REQUIRED_SPLITS:
        base_metrics, candidate_metrics = base_splits.get(split), candidate_splits.get(split)
        if not isinstance(base_metrics, Mapping) or not isinstance(candidate_metrics, Mapping):
            raise ValueError(f"Both models must include held-out split={split!r}")
        base_recall = _recall_at_k(base_metrics, required_k)
        candidate_recall = _recall_at_k(candidate_metrics, required_k)
        recall_improved = candidate_recall > base_recall
        safety = {
            bucket: {
                "base_rate": _metric_rate(base_metrics, bucket),
                "candidate_rate": _metric_rate(candidate_metrics, bucket),
            }
            for bucket in SAFETY_BUCKETS
        }
        safety_preserved = all(
            values["candidate_rate"] <= values["base_rate"] for values in safety.values()
        )
        all_recall_improved &= recall_improved
        all_safety_preserved &= safety_preserved
        split_results[split] = {
            "base_recall_at_k": base_recall,
            "candidate_recall_at_k": candidate_recall,
            "recall_strictly_improved": recall_improved,
            "safety": safety,
            "safety_preserved": safety_preserved,
        }
    eligible = all_recall_improved and all_safety_preserved
    return {
        "required_recall_k": required_k,
        "base_label": base_label,
        "candidate_label": candidate_label,
        "split_results": split_results,
        "candidate_status": (
            "eligible_for_followup_experiment_not_promoted"
            if eligible
            else "rejected_by_issuer_heldout_gate_not_promoted"
        ),
        "promotion_status": "not_promoted",
        "reasons": {
            "recall_improved_on_all_held_out_splits": all_recall_improved,
            "wrong_year_and_scope_not_worse_on_all_held_out_splits": all_safety_preserved,
        },
    }


def main() -> None:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite candidate assessment: {args.output}")
    payload = json.loads(args.evaluation_manifest.read_text(encoding="utf-8"))
    validate_evaluation_manifest(payload)
    models = payload.get("models")
    if not isinstance(models, Mapping):
        raise ValueError("Evaluation manifest has no model results")
    assessment = assess_candidate(
        models,
        base_label=args.base_label,
        candidate_label=args.candidate_label,
        required_k=args.required_k,
    )
    output = {
        "protocol": ASSESSMENT_PROTOCOL,
        "evaluation_manifest": str(args.evaluation_manifest),
        "assessment": assessment,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
