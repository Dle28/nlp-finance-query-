from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finance_query.table_structure import sha256_file


review_spec = importlib.util.spec_from_file_location(
    "verify_production_release_intake_reviews",
    ROOT / "scripts" / "verify_production_release_intake_reviews.py",
)
review = importlib.util.module_from_spec(review_spec)
assert review_spec.loader is not None
review_spec.loader.exec_module(review)
reconcile_spec = importlib.util.spec_from_file_location(
    "reconcile_production_release_intake_reviews",
    ROOT / "scripts" / "reconcile_production_release_intake_reviews.py",
)
reconcile = importlib.util.module_from_spec(reconcile_spec)
assert reconcile_spec.loader is not None
reconcile_spec.loader.exec_module(reconcile)
validator_spec = importlib.util.spec_from_file_location(
    "validate_typed_plan_review_consensus",
    ROOT / "scripts" / "validate_typed_plan_review_consensus.py",
)
mod = importlib.util.module_from_spec(validator_spec)
assert validator_spec.loader is not None
validator_spec.loader.exec_module(mod)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def _proposal() -> dict:
    return {
        "effective_family": "direct_lookup",
        "route": "human_reviewed_typed_operation",
        "entities": ["ABC"],
        "years": [2023],
        "scope": "consolidated",
        "requested_unit": "million_vnd",
        "operands": [
            {
                "operand_id": "x0",
                "role": "value",
                "metric_hints": ["Doanh thu thuần"],
                "entity": "ABC",
                "ticker": "ABC",
                "years": [2023],
                "scope": "consolidated",
                "unit_contract": {
                    "requested_unit": "million_vnd",
                    "source_unit_required": True,
                    "conversion_allowed": False,
                },
                "allowed_table_functions": [],
                "stage_id": None,
                "required": True,
                "grounding_contract": {
                    "exact_internal_table_uid": True,
                    "exact_row_index": True,
                    "exact_column_index": True,
                    "exact_raw_cell": True,
                    "canonical_header_required": True,
                    "adjacent_table_inference_allowed": False,
                },
            }
        ],
        "operation_ast": {"op": "lookup", "args": ["x0"]},
        "formula_id": None,
    }


def _fixture(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "raw" / "ABC_2023.txt"
    source.parent.mkdir(parents=True)
    source.write_text("Original raw report", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    tables = bundle / "tables.jsonl"
    tables.write_text("{}\n", encoding="utf-8")
    item = {"id": 7, "question": "Doanh thu thuần ABC năm 2023 là bao nhiêu?", "question_plan": {}}
    review_items = bundle / "review_items.jsonl"
    _write_jsonl(review_items, [item])
    source_provenance = {
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "char_start": 0,
        "table_sha256": "a" * 64,
    }
    sidecar_row = {
        "internal_table_uid": "u1",
        "document_id": "ABC_2023",
        "page_no": None,
        "source_provenance": source_provenance,
    }
    v2 = bundle / "tables_structured_v2.jsonl"
    v3 = bundle / "tables_evidence_context_v3.jsonl"
    _write_jsonl(v2, [sidecar_row])
    _write_jsonl(v3, [sidecar_row])
    _write_json(
        bundle / "table_structure_v2.manifest.json",
        {
            "structure_version": 2,
            "error_count": 0,
            "input_bundle_tables_sha256": sha256_file(tables),
            "sidecar_sha256": sha256_file(v2),
            "repaired_table_count": 1,
            "table_count": 1,
        },
    )
    _write_json(
        bundle / "table_evidence_context_v3.manifest.json",
        {
            "evidence_context_version": 3,
            "numeric_binding_policy": "one_reliable_raw_v2_number_per_cell",
            "error_count": 0,
            "input_structure_sha256": sha256_file(v2),
            "input_bundle_tables_sha256": sha256_file(tables),
            "sidecar_sha256": sha256_file(v3),
        },
    )
    typed = tmp_path / "typed.jsonl"
    plan = {
        "schema_version": 1,
        "protocol": "typed_operand_decomposition_fail_closed_v1",
        "question_id": 7,
        "question": item["question"],
        "effective_family": "direct_lookup",
        "decomposition_status": "abstain",
        "route": "abstain",
        "reason_codes": ["UNKNOWN_OPERAND_STRUCTURE"],
        "entities": [],
        "years": [],
        "scope": None,
        "requested_unit": None,
        "operands": [],
        "operation_ast": {"op": "abstain"},
        "formula_id": None,
    }
    _write_jsonl(typed, [plan])
    typed_manifest = tmp_path / "typed.manifest.json"
    _write_json(
        typed_manifest,
        {
            "protocol": "typed_operand_decomposition_fail_closed_v1",
            "review_items_sha256": sha256_file(review_items),
            "sidecar_sha256": sha256_file(typed),
        },
    )
    gate = tmp_path / "gate.json"
    _write_json(gate, {"protocol": "production_release_gate_v1", "release_status": "blocked", "production_eligible": False, "submission_compilation_allowed": False, "answer_materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT})
    remediation = tmp_path / "remediation.jsonl"
    _write_jsonl(remediation, [{"question_id": 7}])
    remediation_manifest = tmp_path / "remediation.manifest.json"
    _write_json(remediation_manifest, {"protocol": "production_release_remediation_queue_v1", "queue_status": "non_materializable", "source_contract": review.SOURCE_CONTRACT, "inputs": {"release_gate": _record(gate)}, "outputs": {"queue": _record(remediation)}})
    context = {
        "question": item["question"],
        "question_plan": item["question_plan"],
        "typed_plan_snapshot": {
            "effective_family": plan["effective_family"],
            "route": plan["route"],
            "entities": plan["entities"],
            "years": plan["years"],
            "scope": plan["scope"],
            "requested_unit": plan["requested_unit"],
            "reason_codes": plan["reason_codes"],
        },
    }
    intake_row = {
        "schema_version": 1,
        "protocol": "typed_plan_abstain_review_queue_v1",
        "question_id": 7,
        "immutable_review_context_sha256": review.canonical_sha256(context),
        "review_context": context,
        "review_decision_contract": {
            "decision": None,
            "decision_provenance": None,
            "reviewer_id": None,
            "reviewed_at": None,
            "source_coordinates_checked": None,
            "proposed_typed_plan": None,
            "materialization_allowed": False,
        },
        "materialization_allowed": False,
        "source_contract": review.SOURCE_CONTRACT,
    }
    intake = tmp_path / "intake.jsonl"
    _write_jsonl(intake, [intake_row])
    intake_manifest = tmp_path / "intake.manifest.json"
    _write_json(
        intake_manifest,
        {
            "protocol": "typed_plan_abstain_review_queue_v1",
            "question_count": 1,
            "labels_prepopulated": False,
            "materialization_allowed": False,
            "source_contract": review.SOURCE_CONTRACT,
            "inputs": {
                "bundle_review_items": _record(review_items),
                "typed_plans": _record(typed),
                "typed_plans_manifest": _record(typed_manifest),
                "release_gate": _record(gate),
                "remediation_queue": _record(remediation),
                "remediation_manifest": _record(remediation_manifest),
            },
            "outputs": {"queue": _record(intake)},
        },
    )
    assignments = review.build_assignments(
        intake=intake, intake_manifest=intake_manifest, output_dir=tmp_path / "assignments"
    )
    assignment_manifest = Path(assignments["manifest_path"])
    result: dict[str, Path] = {"bundle_dir": bundle, "assignment_manifest": assignment_manifest}
    coordinates = [{"source_locator": f"{source}#char_start=0", "document_id": "ABC_2023"}]
    for slot, reviewer_id in (("reviewer_a", "human-a"), ("reviewer_b", "human-b")):
        assignment = Path(assignments["outputs"][slot]["assignment"]["path"])
        assignment_row = json.loads(assignment.read_text().splitlines()[0])
        label = review.blank_label_template(assignment_row=assignment_row)
        label.update(
            {
                "decision": "accept",
                "decision_provenance": "human_verified",
                "reviewer_id": reviewer_id,
                "reviewed_at": "2026-08-13T00:00:00Z",
                "source_coordinates_checked": coordinates,
                "source_coordinates_sha256": review.canonical_sha256({"source_coordinates_checked": coordinates}),
                "proposed_typed_plan": _proposal(),
                "notes": "Independently reopened the raw report source.",
                "is_blank_template": False,
            }
        )
        labels = tmp_path / f"{slot}-labels.jsonl"
        _write_jsonl(labels, [label])
        review_manifest = tmp_path / f"{slot}-review.json"
        review.verify(assignment=assignment, assignment_manifest=assignment_manifest, completed_labels=labels, reviewer_slot=slot, reviewer_id=reviewer_id, output=review_manifest)
        result[f"{slot}_assignment"] = assignment
        result[f"{slot}_labels"] = labels
        result[f"{slot}_review_manifest"] = review_manifest
    reconciliation = reconcile.reconcile(
        assignment_manifest=assignment_manifest,
        reviewer_a_assignment=result["reviewer_a_assignment"],
        reviewer_b_assignment=result["reviewer_b_assignment"],
        reviewer_a_labels=result["reviewer_a_labels"],
        reviewer_b_labels=result["reviewer_b_labels"],
        reviewer_a_review_manifest=result["reviewer_a_review_manifest"],
        reviewer_b_review_manifest=result["reviewer_b_review_manifest"],
        output_dir=tmp_path / "reconciliation",
    )
    result["reconciliation_manifest"] = Path(reconciliation["manifest_path"])
    return result


def _kwargs(paths: dict[str, Path]) -> dict[str, Path]:
    return dict(paths)


def test_typed_consensus_validator_reopens_source_and_keeps_plan_abstained(tmp_path: Path) -> None:
    result = mod.validate(**_kwargs(_fixture(tmp_path)), output_dir=tmp_path / "out")
    assert result["counts"] == {
        "reconciled_review_count": 1,
        "agreed_accept_source_validated_count": 1,
        "operator_counts": {"lookup": 1},
        "family_counts": {"direct_lookup": 1},
    }
    row = json.loads((tmp_path / "out" / "typed_plan_independent_review_consensus_validation_v1.jsonl").read_text())
    assert row["validation_state"] == "typed_plan_contract_source_validated_non_materializable"
    assert row["typed_plan_status"] == "abstain"
    assert row["materialization_allowed"] is False
    assert row["source_coordinate_validation"][0]["v2_v3_coordinate_exists"] is True


def test_typed_consensus_validator_rejects_unsafe_ast_or_stale_reconciliation(tmp_path: Path) -> None:
    paths = _fixture(tmp_path / "unsafe")
    rows_path = paths["reconciliation_manifest"].parent / "production_release_intake_reconciled_reviews_v1.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    rows[0]["consensus_proposal"]["operation_ast"] = {"op": "lookup", "args": ["x0", "x1"]}
    _write_jsonl(rows_path, rows)
    manifest = json.loads(paths["reconciliation_manifest"].read_text())
    manifest["outputs"]["reconciled"]["sha256"] = sha256_file(rows_path)
    paths["reconciliation_manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match independently recomputed|hashes are invalid"):
        mod.validate(**_kwargs(paths), output_dir=tmp_path / "unsafe-out")
