#!/usr/bin/env python3
"""Materialize a fail-closed handoff for plain direct-lookup route rebuilds.

This is deliberately a queue producer, not a route overlay mutator.  A typed
question plan can show that a question has one deterministic lookup operand,
but it cannot choose a table, period column, source cell, value or semantic
approval.  Every selected row therefore remains candidate-only until a new
route/period/binding lineage is built and replayed.
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

PROTOCOL = "vifinqa_controlled_direct_lookup_candidate_queue_v1"


def _direct_lookup_rejection_reasons(plan: Mapping[str, Any]) -> list[str]:
    """Return why a plan is not the one-operand lookup template.

    This predicate intentionally checks the serialized plan, rather than its
    route label.  A legacy ``existing_typed_plan`` is eligible only if its AST
    is the exact single-operand lookup shape.
    """

    reasons: list[str] = []
    if plan.get("decomposition_status") != "complete":
        reasons.append("plan_not_complete")
    if plan.get("effective_family") != "direct_lookup":
        reasons.append("plan_not_direct_lookup")
    ast = plan.get("operation_ast")
    operands = plan.get("operands")
    if not isinstance(ast, Mapping) or ast.get("op") != "lookup":
        reasons.append("plan_not_lookup_ast")
        return reasons
    args = ast.get("args")
    if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], str):
        reasons.append("plan_not_single_operand_lookup")
        return reasons
    if not isinstance(operands, list) or len(operands) != 1 or not isinstance(operands[0], Mapping):
        reasons.append("plan_not_single_operand_lookup")
        return reasons
    operand = operands[0]
    if operand.get("operand_id") != args[0]:
        reasons.append("lookup_operand_id_mismatch")
    if not [hint for hint in operand.get("metric_hints") or [] if isinstance(hint, str) and hint.strip()]:
        reasons.append("lookup_metric_hint_missing")
    if not isinstance(operand.get("entity"), str) or not str(operand.get("entity")).strip():
        reasons.append("lookup_entity_missing")
    years = operand.get("years")
    if (
        not isinstance(years, list)
        or len(years) != 1
        or isinstance(years[0], bool)
        or not isinstance(years[0], int)
    ):
        reasons.append("lookup_period_not_single_year")
    unit_contract = operand.get("unit_contract")
    if not isinstance(unit_contract, Mapping) or unit_contract.get("source_unit_required") is not True:
        reasons.append("lookup_source_unit_not_required")
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
        reasons.append("lookup_exact_grounding_contract_missing")
    return sorted(set(reasons))


def build_direct_lookup_candidate_queue(
    *,
    blocker_manifest: Path,
    typed_operand_plans: Path,
    typed_operand_plans_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create a candidate-only direct-lookup handoff from two immutable artifacts."""

    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite direct lookup queue: {output_dir}")
    blocker_manifest = blocker_manifest.resolve()
    typed_operand_plans = typed_operand_plans.resolve()
    typed_operand_plans_manifest = typed_operand_plans_manifest.resolve()
    blocker_rows = load_blocker_rows(blocker_manifest)
    plans = load_typed_plans(typed_operand_plans, typed_operand_plans_manifest)

    tracked = [
        row
        for row in blocker_rows
        if row.get("remediation_track") == "controlled_direct_lookup_template"
    ]
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    first_blockers = Counter()
    exclusion_reasons = Counter()
    for blocker in sorted(tracked, key=lambda value: int(value["question_id"])):
        question_id = int(blocker["question_id"])
        plan = plans.get(question_id)
        reasons = ["typed_plan_missing"] if plan is None else _direct_lookup_rejection_reasons(plan)
        if reasons:
            exclusion_reasons.update(reasons)
            exclusions.append(
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "question_id": question_id,
                    "selection_status": "not_admitted_to_controlled_direct_lookup",
                    "reason_codes": reasons,
                    "blocker_queue_row_sha256": canonical_sha256(blocker),
                    "typed_plan_row_sha256": canonical_sha256(plan) if plan is not None else None,
                    "source_contract": SOURCE_CONTRACT,
                }
            )
            continue
        assert plan is not None
        operand = dict((plan.get("operands") or [])[0])
        first_blocker = str(blocker.get("first_blocker") or "unknown")
        first_blockers[first_blocker] += 1
        candidates.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "question": blocker.get("question"),
                "selection_status": "candidate_requires_route_period_binding_rebuild",
                "first_blocker": first_blocker,
                "base_route_status": blocker.get("route_status"),
                "planned_operation_ast": dict(plan["operation_ast"]),
                "planned_operand": operand,
                "plan_fingerprint": plan.get("plan_fingerprint"),
                "required_rebuilds": [
                    "source_grounded_route",
                    "period_packet",
                    "exact_cell_binding",
                    "source_unit_binding",
                    "semantic_approval",
                    "deterministic_replay",
                ],
                "blocker_queue_row_sha256": canonical_sha256(blocker),
                "typed_plan_row_sha256": canonical_sha256(plan),
                "source_contract": SOURCE_CONTRACT,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    candidate_path = output_dir / "direct_lookup_candidate_queue.jsonl"
    exclusion_path = output_dir / "direct_lookup_exclusions.jsonl"
    write_jsonl(candidate_path, candidates)
    write_jsonl(exclusion_path, exclusions)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "candidates_materialized_no_route_or_answer_promoted",
        "counts": {
            "direct_lookup_track_count": len(tracked),
            "candidate_count": len(candidates),
            "excluded_count": len(exclusions),
            "candidate_first_blocker_counts": dict(sorted(first_blockers.items())),
            "exclusion_reason_counts": dict(sorted(exclusion_reasons.items())),
        },
        "source_contract": SOURCE_CONTRACT,
    }
    summary_path = output_dir / "direct_lookup_queue_summary_v1.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    manifest_path = output_dir / "direct_lookup_candidate_queue.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocker-manifest", type=Path, required=True)
    parser.add_argument("--typed-operand-plans", type=Path, required=True)
    parser.add_argument("--typed-operand-plans-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_direct_lookup_candidate_queue(**vars(args))
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
