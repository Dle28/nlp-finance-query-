#!/usr/bin/env python3
"""Validate Formula-EvidenceSet materialization review consensus fail-closed.

This only verifies that two human reviewers agreed to request a new exact
Formula EvidenceSet for one frozen complete typed plan and that their source
locator still names raw V2/V3 provenance. It cannot infer a formula, create an
EvidenceSet, select a candidate/cell, execute an AST, or alter release state.
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
_queue = _module("build_formula_evidence_materialization_intake_queue", "build_formula_evidence_materialization_intake_queue.py")


PROTOCOL = "formula_evidence_materialization_consensus_validation_v1"
RECONCILIATION_PROTOCOL = "production_release_intake_independent_review_reconciliation_v1"
INTAKE_PROTOCOL = "formula_evidence_materialization_intake_queue_v1"
TYPED_PLAN_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"
SOURCE_CONTRACT = _review.SOURCE_CONTRACT
ALLOWED_PROPOSAL_FIELDS = frozenset(
    {"plan_fingerprint", "required_operand_ids", "operation_ast", "evidence_set_disposition"}
)


def _load_json(path: Path) -> dict[str, Any]:
    return _review.load_json(path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return _review.load_jsonl(path)


def _index(rows: list[dict[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if type(value) is not int or value in result:
            raise ValueError(f"{label} must contain unique integer {key} values")
        result[value] = row
    return result


def _required_operands(plan: Mapping[str, Any], question_id: int) -> list[str]:
    ids = sorted(
        str(operand.get("operand_id") or "")
        for operand in plan.get("operands") or []
        if isinstance(operand, Mapping) and operand.get("required") is True
    )
    if not ids or "" in ids or len(ids) != len(set(ids)):
        raise ValueError(f"Q{question_id}: complete typed plan has invalid required operands")
    return ids


def _validate_baseline(
    *, descriptor: Mapping[str, Any], bundle_dir: Path
) -> tuple[dict[int, dict[str, Any]], Path, Path]:
    lineage = descriptor["lineage"]
    typed_path = Path(lineage["typed_plans"]["path"])
    typed_manifest_path = Path(lineage["typed_plans_manifest"]["path"])
    review_items_path = bundle_dir / "review_items.jsonl"
    if _source.sha256_file(review_items_path) != lineage["bundle_review_items"]["sha256"]:
        raise ValueError("Bundle review items do not bind Formula materialization intake")
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
        if item is None or plan is None or plan.get("decomposition_status") != "complete":
            raise ValueError(f"Q{question_id}: Formula materialization review was not a complete typed plan")
        context = {
            "question": item.get("question"),
            "question_plan": item.get("question_plan"),
            "typed_plan_contract": _queue.typed_contract(plan),
        }
        if (
            queue_row.get("plan_fingerprint") != plan.get("plan_fingerprint")
            or queue_row.get("review_context") != context
            or queue_row.get("immutable_review_context_sha256") != _review.canonical_sha256(context)
        ):
            raise ValueError(f"Q{question_id}: Formula materialization queue no longer binds typed plan")
        _required_operands(plan, question_id)
    return plans, typed_path, review_items_path


def _validate_proposal(*, proposal: object, plan: Mapping[str, Any], question_id: int) -> dict[str, Any]:
    if not isinstance(proposal, dict) or set(proposal) != ALLOWED_PROPOSAL_FIELDS:
        raise ValueError(f"Q{question_id}: Formula materialization proposal has unsupported fields")
    if (
        proposal.get("plan_fingerprint") != plan.get("plan_fingerprint")
        or proposal.get("required_operand_ids") != _required_operands(plan, question_id)
        or proposal.get("operation_ast") != plan.get("operation_ast")
        or proposal.get("evidence_set_disposition") != "require_new_exact_evidence_set"
    ):
        raise ValueError(f"Q{question_id}: Formula materialization proposal changes frozen typed plan")
    return {
        "plan_fingerprint": str(plan["plan_fingerprint"]),
        "required_operand_count": len(_required_operands(plan, question_id)),
        "operator": str((plan.get("operation_ast") or {}).get("op") or ""),
        "evidence_set_disposition": "require_new_exact_evidence_set",
    }


def validate(
    *, bundle_dir: Path, assignment_manifest: Path, reviewer_a_assignment: Path,
    reviewer_b_assignment: Path, reviewer_a_labels: Path, reviewer_b_labels: Path,
    reviewer_a_review_manifest: Path, reviewer_b_review_manifest: Path,
    reconciliation_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    """Write a source-locatable request for a future exact EvidenceSet rebuild."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "formula_evidence_materialization_consensus_validation_v1.jsonl"
    manifest_path = output_dir / "formula_evidence_materialization_consensus_validation_v1.manifest.json"
    if output.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite Formula materialization consensus validation: {output_dir}")
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
        raise ValueError("Formula materialization validator accepts only its immutable intake")
    plans, typed_path, review_items_path = _validate_baseline(descriptor=descriptor, bundle_dir=bundle_dir)
    documents, v2_path, v3_path = _source._source_documents(bundle_dir)
    rows: list[dict[str, Any]] = []
    for reconciled in receipt["reconciled_rows"]:
        question_id = int(reconciled["question_id"])
        if (
            reconciled.get("protocol") != RECONCILIATION_PROTOCOL
            or reconciled.get("intake_protocol") != INTAKE_PROTOCOL
        ):
            raise ValueError(f"Q{question_id}: reconciliation belongs to another intake")
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
        contract = _validate_proposal(proposal=proposal, plan=plans[question_id], question_id=question_id)
        source_validation = _source._validate_coordinates(
            coordinates=coordinates, documents=documents, question_id=question_id
        )
        rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "immutable_review_context_sha256": reconciled["immutable_review_context_sha256"],
                "consensus_proposal_sha256": reconciled["consensus_proposal_sha256"],
                "consensus_source_coordinates_sha256": reconciled["consensus_source_coordinates_sha256"],
                "formula_evidence_materialization_contract": contract,
                "source_coordinate_validation": source_validation,
                "source_locatable": True,
                "formula_evidence_status": "not_materialized",
                "validation_state": "formula_materialization_contract_source_validated_non_materializable",
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    _review.write_jsonl(output, rows)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "materialization_allowed": False,
        "formula_evidence_rebuild_allowed": False,
        "inputs": {
            "reconciliation_manifest": {"path": str(reconciliation_manifest), "sha256": receipt["reconciliation_manifest_sha256"]},
            "reconciled_reviews": {"path": str(receipt["reconciled_path"]), "sha256": receipt["reconciliation_sha256"]},
            "typed_plans": {"path": str(typed_path), "sha256": _source.sha256_file(typed_path)},
            "bundle_review_items": {"path": str(review_items_path), "sha256": _source.sha256_file(review_items_path)},
            "structured_tables_v2": {"path": str(v2_path), "sha256": _source.sha256_file(v2_path)},
            "evidence_context_v3": {"path": str(v3_path), "sha256": _source.sha256_file(v3_path)},
        },
        "outputs": {"validation": {"path": str(output), "sha256": _source.sha256_file(output)}},
        "counts": {
            "reconciled_review_count": len(receipt["reconciled_rows"]),
            "agreed_accept_source_validated_count": len(rows),
            "operator_counts": dict(sorted(Counter(row["formula_evidence_materialization_contract"]["operator"] for row in rows).items())),
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
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(validate(**vars(args))["manifest_path"])


if __name__ == "__main__":
    main()
