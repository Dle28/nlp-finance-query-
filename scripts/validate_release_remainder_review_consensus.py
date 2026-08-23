#!/usr/bin/env python3
"""Fail-closed validators for the three production-release remainder lanes.

Every lane revalidates the two independent human reviews and their immutable
reconciliation, checks that the reviewed typed-plan blocker still exists, and
reopens source identity through V2/V3.  The result is a source-locatable
receipt only. It cannot compile code, select a conflict winner, create formula
evidence, execute a QueryProgram, write an answer, or change release state.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_reconcile = _module("reconcile_production_release_intake_reviews", "reconcile_production_release_intake_reviews.py")
_review = _reconcile._review
_source = _module("validate_typed_plan_review_consensus", "validate_typed_plan_review_consensus.py")
_queue = _module("build_release_remainder_intake_queues", "build_release_remainder_intake_queues.py")


PROTOCOL = "production_release_remainder_independent_review_consensus_validation_v1"
RECONCILIATION_PROTOCOL = "production_release_intake_independent_review_reconciliation_v1"
INTAKE_PROTOCOL = "production_release_remainder_intakes_v1"
TYPED_PLAN_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"
SOURCE_CONTRACT = _review.SOURCE_CONTRACT
LANE_SPECS: dict[str, dict[str, Any]] = {
    "deterministic_executor_compile": {
        "intake_kind": "blank_executor_contract_review",
        "proposal_fields": frozenset(
            {"plan_fingerprint", "required_operand_ids", "operation_ast", "executor_disposition"}
        ),
        "disposition_key": "executor_disposition",
        "disposition": "require_new_deterministic_executor_after_exact_evidence",
        "output_state": "executor_contract_source_validated_non_materializable",
        "blocker_state": "typed_non_executable",
    },
    "exact_source_conflict_adjudication": {
        "intake_kind": "blank_exact_source_conflict_review",
        "proposal_fields": frozenset(
            {"plan_fingerprint", "required_operand_ids", "operator", "conflict_disposition"}
        ),
        "disposition_key": "conflict_disposition",
        "disposition": "retain_conflict_pending_fresh_exact_replay",
        "output_state": "conflict_contract_source_validated_non_materializable",
        "blocker_state": "exact_source_conflict",
    },
    "query_program_shadow_completion": {
        "intake_kind": "blank_query_program_shadow_review",
        "proposal_fields": frozenset(
            {"plan_fingerprint", "formula_id", "required_operand_ids", "operation_ast", "program_disposition"}
        ),
        "disposition_key": "program_disposition",
        "disposition": "require_new_source_bound_shadow_program_rebuild",
        "output_state": "query_program_contract_source_validated_non_materializable",
        "blocker_state": "query_program_shadow_incomplete",
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    return _review.load_json(path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return _review.load_jsonl(path)


def _index(rows: list[dict[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if type(value) is not int or value in output:
            raise ValueError(f"{label} must contain unique integer {key} values")
        output[value] = row
    return output


def _required_operands(plan: Mapping[str, Any], question_id: int) -> list[str]:
    values = sorted(
        str(operand.get("operand_id") or "")
        for operand in plan.get("operands") or []
        if isinstance(operand, Mapping) and operand.get("required") is True
    )
    if not values or "" in values or len(values) != len(set(values)):
        raise ValueError(f"Q{question_id}: typed plan has invalid required operands")
    return values


def _validate_baseline(
    *, descriptor: Mapping[str, Any], bundle_dir: Path
) -> tuple[str, dict[int, dict[str, Any]], Path, Path]:
    lane = str(descriptor["output_name"])
    spec = LANE_SPECS.get(lane)
    if spec is None:
        raise ValueError("Remainder consensus validator received an unsupported lane")
    lineage = descriptor["lineage"]
    typed_path = Path(lineage["typed_plans"]["path"])
    typed_manifest_path = Path(lineage["typed_plans_manifest"]["path"])
    review_items_path = bundle_dir / "review_items.jsonl"
    if _source.sha256_file(review_items_path) != lineage["bundle_review_items"]["sha256"]:
        raise ValueError("Bundle review items do not bind remainder intake")
    typed_manifest = _load_json(typed_manifest_path)
    if (
        typed_manifest.get("protocol") != TYPED_PLAN_PROTOCOL
        or typed_manifest.get("sidecar_sha256") != _source.sha256_file(typed_path)
        or typed_manifest.get("review_items_sha256") != _source.sha256_file(review_items_path)
    ):
        raise ValueError("Typed-plan sidecar does not bind immutable review items")
    items = _index(_load_jsonl(review_items_path), "id", "review items")
    plans = _index(_load_jsonl(typed_path), "question_id", "typed plans")
    if set(items) != set(plans):
        raise ValueError("Typed-plan sidecar coverage differs from review items")
    for question_id, queue_row in descriptor["rows_by_id"].items():
        item, plan = items.get(question_id), plans.get(question_id)
        context = {
            "question": None if item is None else item.get("question"),
            "question_plan": None if item is None else item.get("question_plan"),
            "typed_plan_contract": None if plan is None else _queue.typed_contract(plan),
            "remediation_lane": lane,
        }
        if (
            item is None
            or plan is None
            or queue_row.get("intake_kind") != spec["intake_kind"]
            or queue_row.get("plan_fingerprint") != plan.get("plan_fingerprint")
            or queue_row.get("review_context") != context
            or queue_row.get("immutable_review_context_sha256") != _review.canonical_sha256(context)
        ):
            raise ValueError(f"Q{question_id}: remainder intake does not bind its frozen typed plan")
        _required_operands(plan, question_id)
    return lane, plans, typed_path, review_items_path


def _validate_lane_blocker(*, lane: str, plan: Mapping[str, Any], question_id: int) -> None:
    if lane == "deterministic_executor_compile":
        if plan.get("decomposition_status") != "typed_non_executable":
            raise ValueError(f"Q{question_id}: executor lane no longer has a non-executable typed plan")
        return
    if lane == "exact_source_conflict_adjudication":
        if (
            plan.get("decomposition_status") != "complete"
            or plan.get("effective_family") != "direct_lookup"
            or (plan.get("operation_ast") or {}).get("op") != "lookup"
        ):
            raise ValueError(f"Q{question_id}: source-conflict lane is not a complete direct lookup")
        return
    if lane == "query_program_shadow_completion":
        if plan.get("decomposition_status") != "complete" or not isinstance(plan.get("formula_id"), str):
            raise ValueError(f"Q{question_id}: QueryProgram lane lacks a complete controlled formula plan")
        return
    raise AssertionError(f"Unknown lane: {lane}")


def _validate_proposal(*, lane: str, proposal: object, plan: Mapping[str, Any], question_id: int) -> dict[str, Any]:
    spec = LANE_SPECS[lane]
    if not isinstance(proposal, dict) or set(proposal) != spec["proposal_fields"]:
        raise ValueError(f"Q{question_id}: {lane} proposal has unsupported fields")
    if (
        proposal.get("plan_fingerprint") != plan.get("plan_fingerprint")
        or proposal.get("required_operand_ids") != _required_operands(plan, question_id)
        or proposal.get(spec["disposition_key"]) != spec["disposition"]
    ):
        raise ValueError(f"Q{question_id}: {lane} proposal changes frozen typed contract")
    if lane == "exact_source_conflict_adjudication":
        if proposal.get("operator") != "lookup":
            raise ValueError(f"Q{question_id}: source-conflict proposal must retain lookup semantics")
    else:
        if proposal.get("operation_ast") != plan.get("operation_ast"):
            raise ValueError(f"Q{question_id}: {lane} proposal changes immutable operation AST")
    if lane == "query_program_shadow_completion" and proposal.get("formula_id") != plan.get("formula_id"):
        raise ValueError(f"Q{question_id}: QueryProgram proposal changes formula identity")
    return {
        "plan_fingerprint": str(plan["plan_fingerprint"]),
        "required_operand_count": len(_required_operands(plan, question_id)),
        "operator": str((plan.get("operation_ast") or {}).get("op") or ""),
        "disposition": spec["disposition"],
    }


def _validate_query_program_inputs(
    *, lane: str, plans: Mapping[int, Mapping[str, Any]], formula_evidence: Path | None,
    query_program: Path | None, bundle_dir: Path,
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[str, dict[str, str]]]:
    if lane != "query_program_shadow_completion":
        if formula_evidence is not None or query_program is not None:
            raise ValueError("Formula EvidenceSet and QueryProgram inputs apply only to the QueryProgram lane")
        return {}, {}, {}
    if formula_evidence is None or query_program is None:
        raise ValueError("QueryProgram consensus requires formula evidence and current QueryProgram sidecars")
    formula_manifest_path = formula_evidence.with_suffix(".manifest.json")
    program_manifest_path = query_program.with_suffix(".manifest.json")
    formula_manifest, program_manifest = _load_json(formula_manifest_path), _load_json(program_manifest_path)
    if (
        formula_manifest.get("sidecar_sha256") != _source.sha256_file(formula_evidence)
        or formula_manifest.get("bundle_review_items_sha256") != _source.sha256_file(bundle_dir / "review_items.jsonl")
        or program_manifest.get("formula_evidence_sha256") != _source.sha256_file(formula_evidence)
        or program_manifest.get("sidecar_sha256") != _source.sha256_file(query_program)
        or program_manifest.get("submission_eligible") is not False
    ):
        raise ValueError("QueryProgram/formula lineage is invalid")
    formulas = _index(_load_jsonl(formula_evidence), "id", "Formula EvidenceSets")
    programs = _index(_load_jsonl(query_program), "id", "QueryPrograms")
    for question_id, plan in plans.items():
        if question_id not in formulas or question_id not in programs:
            continue
        formula, program = formulas[question_id], programs[question_id]
        if (
            formula.get("evidence_completeness") != "complete"
            or ((formula.get("formula") or {}).get("formula_id")) != plan.get("formula_id")
            or ((program.get("shadow_execution") or {}).get("status")) == "shadow_complete"
        ):
            raise ValueError(f"Q{question_id}: QueryProgram lane blocker is not preserved")
    return formulas, programs, {
        "formula_evidence": {"path": str(formula_evidence), "sha256": _source.sha256_file(formula_evidence)},
        "query_program": {"path": str(query_program), "sha256": _source.sha256_file(query_program)},
    }


def validate(
    *, bundle_dir: Path, assignment_manifest: Path, reviewer_a_assignment: Path,
    reviewer_b_assignment: Path, reviewer_a_labels: Path, reviewer_b_labels: Path,
    reviewer_a_review_manifest: Path, reviewer_b_review_manifest: Path,
    reconciliation_manifest: Path, output_dir: Path,
    formula_evidence: Path | None = None, query_program: Path | None = None,
) -> dict[str, Any]:
    """Write lane-specific source-bound receipts, never a completed remediation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "production_release_remainder_independent_review_consensus_validation_v1.jsonl"
    manifest_path = output_dir / "production_release_remainder_independent_review_consensus_validation_v1.manifest.json"
    if output.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite remainder consensus validation: {output_dir}")
    receipt = _reconcile.validate_reconciliation_receipt(
        assignment_manifest=assignment_manifest,
        reviewer_a_assignment=reviewer_a_assignment,
        reviewer_b_assignment=reviewer_b_assignment,
        reviewer_a_labels=reviewer_a_labels,
        reviewer_b_labels=reviewer_b_labels,
        reviewer_a_review_manifest=reviewer_a_review_manifest,
        reviewer_b_review_manifest=reviewer_b_review_manifest,
        reconciliation_manifest=reconciliation_manifest,
    )
    descriptor = receipt["descriptor"]
    if descriptor["protocol"] != INTAKE_PROTOCOL:
        raise ValueError("Remainder consensus validator accepts only remainder intake queues")
    lane, plans, typed_path, review_items_path = _validate_baseline(descriptor=descriptor, bundle_dir=bundle_dir)
    formulas, programs, extra_inputs = _validate_query_program_inputs(
        lane=lane, plans={question_id: plans[question_id] for question_id in descriptor["rows_by_id"]},
        formula_evidence=formula_evidence, query_program=query_program, bundle_dir=bundle_dir,
    )
    documents, v2_path, v3_path = _source._source_documents(bundle_dir)
    rows: list[dict[str, Any]] = []
    for reconciled in receipt["reconciled_rows"]:
        question_id = int(reconciled["question_id"])
        if (
            reconciled.get("protocol") != RECONCILIATION_PROTOCOL
            or reconciled.get("intake_protocol") != INTAKE_PROTOCOL
            or reconciled.get("intake_output_name") != lane
        ):
            raise ValueError(f"Q{question_id}: reconciliation belongs to another remainder lane")
        _validate_lane_blocker(lane=lane, plan=plans[question_id], question_id=question_id)
        if lane == "query_program_shadow_completion":
            formula, program = formulas.get(question_id), programs.get(question_id)
            if formula is None or program is None:
                raise ValueError(f"Q{question_id}: QueryProgram lane lacks formula/program record")
        if reconciled.get("reconciliation_state") != "agreed_accept_non_materializable":
            continue
        proposal = reconciled.get("consensus_proposal")
        coordinates = reconciled.get("consensus_source_coordinates_checked")
        if (
            reconciled.get("consensus_proposal_sha256") != _review.canonical_sha256(proposal)
            or reconciled.get("consensus_source_coordinates_sha256")
            != _review.canonical_sha256({"source_coordinates_checked": coordinates})
        ):
            raise ValueError(f"Q{question_id}: reconciliation consensus hashes are invalid")
        contract = _validate_proposal(lane=lane, proposal=proposal, plan=plans[question_id], question_id=question_id)
        source_validation = _source._validate_coordinates(coordinates=coordinates, documents=documents, question_id=question_id)
        rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "remediation_lane": lane,
                "question_id": question_id,
                "immutable_review_context_sha256": reconciled["immutable_review_context_sha256"],
                "consensus_proposal_sha256": reconciled["consensus_proposal_sha256"],
                "consensus_source_coordinates_sha256": reconciled["consensus_source_coordinates_sha256"],
                "remainder_contract_validation": contract,
                "source_coordinate_validation": source_validation,
                "source_locatable": True,
                "remediation_status": "rebuild_required_not_run",
                "validation_state": LANE_SPECS[lane]["output_state"],
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    _review.write_jsonl(output, rows)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "remediation_lane": lane,
        "materialization_allowed": False,
        "rebuild_allowed": False,
        "inputs": {
            "reconciliation_manifest": {"path": str(reconciliation_manifest), "sha256": receipt["reconciliation_manifest_sha256"]},
            "reconciled_reviews": {"path": str(receipt["reconciled_path"]), "sha256": receipt["reconciliation_sha256"]},
            "typed_plans": {"path": str(typed_path), "sha256": _source.sha256_file(typed_path)},
            "bundle_review_items": {"path": str(review_items_path), "sha256": _source.sha256_file(review_items_path)},
            "structured_tables_v2": {"path": str(v2_path), "sha256": _source.sha256_file(v2_path)},
            "evidence_context_v3": {"path": str(v3_path), "sha256": _source.sha256_file(v3_path)},
            **extra_inputs,
        },
        "outputs": {"validation": {"path": str(output), "sha256": _source.sha256_file(output)}},
        "counts": {
            "reconciled_review_count": len(receipt["reconciled_rows"]),
            "agreed_accept_source_validated_count": len(rows),
            "operator_counts": dict(sorted(Counter(row["remainder_contract_validation"]["operator"] for row in rows).items())),
        },
        "source_contract": SOURCE_CONTRACT,
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--assignment-manifest", type=Path, required=True)
    parser.add_argument("--reviewer-a-assignment", type=Path, required=True)
    parser.add_argument("--reviewer-b-assignment", type=Path, required=True)
    parser.add_argument("--reviewer-a-labels", type=Path, required=True)
    parser.add_argument("--reviewer-b-labels", type=Path, required=True)
    parser.add_argument("--reviewer-a-review-manifest", type=Path, required=True)
    parser.add_argument("--reviewer-b-review-manifest", type=Path, required=True)
    parser.add_argument("--reconciliation-manifest", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path)
    parser.add_argument("--query-program", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(validate(**vars(args))["manifest_path"])


if __name__ == "__main__":
    main()
