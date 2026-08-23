from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finance_query.table_structure import sha256_file


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


review = _module("verify_production_release_intake_reviews", "verify_production_release_intake_reviews.py")
reconcile = _module("reconcile_production_release_intake_reviews", "reconcile_production_release_intake_reviews.py")
queue = _module("build_formula_evidence_partial_review_queue", "build_formula_evidence_partial_review_queue.py")
mod = _module("validate_formula_evidence_review_consensus", "validate_formula_evidence_review_consensus.py")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def _formula() -> dict:
    return {
        "formula_id": "current_ratio",
        "label": "Current ratio",
        "definition_status": "defined",
        "execution_status": "source_binding_required",
        "output_unit": "times",
        "operands": [
            {
                "operand_id": "current_assets",
                "label": "Tài sản ngắn hạn",
                "entity": "ABC",
                "role": "numerator",
                "stage_id": None,
                "years": [2023],
                "required": True,
                "allowed_table_functions": ["balance_sheet"],
                "metric_hints": ["Tài sản ngắn hạn"],
            },
            {
                "operand_id": "current_liabilities",
                "label": "Nợ ngắn hạn",
                "entity": "ABC",
                "role": "denominator",
                "stage_id": None,
                "years": [2023],
                "required": True,
                "allowed_table_functions": ["balance_sheet"],
                "metric_hints": ["Nợ ngắn hạn"],
            },
        ],
    }


def _proposal() -> dict:
    return {
        "formula_id": "current_ratio",
        "required_operand_ids": ["current_assets", "current_liabilities"],
        "output_unit": "times",
        "formula_definition_disposition": "retain_existing_controlled_formula",
        "execution_disposition": "require_exact_operand_rebuild",
    }


def _fixture(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "raw" / "ABC_2023.txt"
    source.parent.mkdir(parents=True)
    source.write_text("Original Formula source", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    raw_tables = bundle / "tables.jsonl"
    raw_tables.write_text("{}\n", encoding="utf-8")
    item = {"id": 7, "question": "Hệ số thanh toán hiện hành ABC năm 2023?", "question_plan": {}}
    review_items = bundle / "review_items.jsonl"
    _write_jsonl(review_items, [item])
    source_provenance = {
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "char_start": 0,
        "table_sha256": "a" * 64,
    }
    sidecar_row = {"internal_table_uid": "u1", "document_id": "ABC_2023", "page_no": None, "source_provenance": source_provenance}
    v2, v3 = bundle / "tables_structured_v2.jsonl", bundle / "tables_evidence_context_v3.jsonl"
    _write_jsonl(v2, [sidecar_row])
    _write_jsonl(v3, [sidecar_row])
    _write_json(bundle / "table_structure_v2.manifest.json", {"structure_version": 2, "error_count": 0, "input_bundle_tables_sha256": sha256_file(raw_tables), "sidecar_sha256": sha256_file(v2), "repaired_table_count": 1, "table_count": 1})
    _write_json(bundle / "table_evidence_context_v3.manifest.json", {"evidence_context_version": 3, "numeric_binding_policy": "one_reliable_raw_v2_number_per_cell", "error_count": 0, "input_structure_sha256": sha256_file(v2), "input_bundle_tables_sha256": sha256_file(raw_tables), "sidecar_sha256": sha256_file(v3)})
    formula_row = {
        "id": 7,
        "question": item["question"],
        "formula": _formula(),
        "operand_matches": {"current_assets": [], "current_liabilities": []},
        "selected_operand_matches": {},
        "evidence_completeness": "partial",
        "reason_codes": ["required_operand_unresolved"],
        "missing_operand_ids": ["current_assets", "current_liabilities"],
    }
    formula_evidence = tmp_path / "formula.jsonl"
    _write_jsonl(formula_evidence, [formula_row])
    formula_manifest = tmp_path / "formula.manifest.json"
    _write_json(formula_manifest, {"sidecar_sha256": sha256_file(formula_evidence), "bundle_review_items_sha256": sha256_file(review_items)})
    gate = tmp_path / "gate.json"
    _write_json(gate, {"protocol": "production_release_gate_v1", "release_status": "blocked", "production_eligible": False, "submission_compilation_allowed": False, "answer_materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT})
    remediation = tmp_path / "remediation.jsonl"
    _write_jsonl(remediation, [{"question_id": 7}])
    remediation_manifest = tmp_path / "remediation.manifest.json"
    _write_json(remediation_manifest, {"protocol": "production_release_remediation_queue_v1", "queue_status": "non_materializable", "source_contract": review.SOURCE_CONTRACT, "inputs": {"release_gate": _record(gate)}, "outputs": {"queue": _record(remediation)}})
    context = {
        "question": item["question"],
        "question_plan": item["question_plan"],
        "formula_contract": queue._formula_contract(formula_row),
        "evidence_completeness": "partial",
        "reason_codes": ["required_operand_unresolved"],
        "missing_operand_ids": ["current_assets", "current_liabilities"],
    }
    intake_row = {
        "schema_version": 1,
        "protocol": "formula_evidence_partial_review_queue_v1",
        "question_id": 7,
        "formula_id": "current_ratio",
        "immutable_review_context_sha256": review.canonical_sha256(context),
        "review_context": context,
        "review_decision_contract": {"decision": None, "decision_provenance": None, "reviewer_id": None, "reviewed_at": None, "source_coordinates_checked": None, "proposed_evidence_contract": None, "materialization_allowed": False},
        "materialization_allowed": False,
        "source_contract": review.SOURCE_CONTRACT,
    }
    intake = tmp_path / "intake.jsonl"
    _write_jsonl(intake, [intake_row])
    intake_manifest = tmp_path / "intake.manifest.json"
    _write_json(intake_manifest, {"protocol": "formula_evidence_partial_review_queue_v1", "question_count": 1, "labels_prepopulated": False, "materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT, "inputs": {"bundle_review_items": _record(review_items), "formula_evidence": _record(formula_evidence), "formula_evidence_manifest": _record(formula_manifest), "release_gate": _record(gate), "remediation_queue": _record(remediation), "remediation_manifest": _record(remediation_manifest)}, "outputs": {"queue": _record(intake)}})
    assignments = review.build_assignments(intake=intake, intake_manifest=intake_manifest, output_dir=tmp_path / "assignments")
    assignment_manifest = Path(assignments["manifest_path"])
    result: dict[str, Path] = {"bundle_dir": bundle, "assignment_manifest": assignment_manifest}
    coordinates = [{"source_locator": f"{source}#char_start=0", "document_id": "ABC_2023"}]
    for slot, reviewer_id in (("reviewer_a", "human-a"), ("reviewer_b", "human-b")):
        assignment = Path(assignments["outputs"][slot]["assignment"]["path"])
        assignment_row = json.loads(assignment.read_text().splitlines()[0])
        label = review.blank_label_template(assignment_row=assignment_row)
        label.update({"decision": "accept", "decision_provenance": "human_verified", "reviewer_id": reviewer_id, "reviewed_at": "2026-08-13T00:00:00Z", "source_coordinates_checked": coordinates, "source_coordinates_sha256": review.canonical_sha256({"source_coordinates_checked": coordinates}), "proposed_evidence_contract": _proposal(), "notes": "Independently reopened the raw Formula source.", "is_blank_template": False})
        labels = tmp_path / f"{slot}-labels.jsonl"
        _write_jsonl(labels, [label])
        review_manifest = tmp_path / f"{slot}-review.json"
        review.verify(assignment=assignment, assignment_manifest=assignment_manifest, completed_labels=labels, reviewer_slot=slot, reviewer_id=reviewer_id, output=review_manifest)
        result[f"{slot}_assignment"] = assignment
        result[f"{slot}_labels"] = labels
        result[f"{slot}_review_manifest"] = review_manifest
    reconciliation = reconcile.reconcile(
        assignment_manifest=assignment_manifest,
        reviewer_a_assignment=result["reviewer_a_assignment"], reviewer_b_assignment=result["reviewer_b_assignment"],
        reviewer_a_labels=result["reviewer_a_labels"], reviewer_b_labels=result["reviewer_b_labels"],
        reviewer_a_review_manifest=result["reviewer_a_review_manifest"], reviewer_b_review_manifest=result["reviewer_b_review_manifest"],
        output_dir=tmp_path / "reconciliation",
    )
    result["reconciliation_manifest"] = Path(reconciliation["manifest_path"])
    return result


def test_formula_consensus_validator_reopens_source_without_completing_evidence(tmp_path: Path) -> None:
    result = mod.validate(**_fixture(tmp_path), output_dir=tmp_path / "out")
    assert result["counts"] == {"reconciled_review_count": 1, "agreed_accept_source_validated_count": 1, "formula_counts": {"current_ratio": 1}}
    row = json.loads((tmp_path / "out" / "formula_evidence_independent_review_consensus_validation_v1.jsonl").read_text())
    assert row["formula_evidence_status"] == "partial"
    assert row["validation_state"] == "formula_contract_source_validated_non_materializable"
    assert row["materialization_allowed"] is False


def test_formula_consensus_validator_rejects_mutated_reconciliation(tmp_path: Path) -> None:
    paths = _fixture(tmp_path / "unsafe")
    reconciled = paths["reconciliation_manifest"].parent / "production_release_intake_reconciled_reviews_v1.jsonl"
    rows = [json.loads(line) for line in reconciled.read_text().splitlines()]
    rows[0]["consensus_proposal"]["output_unit"] = "million_vnd"
    _write_jsonl(reconciled, rows)
    manifest = json.loads(paths["reconciliation_manifest"].read_text())
    manifest["outputs"]["reconciled"]["sha256"] = sha256_file(reconciled)
    paths["reconciliation_manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match independently recomputed|consensus hashes are invalid"):
        mod.validate(**paths, output_dir=tmp_path / "unsafe-out")
