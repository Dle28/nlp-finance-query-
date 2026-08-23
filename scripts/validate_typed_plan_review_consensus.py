#!/usr/bin/env python3
"""Validate an agreed typed-plan review against raw-source identity.

This is intentionally a receipt layer.  It accepts only a revalidated,
two-review consensus for a previously abstained typed plan, reopens the named
raw source identity through V2/V3, and checks a compact, deterministic typed
operation schema.  It never writes a typed-plan override, table/cell binding,
Formula EvidenceSet, execution, answer, label, or release decision.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.execution import OPERATOR_REGISTRY, validate_operation_ast  # noqa: E402
from finance_query.table_structure import sha256_file, validate_structure_sidecar  # noqa: E402


_spec = importlib.util.spec_from_file_location(
    "reconcile_production_release_intake_reviews",
    ROOT / "scripts" / "reconcile_production_release_intake_reviews.py",
)
_reconcile = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_reconcile)
_review = _reconcile._review


PROTOCOL = "typed_plan_independent_review_consensus_validation_v1"
RECONCILIATION_PROTOCOL = "production_release_intake_independent_review_reconciliation_v1"
TYPED_INTAKE_PROTOCOL = "typed_plan_abstain_review_queue_v1"
TYPED_PLAN_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"
SOURCE_CONTRACT = _review.SOURCE_CONTRACT
ALLOWED_PROPOSAL_FIELDS = frozenset(
    {
        "effective_family",
        "route",
        "entities",
        "years",
        "scope",
        "requested_unit",
        "operands",
        "operation_ast",
        "formula_id",
    }
)
ALLOWED_OPERAND_FIELDS = frozenset(
    {
        "operand_id",
        "role",
        "metric_hints",
        "entity",
        "ticker",
        "years",
        "scope",
        "unit_contract",
        "allowed_table_functions",
        "stage_id",
        "required",
        "grounding_contract",
    }
)
GROUNDING_CONTRACT = {
    "exact_internal_table_uid": True,
    "exact_row_index": True,
    "exact_column_index": True,
    "exact_raw_cell": True,
    "canonical_header_required": True,
    "adjacent_table_inference_allowed": False,
}
UNIT_CONTRACT_KEYS = frozenset(
    {"requested_unit", "source_unit_required", "conversion_allowed"}
)
ALLOWED_FAMILIES = frozenset(
    {
        "direct_lookup",
        "cross_entity_comparison",
        "multi_entity_or_period_aggregation",
        "ratio_or_derived",
        "temporal_change",
    }
)
ALLOWED_UNITS = frozenset(
    {None, "billion_vnd", "million_vnd", "thousand_vnd", "percent", "times"}
)
ALLOWED_COORDINATE_FIELDS = frozenset(
    {"source_locator", "document_id", "page_no", "section", "notes"}
)
TICKER_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}\Z")


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


def _fragment(locator: str, question_id: int, index: int) -> tuple[str, dict[str, int]]:
    source_path, marker, raw_fragment = locator.partition("#")
    if not marker or not source_path.strip() or not raw_fragment:
        raise ValueError(f"Q{question_id}: source coordinate {index} needs exact source path and locator fragment")
    attributes: dict[str, int] = {}
    for part in raw_fragment.split("&"):
        key, equals, value = part.partition("=")
        if not equals or key not in {"page", "char_start"} or not value.isdecimal():
            raise ValueError(f"Q{question_id}: source coordinate {index} has unsupported locator fragment")
        number = int(value)
        if key in attributes or (key == "page" and number < 1) or (key == "char_start" and number < 0):
            raise ValueError(f"Q{question_id}: source coordinate {index} has invalid locator fragment")
        attributes[key] = number
    if not attributes:
        raise ValueError(f"Q{question_id}: source coordinate {index} must locate a page or source character offset")
    return source_path, attributes


def _source_documents(bundle_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], Path, Path]:
    v2_path = bundle_dir / "tables_structured_v2.jsonl"
    v3_path = bundle_dir / "tables_evidence_context_v3.jsonl"
    validate_structure_sidecar(bundle_dir, v2_path)
    validate_evidence_context_sidecar(bundle_dir, v2_path, v3_path)
    v2_rows = _load_jsonl(v2_path)
    v3_by_uid = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(v3_path)}
    if not v2_rows or "" in v3_by_uid or len(v3_by_uid) != len(v2_rows):
        raise ValueError("V2/V3 source sidecars have incomplete table UID coverage")
    documents: dict[str, list[dict[str, Any]]] = {}
    for row in v2_rows:
        uid = str(row.get("internal_table_uid") or "")
        document_id = str(row.get("document_id") or "")
        provenance = row.get("source_provenance") or {}
        v3 = v3_by_uid.get(uid) or {}
        v3_provenance = v3.get("source_provenance") or {}
        if (
            not uid
            or not document_id
            or not isinstance(provenance.get("source_path"), str)
            or not provenance["source_path"]
            or not isinstance(provenance.get("source_sha256"), str)
            or len(provenance["source_sha256"]) != 64
            or v3.get("document_id") != document_id
            or v3_provenance.get("source_path") != provenance["source_path"]
            or v3_provenance.get("source_sha256") != provenance["source_sha256"]
        ):
            raise ValueError("V2/V3 source sidecars have invalid source provenance")
        documents.setdefault(document_id, []).append(row)
    return documents, v2_path, v3_path


def _validate_coordinates(
    *, coordinates: object, documents: Mapping[str, list[dict[str, Any]]], question_id: int
) -> list[dict[str, Any]]:
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError(f"Q{question_id}: accepted consensus lacks source coordinates")
    checked: list[dict[str, Any]] = []
    for index, coordinate in enumerate(coordinates):
        if not isinstance(coordinate, dict) or set(coordinate).difference(ALLOWED_COORDINATE_FIELDS):
            raise ValueError(f"Q{question_id}: source coordinate {index} has unsupported fields")
        document_id = coordinate.get("document_id")
        locator = coordinate.get("source_locator")
        if not isinstance(document_id, str) or not document_id.strip() or not isinstance(locator, str):
            raise ValueError(f"Q{question_id}: source coordinate {index} requires document_id and source_locator")
        source_path, fragment = _fragment(locator, question_id, index)
        coordinate_page = coordinate.get("page_no")
        if coordinate_page is not None and (type(coordinate_page) is not int or coordinate_page < 1):
            raise ValueError(f"Q{question_id}: source coordinate {index} has invalid page_no")
        if "page" in fragment and coordinate_page is not None and fragment["page"] != coordinate_page:
            raise ValueError(f"Q{question_id}: source coordinate {index} has a page mismatch")
        expected_page = fragment.get("page", coordinate_page)
        document_rows = documents.get(document_id)
        if not document_rows:
            raise ValueError(f"Q{question_id}: source coordinate {index} document_id is absent from V2/V3")
        candidates = [
            row
            for row in document_rows
            if (row.get("source_provenance") or {}).get("source_path") == source_path
            and (expected_page is None or row.get("page_no") == expected_page)
            and (
                "char_start" not in fragment
                or (row.get("source_provenance") or {}).get("char_start") == fragment["char_start"]
            )
        ]
        if not candidates:
            raise ValueError(f"Q{question_id}: source coordinate {index} is absent from V2/V3 source provenance")
        source_shas = {str((row.get("source_provenance") or {}).get("source_sha256") or "") for row in candidates}
        if len(source_shas) != 1 or "" in source_shas:
            raise ValueError(f"Q{question_id}: source coordinate {index} has ambiguous raw-source hash")
        source_file = Path(source_path)
        if not source_file.is_file() or sha256_file(source_file) != next(iter(source_shas)):
            raise ValueError(f"Q{question_id}: source coordinate {index} raw source is missing or hash-mismatched")
        checked.append(
            {
                "document_id": document_id,
                "source_locator": locator,
                "source_sha256": next(iter(source_shas)),
                "source_table_count_at_locator": len(candidates),
                "v2_v3_coordinate_exists": True,
            }
        )
    return checked


def _ast_references(ast: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for argument in value.get("args") or []:
                visit(argument)
        elif isinstance(value, list):
            for argument in value:
                visit(argument)
        elif isinstance(value, str):
            names.add(value)

    visit(ast)
    return names


def _validate_proposal(proposal: object, question_id: int) -> dict[str, Any]:
    if not isinstance(proposal, dict) or set(proposal) != ALLOWED_PROPOSAL_FIELDS:
        raise ValueError(f"Q{question_id}: typed-plan proposal must use the exact V1 contract fields")
    if proposal.get("effective_family") not in ALLOWED_FAMILIES:
        raise ValueError(f"Q{question_id}: typed-plan proposal has unsupported family")
    if proposal.get("route") != "human_reviewed_typed_operation":
        raise ValueError(f"Q{question_id}: typed-plan proposal has unsupported route")
    entities = proposal.get("entities")
    years = proposal.get("years")
    scope = proposal.get("scope")
    requested_unit = proposal.get("requested_unit")
    if (
        not isinstance(entities, list)
        or not entities
        or len(entities) != len(set(entities))
        or any(not isinstance(value, str) or not TICKER_RE.fullmatch(value) for value in entities)
        or not isinstance(years, list)
        or not years
        or len(years) != len(set(years))
        or any(type(value) is not int or value < 1900 or value > 2100 for value in years)
        or scope not in {None, "separate", "consolidated"}
        or requested_unit not in ALLOWED_UNITS
        or proposal.get("formula_id") is not None
    ):
        raise ValueError(f"Q{question_id}: typed-plan proposal lacks a safe entity/period/unit contract")
    operands = proposal.get("operands")
    if not isinstance(operands, list) or not operands:
        raise ValueError(f"Q{question_id}: typed-plan proposal needs typed operands")
    operand_ids: set[str] = set()
    roles: set[str] = set()
    for operand in operands:
        if not isinstance(operand, dict) or set(operand) != ALLOWED_OPERAND_FIELDS:
            raise ValueError(f"Q{question_id}: typed operand violates the exact contract")
        operand_id, role = operand.get("operand_id"), operand.get("role")
        hints, operand_years = operand.get("metric_hints"), operand.get("years")
        if (
            not isinstance(operand_id, str)
            or not operand_id.strip()
            or operand_id in operand_ids
            or not isinstance(role, str)
            or not role.strip()
            or role in roles
            or not isinstance(hints, list)
            or not hints
            or any(not isinstance(hint, str) or not hint.strip() for hint in hints)
            or operand.get("entity") not in entities
            or operand.get("ticker") != operand.get("entity")
            or not isinstance(operand_years, list)
            or not operand_years
            or any(type(year) is not int or year not in years for year in operand_years)
            or operand.get("scope") != scope
            or operand.get("stage_id") is not None
            or operand.get("required") is not True
            or operand.get("grounding_contract") != GROUNDING_CONTRACT
        ):
            raise ValueError(f"Q{question_id}: typed operand lacks an exact grounded contract")
        unit_contract = operand.get("unit_contract")
        if (
            not isinstance(unit_contract, dict)
            or set(unit_contract) != UNIT_CONTRACT_KEYS
            or unit_contract.get("requested_unit") != requested_unit
            or unit_contract.get("source_unit_required") is not True
            or unit_contract.get("conversion_allowed") is not False
        ):
            raise ValueError(f"Q{question_id}: typed operand has unsafe unit conversion")
        functions = operand.get("allowed_table_functions")
        if not isinstance(functions, list) or any(not isinstance(value, str) or not value.strip() for value in functions):
            raise ValueError(f"Q{question_id}: typed operand has invalid allowed table functions")
        operand_ids.add(operand_id)
        roles.add(role)
    ast = proposal.get("operation_ast")
    if not isinstance(ast, Mapping) or validate_operation_ast(ast):
        raise ValueError(f"Q{question_id}: operation AST is non-deterministic or not shadow eligible")
    root = OPERATOR_REGISTRY.get(str(ast.get("op") or ""))
    if root is None or not root.shadow_eligible or _ast_references(ast) != operand_ids:
        raise ValueError(f"Q{question_id}: operation AST does not bind every typed operand exactly")
    return {
        "effective_family": str(proposal["effective_family"]),
        "operator": root.name,
        "operand_count": len(operands),
        "operand_ids": sorted(operand_ids),
    }


def _validate_baseline(
    *, descriptor: Mapping[str, Any], bundle_dir: Path
) -> tuple[dict[int, dict[str, Any]], Path, Path]:
    lineage = descriptor["lineage"]
    typed_path = Path(lineage["typed_plans"]["path"])
    typed_manifest_path = Path(lineage["typed_plans_manifest"]["path"])
    review_items_path = bundle_dir / "review_items.jsonl"
    if sha256_file(review_items_path) != lineage["bundle_review_items"]["sha256"]:
        raise ValueError("Bundle review items do not bind the reviewed intake")
    typed_manifest = _review.load_json(typed_manifest_path)
    if (
        typed_manifest.get("protocol") != TYPED_PLAN_PROTOCOL
        or typed_manifest.get("sidecar_sha256") != sha256_file(typed_path)
        or typed_manifest.get("review_items_sha256") != sha256_file(review_items_path)
    ):
        raise ValueError("Typed-plan sidecar does not bind its immutable review items")
    items = _index(_load_jsonl(review_items_path), "id", "review items")
    plans = _index(_load_jsonl(typed_path), "question_id", "typed plans")
    if set(items) != set(plans):
        raise ValueError("Typed-plan sidecar coverage differs from review items")
    for question_id, queue_row in descriptor["rows_by_id"].items():
        plan, item = plans.get(question_id), items.get(question_id)
        if plan is None or item is None or plan.get("decomposition_status") != "abstain":
            raise ValueError(f"Q{question_id}: reviewed consensus was not an original typed-plan abstention")
        context = queue_row.get("review_context") or {}
        snapshot = context.get("typed_plan_snapshot") or {}
        if (
            context.get("question") != item.get("question")
            or any(plan.get(key) != snapshot.get(key) for key in ("effective_family", "route", "entities", "years", "scope", "requested_unit"))
            or sorted(str(value) for value in plan.get("reason_codes") or []) != snapshot.get("reason_codes")
        ):
            raise ValueError(f"Q{question_id}: reviewed typed-plan snapshot no longer binds its original abstention")
    return plans, typed_path, review_items_path


def validate(
    *, bundle_dir: Path, assignment_manifest: Path, reviewer_a_assignment: Path,
    reviewer_b_assignment: Path, reviewer_a_labels: Path, reviewer_b_labels: Path,
    reviewer_a_review_manifest: Path, reviewer_b_review_manifest: Path,
    reconciliation_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    """Write a source-locatable, schema-validated receipt for agreed accepts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "typed_plan_independent_review_consensus_validation_v1.jsonl"
    manifest_path = output_dir / "typed_plan_independent_review_consensus_validation_v1.manifest.json"
    if output.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite typed-plan consensus validation: {output_dir}")
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
    if descriptor["protocol"] != TYPED_INTAKE_PROTOCOL:
        raise ValueError("Typed-plan consensus validator accepts only the typed-plan abstain intake")
    _plans, typed_path, review_items_path = _validate_baseline(descriptor=descriptor, bundle_dir=bundle_dir)
    documents, v2_path, v3_path = _source_documents(bundle_dir)
    rows: list[dict[str, Any]] = []
    for row in receipt["reconciled_rows"]:
        question_id = int(row["question_id"])
        if row.get("protocol") != RECONCILIATION_PROTOCOL or row.get("intake_protocol") != TYPED_INTAKE_PROTOCOL:
            raise ValueError(f"Q{question_id}: reconciliation belongs to another intake")
        if row.get("reconciliation_state") != "agreed_accept_non_materializable":
            continue
        proposal = row.get("consensus_proposal")
        coordinates = row.get("consensus_source_coordinates_checked")
        if (
            row.get("consensus_proposal_sha256") != _review.canonical_sha256(proposal)
            or row.get("consensus_source_coordinates_sha256")
            != _review.canonical_sha256({"source_coordinates_checked": coordinates})
        ):
            raise ValueError(f"Q{question_id}: reconciliation consensus hashes are invalid")
        contract = _validate_proposal(proposal, question_id)
        source_validation = _validate_coordinates(
            coordinates=coordinates, documents=documents, question_id=question_id
        )
        rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "immutable_review_context_sha256": row["immutable_review_context_sha256"],
                "consensus_proposal_sha256": row["consensus_proposal_sha256"],
                "consensus_source_coordinates_sha256": row["consensus_source_coordinates_sha256"],
                "typed_plan_contract_validation": contract,
                "source_coordinate_validation": source_validation,
                "source_locatable": True,
                "validation_state": "typed_plan_contract_source_validated_non_materializable",
                "typed_plan_status": "abstain",
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    _review.write_jsonl(output, rows)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "materialization_allowed": False,
        "typed_plan_rebuild_allowed": False,
        "inputs": {
            "reconciliation_manifest": {"path": str(reconciliation_manifest), "sha256": receipt["reconciliation_manifest_sha256"]},
            "reconciled_reviews": {"path": str(receipt["reconciled_path"]), "sha256": receipt["reconciliation_sha256"]},
            "typed_plans": {"path": str(typed_path), "sha256": sha256_file(typed_path)},
            "bundle_review_items": {"path": str(review_items_path), "sha256": sha256_file(review_items_path)},
            "structured_tables_v2": {"path": str(v2_path), "sha256": sha256_file(v2_path)},
            "evidence_context_v3": {"path": str(v3_path), "sha256": sha256_file(v3_path)},
        },
        "outputs": {"validation": {"path": str(output), "sha256": sha256_file(output)}},
        "counts": {
            "reconciled_review_count": len(receipt["reconciled_rows"]),
            "agreed_accept_source_validated_count": len(rows),
            "operator_counts": dict(sorted(Counter(row["typed_plan_contract_validation"]["operator"] for row in rows).items())),
            "family_counts": dict(sorted(Counter(row["typed_plan_contract_validation"]["effective_family"] for row in rows).items())),
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
