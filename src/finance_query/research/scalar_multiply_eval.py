"""Score a frozen scalar-multiply policy without materializing benchmark text."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .finqa_dsl_eval import execute_finqa_program
from .scalar_multiply_experiment import SOURCE_CONTRACT, STEP_RE, _eligible, _record, _seal


PROTOCOL = "dimensionless_scalar_multiply_evaluation_v1"
ARMS: dict[str, frozenset[str]] = {
    "A_current_locked_kernel_contract": frozenset(),
    "B_A_plus_named_constant_or_percent_scalar": frozenset({"named_constant", "percent_literal"}),
    "C_B_without_percent_literal_scalars": frozenset({"named_constant"}),
    "D_B_without_named_constant_scalars": frozenset({"percent_literal"}),
}


def _token_scalar_kind(token: str) -> str | None:
    value = token.strip()
    if value.startswith("const_"):
        return "named_constant"
    if value.endswith("%"):
        return "percent_literal"
    return None


def scalar_policy_eligible(program: str, allowed_scalar_kinds: frozenset[str]) -> bool:
    multiply_count = 0
    for match in STEP_RE.finditer(program):
        if match.group(1) != "multiply":
            continue
        multiply_count += 1
        arguments = match.group(2).split(", ", 1)
        if len(arguments) != 2:
            return False
        kinds = {_token_scalar_kind(token) for token in arguments}
        kinds.discard(None)
        if not kinds.intersection(allowed_scalar_kinds):
            return False
    return multiply_count > 0


def _matches(actual: Any, expected: Any) -> bool:
    from .finqa_dsl_eval import _answer_matches

    return _answer_matches(actual, expected)


def evaluate_scalar_multiply_stage(
    *,
    assignments: Iterable[Mapping[str, Any]],
    finqa_items: Iterable[Mapping[str, Any]],
    stage: str,
    split: str,
    seed: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stage_assignments = [dict(row) for row in assignments if row.get("benchmark_stage") == stage]
    source_by_hash: dict[str, Mapping[str, Any]] = {}
    for item in finqa_items:
        record = _record(item, split, seed)
        source_by_hash[record["record_sha256"]] = item
    result_rows: list[dict[str, Any]] = []
    arm_counts: dict[str, Counter[str]] = defaultdict(Counter)
    outcome_counts: Counter[str] = Counter()
    metric_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    composition_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    for assignment in stage_assignments:
        item = source_by_hash.get(str(assignment["record_sha256"]))
        if item is None:
            raise ValueError("sealed assignment cannot be resolved against its pinned source split")
        qa = item.get("qa") or {}
        program = str(qa.get("program") or "")
        actual, _operators = execute_finqa_program(program, list(item.get("table") or []))
        correct = _matches(actual, qa.get("exe_ans"))
        arm_results: dict[str, dict[str, Any]] = {}
        for arm, allowed_kinds in ARMS.items():
            eligible = scalar_policy_eligible(program, allowed_kinds)
            false_confident = eligible and not correct
            arm_results[arm] = {
                "eligible": eligible,
                "correct_if_eligible": correct if eligible else None,
                "false_confident": false_confident,
            }
            arm_counts[arm]["eligible" if eligible else "blocked"] += 1
            if eligible:
                arm_counts[arm]["correct" if correct else "incorrect"] += 1
                if false_confident:
                    arm_counts[arm]["false_confident"] += 1
        baseline = arm_results["A_current_locked_kernel_contract"]
        candidate = arm_results["B_A_plus_named_constant_or_percent_scalar"]
        if candidate["eligible"] and candidate["correct_if_eligible"] and not baseline["eligible"]:
            outcome = "IMPROVED"
        elif baseline["eligible"] and (not candidate["eligible"] or not candidate["correct_if_eligible"]):
            outcome = "REGRESSED"
        else:
            outcome = "UNCHANGED"
        outcome_counts[outcome] += 1
        metric_outcomes[str(assignment["metric_family"])][outcome] += 1
        composition_outcomes[str(assignment["composition_signature"])][outcome] += 1
        result_rows.append({
            "protocol": PROTOCOL,
            "benchmark_stage": stage,
            "source_split": split,
            "record_sha256": assignment["record_sha256"],
            "source_record_tracking_hash": assignment["source_record_tracking_hash"],
            "metric_family": assignment["metric_family"],
            "composition_signature": assignment["composition_signature"],
            "scalar_kinds": assignment["scalar_kinds"],
            "arm_results": arm_results,
            "candidate_outcome": outcome,
            "question_or_program_materialized": False,
            "source_contract": assignment["source_contract"],
        })
    if len(result_rows) != len(stage_assignments):
        raise ValueError("evaluation did not cover the frozen stage")
    report_arms: dict[str, dict[str, Any]] = {}
    for arm, counts in arm_counts.items():
        eligible = counts["eligible"]
        report_arms[arm] = {
            "eligible_count": eligible,
            "blocked_count": counts["blocked"],
            "correct_count": counts["correct"],
            "incorrect_count": counts["incorrect"],
            "false_confident_count": counts["false_confident"],
            "accuracy_on_eligible": counts["correct"] / eligible if eligible else None,
            "false_confidence_rate": counts["false_confident"] / eligible if eligible else None,
        }
    report = {
        "protocol": PROTOCOL,
        "benchmark_stage": stage,
        "source_split": split,
        "record_count": len(result_rows),
        "arms": report_arms,
        "candidate_outcome_counts": dict(sorted(outcome_counts.items())),
        "metric_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(metric_outcomes.items())},
        "composition_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(composition_outcomes.items())},
        "question_or_program_materialized": False,
        "full_vifinqa_dataset_allowed": False,
    }
    return result_rows, report


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_scalar_multiply_full_reference(
    *,
    finqa_items_by_split: Mapping[str, Iterable[Mapping[str, Any]]],
    seed: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate the retained rule on every uncontaminated eligible FinQA row."""
    all_rows: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for split in ("train", "dev", "test"):
        items = [dict(item) for item in finqa_items_by_split.get(split, [])]
        records = _eligible(_record(item, split, seed) for item in items)
        assignments = [_seal(record, "full_dataset") for record in records]
        rows, _report = evaluate_scalar_multiply_stage(
            assignments=assignments,
            finqa_items=items,
            stage="full_dataset",
            split=split,
            seed=seed,
        )
        all_rows.extend(rows)
        source_counts[split] = len(rows)

    arm_counts: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    metric_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    composition_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in all_rows:
        outcome = str(row["candidate_outcome"])
        outcomes[outcome] += 1
        metric_outcomes[str(row["metric_family"])][outcome] += 1
        composition_outcomes[str(row["composition_signature"])][outcome] += 1
        for arm, result in row["arm_results"].items():
            if result["eligible"]:
                arm_counts[arm]["eligible"] += 1
                if result["correct_if_eligible"]:
                    arm_counts[arm]["correct"] += 1
                else:
                    arm_counts[arm]["incorrect"] += 1
                if result["false_confident"]:
                    arm_counts[arm]["false_confident"] += 1
            else:
                arm_counts[arm]["blocked"] += 1
    arms: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        counts = arm_counts[arm]
        eligible = counts["eligible"]
        arms[arm] = {
            "eligible_count": eligible,
            "blocked_count": counts["blocked"],
            "correct_count": counts["correct"],
            "incorrect_count": counts["incorrect"],
            "false_confident_count": counts["false_confident"],
            "accuracy_on_eligible": counts["correct"] / eligible if eligible else None,
            "false_confidence_rate": counts["false_confident"] / eligible if eligible else None,
        }
    report = {
        "protocol": "dimensionless_scalar_multiply_full_external_reference_v1",
        "benchmark_stage": "full_dataset_after_unseen_gate",
        "source_dataset": "finqa",
        "source_split_counts": source_counts,
        "record_count": len(all_rows),
        "arms": arms,
        "candidate_outcome_counts": dict(sorted(outcomes.items())),
        "metric_outcome_counts": {
            key: dict(sorted(value.items())) for key, value in sorted(metric_outcomes.items())
        },
        "composition_outcome_counts": {
            key: dict(sorted(value.items()))
            for key, value in sorted(composition_outcomes.items())
        },
        "contaminated_programs_excluded": ["exp(", "greater("],
        "question_or_program_materialized": False,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
    return all_rows, report
