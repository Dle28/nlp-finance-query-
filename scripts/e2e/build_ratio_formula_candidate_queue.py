#!/usr/bin/env python3
"""Materialize a candidate-only handoff for controlled ratio formulas.

The queue admits only a previously compiled two-operand ``divide`` plan.  It
does not select source rows/cells, execute the formula, or authorize an
answer.  Those remain separate source-grounded and human-review gates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.e2e.candidate_handoff import (
    BLOCKER_PROTOCOL,
    PLAN_PROTOCOL,
    SOURCE_CONTRACT,
    canonical_sha256,
    load_blocker_rows,
    load_typed_plans,
    sha256_file,
    write_jsonl,
)


PROTOCOL = "vifinqa_controlled_ratio_formula_candidate_queue_v1"


def _operand_rejection_reasons(operand: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not isinstance(operand.get("operand_id"), str) or not str(operand["operand_id"]).strip():
        reasons.append("formula_operand_id_missing")
    if not [hint for hint in operand.get("metric_hints") or [] if isinstance(hint, str) and hint.strip()]:
        reasons.append("formula_operand_metric_hint_missing")
    if not isinstance(operand.get("entity"), str) or not str(operand.get("entity")).strip():
        reasons.append("formula_operand_entity_missing")
    years = operand.get("years")
    if (
        not isinstance(years, list)
        or len(years) != 1
        or isinstance(years[0], bool)
        or not isinstance(years[0], int)
    ):
        reasons.append("formula_operand_period_not_single_year")
    unit_contract = operand.get("unit_contract")
    if not isinstance(unit_contract, Mapping) or unit_contract.get("source_unit_required") is not True:
        reasons.append("formula_operand_source_unit_not_required")
    grounding_contract = operand.get("grounding_contract")
    required_grounding = {
        "exact_internal_table_uid",
        "exact_row_index",
        "exact_column_index",
        "exact_raw_cell",
        "canonical_header_required",
    }
    if not isinstance(grounding_contract, Mapping) or any(
        grounding_contract.get(key) is not True for key in required_grounding
    ):
        reasons.append("formula_operand_exact_grounding_contract_missing")
    return reasons


def ratio_formula_rejection_reasons(plan: Mapping[str, Any]) -> list[str]:
    """Require an explicit, unambiguous divide AST before source review starts."""

    reasons: list[str] = []
    if plan.get("decomposition_status") != "complete":
        reasons.append("plan_not_complete")
    if plan.get("effective_family") != "ratio_or_derived":
        reasons.append("plan_not_ratio_or_derived")
    if plan.get("route") != "controlled_formula_template":
        reasons.append("plan_not_controlled_formula_template")
    if not isinstance(plan.get("formula_id"), str) or not str(plan.get("formula_id")).strip():
        reasons.append("formula_id_missing")
    ast = plan.get("operation_ast")
    operands = plan.get("operands")
    if not isinstance(ast, Mapping) or ast.get("op") != "divide":
        reasons.append("plan_not_divide_ast")
        return sorted(set(reasons))
    args = ast.get("args")
    if (
        not isinstance(args, list)
        or len(args) != 2
        or any(not isinstance(value, str) or not value for value in args)
        or len(set(args)) != 2
    ):
        reasons.append("divide_ast_not_two_distinct_operands")
        return sorted(set(reasons))
    if not isinstance(operands, list) or len(operands) != 2 or any(
        not isinstance(operand, Mapping) for operand in operands
    ):
        reasons.append("formula_not_two_operands")
        return sorted(set(reasons))
    by_id = {str(operand.get("operand_id") or ""): operand for operand in operands}
    if len(by_id) != 2 or set(by_id) != set(args):
        reasons.append("divide_operand_id_mismatch")
        return sorted(set(reasons))
    for operand in operands:
        reasons.extend(_operand_rejection_reasons(operand))
    return sorted(set(reasons))


def build_ratio_formula_candidate_queue(
    *,
    blocker_manifest: Path,
    typed_operand_plans: Path,
    typed_operand_plans_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite ratio formula queue: {output_dir}")
    blocker_manifest = blocker_manifest.resolve()
    typed_operand_plans = typed_operand_plans.resolve()
    typed_operand_plans_manifest = typed_operand_plans_manifest.resolve()
    blocker_rows = load_blocker_rows(blocker_manifest)
    plans = load_typed_plans(typed_operand_plans, typed_operand_plans_manifest)
    tracked = [
        row
        for row in blocker_rows
        if row.get("remediation_track") == "controlled_ratio_template"
    ]

    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    first_blockers = Counter()
    exclusion_reasons = Counter()
    for blocker in sorted(tracked, key=lambda value: int(value["question_id"])):
        question_id = int(blocker["question_id"])
        plan = plans.get(question_id)
        reasons = ["typed_plan_missing"] if plan is None else ratio_formula_rejection_reasons(plan)
        if reasons:
            exclusion_reasons.update(reasons)
            exclusions.append(
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "question_id": question_id,
                    "selection_status": "not_admitted_to_controlled_ratio_formula",
                    "reason_codes": reasons,
                    "blocker_queue_row_sha256": canonical_sha256(blocker),
                    "typed_plan_row_sha256": canonical_sha256(plan) if plan is not None else None,
                    "source_contract": SOURCE_CONTRACT,
                }
            )
            continue
        assert plan is not None
        first_blocker = str(blocker.get("first_blocker") or "unknown")
        first_blockers[first_blocker] += 1
        candidates.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "question": blocker.get("question"),
                "selection_status": "candidate_requires_operand_route_period_binding_rebuild",
                "first_blocker": first_blocker,
                "base_route_status": blocker.get("route_status"),
                "formula_id": plan["formula_id"],
                "planned_operation_ast": dict(plan["operation_ast"]),
                "planned_operands": [dict(operand) for operand in plan["operands"]],
                "plan_fingerprint": plan.get("plan_fingerprint"),
                "required_rebuilds": [
                    "source_grounded_operand_routes",
                    "period_packets_for_each_operand",
                    "exact_cell_bindings_for_each_operand",
                    "source_unit_bindings_for_each_operand",
                    "formula_compatibility",
                    "semantic_approval",
                    "deterministic_replay",
                ],
                "blocker_queue_row_sha256": canonical_sha256(blocker),
                "typed_plan_row_sha256": canonical_sha256(plan),
                "source_contract": SOURCE_CONTRACT,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    candidate_path = output_dir / "ratio_formula_candidate_queue.jsonl"
    exclusion_path = output_dir / "ratio_formula_exclusions.jsonl"
    write_jsonl(candidate_path, candidates)
    write_jsonl(exclusion_path, exclusions)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "candidates_materialized_no_formula_or_answer_executed",
        "counts": {
            "controlled_ratio_track_count": len(tracked),
            "candidate_count": len(candidates),
            "excluded_count": len(exclusions),
            "candidate_first_blocker_counts": dict(sorted(first_blockers.items())),
            "exclusion_reason_counts": dict(sorted(exclusion_reasons.items())),
        },
        "source_contract": SOURCE_CONTRACT,
    }
    summary_path = output_dir / "ratio_formula_queue_summary_v1.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    inputs = {
        "blocker_manifest": blocker_manifest,
        "typed_operand_plans": typed_operand_plans,
        "typed_operand_plans_manifest": typed_operand_plans_manifest,
    }
    result = {
        **summary,
        "inputs": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in inputs.items()
        },
        "outputs": {
            "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
            "candidates": {"path": str(candidate_path), "sha256": sha256_file(candidate_path)},
            "exclusions": {"path": str(exclusion_path), "sha256": sha256_file(exclusion_path)},
        },
    }
    manifest_path = output_dir / "ratio_formula_candidate_queue.manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocker-manifest", type=Path, required=True)
    parser.add_argument("--typed-operand-plans", type=Path, required=True)
    parser.add_argument("--typed-operand-plans-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_ratio_formula_candidate_queue(**vars(args))
    print(
        json.dumps(
            {
                "status": result["status"],
                "counts": result["counts"],
                "manifest_path": result["manifest_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
