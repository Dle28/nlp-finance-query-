#!/usr/bin/env python3
"""Validate an agreed Formula EvidenceSet review without completing evidence.

The validator replays both human reviews and their reconciliation, verifies
that the reviewed Formula EvidenceSet is still partial and hash-bound to the
frozen bundle, and reopens raw source identities through V2/V3.  It cannot
select an operand match, table, row, cell or value and cannot turn a partial
Formula EvidenceSet into a complete one.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_reconcile = _load_module(
    "reconcile_production_release_intake_reviews",
    "reconcile_production_release_intake_reviews.py",
)
_review = _reconcile._review
_source = _load_module("validate_typed_plan_review_consensus", "validate_typed_plan_review_consensus.py")
_queue = _load_module(
    "build_formula_evidence_partial_review_queue",
    "build_formula_evidence_partial_review_queue.py",
)


PROTOCOL = "formula_evidence_independent_review_consensus_validation_v1"
TYPED_RECONCILIATION_PROTOCOL = "production_release_intake_independent_review_reconciliation_v1"
FORMULA_INTAKE_PROTOCOL = "formula_evidence_partial_review_queue_v1"
SOURCE_CONTRACT = _review.SOURCE_CONTRACT
ALLOWED_PROPOSAL_FIELDS = frozenset(
    {
        "formula_id",
        "required_operand_ids",
        "output_unit",
        "formula_definition_disposition",
        "execution_disposition",
    }
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


def _required_operand_ids(formula_row: Mapping[str, Any], question_id: int) -> list[str]:
    formula = formula_row.get("formula")
    if not isinstance(formula, Mapping):
        raise ValueError(f"Q{question_id}: Formula EvidenceSet lacks formula contract")
    operand_ids = [
        str(operand.get("operand_id") or "")
        for operand in formula.get("operands") or []
        if isinstance(operand, Mapping) and operand.get("required", True)
    ]
    if not operand_ids or "" in operand_ids or len(operand_ids) != len(set(operand_ids)):
        raise ValueError(f"Q{question_id}: Formula EvidenceSet has invalid required operands")
    return sorted(operand_ids)


def _expected_context(*, item: Mapping[str, Any], formula_row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question": item.get("question"),
        "question_plan": item.get("question_plan"),
        "formula_contract": _queue._formula_contract(formula_row),
        "evidence_completeness": "partial",
        "reason_codes": sorted(str(value) for value in formula_row.get("reason_codes") or []),
        "missing_operand_ids": sorted(str(value) for value in formula_row.get("missing_operand_ids") or []),
    }


def _validate_baseline(
    *, descriptor: Mapping[str, Any], bundle_dir: Path
) -> tuple[dict[int, dict[str, Any]], Path, Path]:
    lineage = descriptor["lineage"]
    formula_path = Path(lineage["formula_evidence"]["path"])
    formula_manifest_path = Path(lineage["formula_evidence_manifest"]["path"])
    review_items_path = bundle_dir / "review_items.jsonl"
    if _source.sha256_file(review_items_path) != lineage["bundle_review_items"]["sha256"]:
        raise ValueError("Bundle review items do not bind the Formula review intake")
    formula_manifest = _load_json(formula_manifest_path)
    if (
        formula_manifest.get("sidecar_sha256") != _source.sha256_file(formula_path)
        or formula_manifest.get("bundle_review_items_sha256") != _source.sha256_file(review_items_path)
    ):
        raise ValueError("Formula EvidenceSet sidecar does not bind immutable review items")
    items = _index(_load_jsonl(review_items_path), "id", "review items")
    formulas = _index(_load_jsonl(formula_path), "id", "Formula EvidenceSets")
    for question_id, queue_row in descriptor["rows_by_id"].items():
        item, formula = items.get(question_id), formulas.get(question_id)
        if item is None or formula is None or formula.get("evidence_completeness") != "partial":
            raise ValueError(f"Q{question_id}: reviewed Formula EvidenceSet was not originally partial")
        if queue_row.get("formula_id") != ((formula.get("formula") or {}).get("formula_id")):
            raise ValueError(f"Q{question_id}: Formula intake ID does not bind current Formula EvidenceSet")
        context = _expected_context(item=item, formula_row=formula)
        if (
            queue_row.get("review_context") != context
            or queue_row.get("immutable_review_context_sha256") != _review.canonical_sha256(context)
        ):
            raise ValueError(f"Q{question_id}: Formula review context does not bind current partial evidence")
    return formulas, formula_path, review_items_path


def _validate_proposal(
    *, proposal: object, formula_row: Mapping[str, Any], question_id: int
) -> dict[str, Any]:
    if not isinstance(proposal, dict) or set(proposal) != ALLOWED_PROPOSAL_FIELDS:
        raise ValueError(f"Q{question_id}: Formula proposal must use the exact non-materializing contract")
    formula = formula_row.get("formula") or {}
    formula_id = formula.get("formula_id")
    output_unit = formula.get("output_unit")
    required_ids = _required_operand_ids(formula_row, question_id)
    if (
        not isinstance(formula_id, str)
        or not formula_id
        or proposal.get("formula_id") != formula_id
        or proposal.get("required_operand_ids") != required_ids
        or proposal.get("output_unit") != output_unit
        or proposal.get("formula_definition_disposition") != "retain_existing_controlled_formula"
        or proposal.get("execution_disposition") != "require_exact_operand_rebuild"
    ):
        raise ValueError(f"Q{question_id}: Formula proposal changes the frozen formula/evidence contract")
    return {
        "formula_id": formula_id,
        "required_operand_count": len(required_ids),
        "output_unit": output_unit,
        "evidence_status": "partial",
    }


def validate(
    *, bundle_dir: Path, assignment_manifest: Path, reviewer_a_assignment: Path,
    reviewer_b_assignment: Path, reviewer_a_labels: Path, reviewer_b_labels: Path,
    reviewer_a_review_manifest: Path, reviewer_b_review_manifest: Path,
    reconciliation_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    """Write a source-locatable receipt for agreed Formula-review contracts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "formula_evidence_independent_review_consensus_validation_v1.jsonl"
    manifest_path = output_dir / "formula_evidence_independent_review_consensus_validation_v1.manifest.json"
    if output.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite Formula consensus validation: {output_dir}")
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
    if descriptor["protocol"] != FORMULA_INTAKE_PROTOCOL:
        raise ValueError("Formula consensus validator accepts only the partial Formula EvidenceSet intake")
    formulas, formula_path, review_items_path = _validate_baseline(
        descriptor=descriptor, bundle_dir=bundle_dir
    )
    documents, v2_path, v3_path = _source._source_documents(bundle_dir)
    rows: list[dict[str, Any]] = []
    for reconciled in receipt["reconciled_rows"]:
        question_id = int(reconciled["question_id"])
        if (
            reconciled.get("protocol") != TYPED_RECONCILIATION_PROTOCOL
            or reconciled.get("intake_protocol") != FORMULA_INTAKE_PROTOCOL
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
        formula_contract = _validate_proposal(
            proposal=proposal, formula_row=formulas[question_id], question_id=question_id
        )
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
                "formula_contract_validation": formula_contract,
                "source_coordinate_validation": source_validation,
                "source_locatable": True,
                "formula_evidence_status": "partial",
                "validation_state": "formula_contract_source_validated_non_materializable",
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
            "formula_evidence": {"path": str(formula_path), "sha256": _source.sha256_file(formula_path)},
            "bundle_review_items": {"path": str(review_items_path), "sha256": _source.sha256_file(review_items_path)},
            "structured_tables_v2": {"path": str(v2_path), "sha256": _source.sha256_file(v2_path)},
            "evidence_context_v3": {"path": str(v3_path), "sha256": _source.sha256_file(v3_path)},
        },
        "outputs": {"validation": {"path": str(output), "sha256": _source.sha256_file(output)}},
        "counts": {
            "reconciled_review_count": len(receipt["reconciled_rows"]),
            "agreed_accept_source_validated_count": len(rows),
            "formula_counts": dict(sorted(Counter(row["formula_contract_validation"]["formula_id"] for row in rows).items())),
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
