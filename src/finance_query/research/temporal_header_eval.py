"""Evaluate the frozen temporal-header rule without materializing source text."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .temporal_header_experiment import (
    _record,
    question_only_temporal,
    temporal_header_prediction,
)


PROTOCOL = "temporal_header_semantics_evaluation_v1"
ARMS = {
    "A_question_only_temporal_parser": (False, False),
    "B_A_plus_source_header_backoff": (True, True),
    "C_B_without_duration_header_anchor": (False, True),
    "D_B_without_instant_header_anchor": (True, False),
}


def evaluate_temporal_header_stage(
    *,
    assignments: Iterable[Mapping[str, Any]],
    finqa_items: Iterable[Mapping[str, Any]],
    stage: str,
    split: str,
    seed: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [dict(row) for row in assignments if row.get("benchmark_stage") == stage]
    source: dict[str, Mapping[str, Any]] = {}
    for item in finqa_items:
        record = _record(item, split, seed)
        source[record["record_sha256"]] = item
    result_rows: list[dict[str, Any]] = []
    arm_counts: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    metric_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    composition_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    gold_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    for assignment in selected:
        item = source.get(str(assignment["record_sha256"]))
        if item is None:
            raise ValueError("sealed temporal assignment cannot be resolved")
        qa = item.get("qa") or {}
        question = str(qa.get("question") or "")
        table = list(item.get("table") or [])
        gold = str(assignment["gold_temporal_type"])
        recomputed = _record(item, split, seed)["gold_temporal_type"]
        if recomputed != gold:
            raise ValueError("sealed temporal gold does not match pinned source")
        arm_results: dict[str, dict[str, Any]] = {}
        for arm, (allow_duration, allow_instant) in ARMS.items():
            prediction = (
                question_only_temporal(question)
                if arm == "A_question_only_temporal_parser"
                else temporal_header_prediction(
                    question,
                    table,
                    allow_duration=allow_duration,
                    allow_instant=allow_instant,
                )
            )
            correct = prediction == gold if prediction is not None else None
            false_confident = prediction is not None and prediction != gold
            arm_results[arm] = {
                "predicted": prediction is not None,
                "correct_if_predicted": correct,
                "false_confident": false_confident,
                "prediction_label": prediction,
            }
            arm_counts[arm]["predicted" if prediction is not None else "abstained"] += 1
            if prediction is not None:
                arm_counts[arm]["correct" if correct else "incorrect"] += 1
                if false_confident:
                    arm_counts[arm]["false_confident"] += 1
        baseline = arm_results["A_question_only_temporal_parser"]
        candidate = arm_results["B_A_plus_source_header_backoff"]
        if candidate["correct_if_predicted"] is True and baseline["predicted"] is False:
            outcome = "IMPROVED"
        elif baseline["correct_if_predicted"] is True and candidate["correct_if_predicted"] is not True:
            outcome = "REGRESSED"
        else:
            outcome = "UNCHANGED"
        outcomes[outcome] += 1
        metric_outcomes[str(assignment["metric_family"])][outcome] += 1
        composition_outcomes[str(assignment["composition_signature"])][outcome] += 1
        gold_outcomes[gold][outcome] += 1
        result_rows.append({
            "protocol": PROTOCOL,
            "benchmark_stage": stage,
            "source_split": split,
            "record_sha256": assignment["record_sha256"],
            "source_record_tracking_hash": assignment["source_record_tracking_hash"],
            "metric_family": assignment["metric_family"],
            "composition_signature": assignment["composition_signature"],
            "gold_temporal_type": gold,
            "arm_results": arm_results,
            "candidate_outcome": outcome,
            "question_or_header_materialized": False,
            "source_contract": assignment["source_contract"],
        })
    if len(result_rows) != len(selected):
        raise ValueError("temporal evaluation did not cover frozen stage")
    arms: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        counts = arm_counts[arm]
        predicted = counts["predicted"]
        arms[arm] = {
            "predicted_count": predicted,
            "abstain_count": counts["abstained"],
            "correct_count": counts["correct"],
            "incorrect_count": counts["incorrect"],
            "false_confident_count": counts["false_confident"],
            "accuracy_on_predictions": counts["correct"] / predicted if predicted else None,
            "false_confidence_rate": counts["false_confident"] / predicted if predicted else None,
        }
    report = {
        "protocol": PROTOCOL,
        "benchmark_stage": stage,
        "source_split": split,
        "record_count": len(result_rows),
        "arms": arms,
        "candidate_outcome_counts": dict(sorted(outcomes.items())),
        "metric_outcome_counts": {
            key: dict(sorted(value.items())) for key, value in sorted(metric_outcomes.items())
        },
        "composition_outcome_counts": {
            key: dict(sorted(value.items()))
            for key, value in sorted(composition_outcomes.items())
        },
        "gold_temporal_outcome_counts": {
            key: dict(sorted(value.items())) for key, value in sorted(gold_outcomes.items())
        },
        "question_or_header_materialized": False,
        "full_vifinqa_dataset_allowed": False,
    }
    return result_rows, report


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
