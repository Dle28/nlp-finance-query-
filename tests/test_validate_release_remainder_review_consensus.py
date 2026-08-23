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
queue = _module("build_release_remainder_intake_queues", "build_release_remainder_intake_queues.py")
mod = _module("validate_release_remainder_review_consensus", "validate_release_remainder_review_consensus.py")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def _plan(question: str, lane: str) -> dict:
    if lane == "deterministic_executor_compile":
        status, family, formula_id, ast = "typed_non_executable", "conditional_analytical", None, {
            "op": "staged_program", "formula_id": "controlled_stage", "stages": []
        }
    elif lane == "exact_source_conflict_adjudication":
        status, family, formula_id, ast = "complete", "direct_lookup", None, {"op": "lookup", "args": ["x0"]}
    else:
        status, family, formula_id, ast = "complete", "ratio_or_derived", "net_finance_result", {
            "op": "subtract", "args": ["x0", "x1"]
        }
    operands = []
    for index, operand_id in enumerate(("x0",) if lane == "exact_source_conflict_adjudication" else ("x0", "x1")):
        operands.append(
            {
                "operand_id": operand_id,
                "entity": "ABC",
                "ticker": "ABC",
                "role": operand_id,
                "stage_id": None,
                "scope": "consolidated",
                "years": [2023 - index],
                "required": True,
                "metric_hints": ["Doanh thu thuần"],
                "allowed_table_functions": ["income_statement"],
                "grounding_contract": {
                    "exact_internal_table_uid": True,
                    "exact_row_index": True,
                    "exact_column_index": True,
                    "exact_raw_cell": True,
                    "canonical_header_required": True,
                    "adjacent_table_inference_allowed": False,
                },
                "unit_contract": {
                    "requested_unit": "million_vnd",
                    "source_unit_required": True,
                    "conversion_allowed": False,
                },
            }
        )
    return {
        "schema_version": 1,
        "protocol": "typed_operand_decomposition_fail_closed_v1",
        "question_id": 7,
        "question": question,
        "effective_family": family,
        "decomposition_status": status,
        "route": "controlled_formula_template" if formula_id else "existing_typed_plan",
        "reason_codes": ["EXECUTOR_COMPILATION_REQUIRED"] if status != "complete" else [],
        "entities": ["ABC"],
        "years": [2022, 2023],
        "scope": "consolidated",
        "requested_unit": "million_vnd",
        "operands": operands,
        "operation_ast": ast,
        "formula_id": formula_id,
        "plan_fingerprint": {"deterministic_executor_compile": "a", "exact_source_conflict_adjudication": "b", "query_program_shadow_completion": "c"}[lane] * 64,
    }


def _proposal(plan: dict, lane: str) -> dict:
    ids = sorted(operand["operand_id"] for operand in plan["operands"])
    if lane == "deterministic_executor_compile":
        return {
            "plan_fingerprint": plan["plan_fingerprint"],
            "required_operand_ids": ids,
            "operation_ast": plan["operation_ast"],
            "executor_disposition": "require_new_deterministic_executor_after_exact_evidence",
        }
    if lane == "exact_source_conflict_adjudication":
        return {
            "plan_fingerprint": plan["plan_fingerprint"],
            "required_operand_ids": ids,
            "operator": "lookup",
            "conflict_disposition": "retain_conflict_pending_fresh_exact_replay",
        }
    return {
        "plan_fingerprint": plan["plan_fingerprint"],
        "formula_id": plan["formula_id"],
        "required_operand_ids": ids,
        "operation_ast": plan["operation_ast"],
        "program_disposition": "require_new_source_bound_shadow_program_rebuild",
    }


def _fixture(tmp_path: Path, lane: str) -> dict[str, Path]:
    source = tmp_path / "raw" / "ABC.txt"
    source.parent.mkdir(parents=True)
    source.write_text("Immutable raw source", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    tables = bundle / "tables.jsonl"
    tables.write_text("{}\n", encoding="utf-8")
    item = {"id": 7, "question": f"Question for {lane}", "question_plan": {"family": "direct_lookup"}}
    review_items = bundle / "review_items.jsonl"
    _write_jsonl(review_items, [item])
    provenance = {"source_path": str(source), "source_sha256": sha256_file(source), "char_start": 0, "table_sha256": "a" * 64}
    sidecar = {"internal_table_uid": "u1", "document_id": "ABC_2023", "page_no": None, "source_provenance": provenance}
    v2, v3 = bundle / "tables_structured_v2.jsonl", bundle / "tables_evidence_context_v3.jsonl"
    _write_jsonl(v2, [sidecar]); _write_jsonl(v3, [sidecar])
    _write_json(bundle / "table_structure_v2.manifest.json", {"structure_version": 2, "error_count": 0, "input_bundle_tables_sha256": sha256_file(tables), "sidecar_sha256": sha256_file(v2), "repaired_table_count": 1, "table_count": 1})
    _write_json(bundle / "table_evidence_context_v3.manifest.json", {"evidence_context_version": 3, "numeric_binding_policy": "one_reliable_raw_v2_number_per_cell", "error_count": 0, "input_structure_sha256": sha256_file(v2), "input_bundle_tables_sha256": sha256_file(tables), "sidecar_sha256": sha256_file(v3)})
    plan = _plan(item["question"], lane)
    typed = tmp_path / "typed.jsonl"; _write_jsonl(typed, [plan])
    typed_manifest = tmp_path / "typed.manifest.json"; _write_json(typed_manifest, {"protocol": "typed_operand_decomposition_fail_closed_v1", "sidecar_sha256": sha256_file(typed), "review_items_sha256": sha256_file(review_items)})
    gate = tmp_path / "gate.json"; _write_json(gate, {"protocol": "production_release_gate_v1", "release_status": "blocked", "production_eligible": False, "submission_compilation_allowed": False, "answer_materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT})
    remediation = tmp_path / "remediation.jsonl"; _write_jsonl(remediation, [{"question_id": 7}])
    remediation_manifest = tmp_path / "remediation.manifest.json"; _write_json(remediation_manifest, {"protocol": "production_release_remediation_queue_v1", "queue_status": "non_materializable", "source_contract": review.SOURCE_CONTRACT, "inputs": {"release_gate": _record(gate)}, "outputs": {"queue": _record(remediation)}})
    kind = mod.LANE_SPECS[lane]["intake_kind"]
    context = {"question": item["question"], "question_plan": item["question_plan"], "typed_plan_contract": queue.typed_contract(plan), "remediation_lane": lane}
    intake_row = {"schema_version": 1, "protocol": "production_release_remainder_intakes_v1", "intake_kind": kind, "question_id": 7, "plan_fingerprint": plan["plan_fingerprint"], "immutable_review_context_sha256": review.canonical_sha256(context), "review_context": context, "review_decision_contract": {"decision": None, "decision_provenance": None, "reviewer_id": None, "reviewed_at": None, "source_coordinates_checked": None, "proposed_contract": None, "materialization_allowed": False}, "materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT}
    intake = tmp_path / "intake.jsonl"; _write_jsonl(intake, [intake_row])
    intake_manifest = tmp_path / "intake.manifest.json"
    _write_json(intake_manifest, {"protocol": "production_release_remainder_intakes_v1", "lane_counts": {lane: 1}, "labels_prepopulated": False, "materialization_allowed": False, "source_contract": review.SOURCE_CONTRACT, "inputs": {"bundle_review_items": _record(review_items), "typed_plans": _record(typed), "typed_plans_manifest": _record(typed_manifest), "release_gate": _record(gate), "remediation_queue": _record(remediation), "remediation_manifest": _record(remediation_manifest)}, "outputs": {lane: _record(intake)}})
    assignments = review.build_assignments(intake=intake, intake_manifest=intake_manifest, output_dir=tmp_path / "assignments")
    assignment_manifest = Path(assignments["manifest_path"])
    result: dict[str, Path] = {"bundle_dir": bundle, "assignment_manifest": assignment_manifest}
    coordinates = [{"source_locator": f"{source}#char_start=0", "document_id": "ABC_2023"}]
    for slot, reviewer_id in (("reviewer_a", "human-a"), ("reviewer_b", "human-b")):
        assignment = Path(assignments["outputs"][slot]["assignment"]["path"])
        assignment_row = json.loads(assignment.read_text().splitlines()[0])
        label = review.blank_label_template(assignment_row=assignment_row)
        label.update({"decision": "accept", "decision_provenance": "human_verified", "reviewer_id": reviewer_id, "reviewed_at": "2026-08-13T00:00:00Z", "source_coordinates_checked": coordinates, "source_coordinates_sha256": review.canonical_sha256({"source_coordinates_checked": coordinates}), "proposed_contract": _proposal(plan, lane), "notes": "Independently reopened raw source; rebuild remains required.", "is_blank_template": False})
        labels = tmp_path / f"{slot}-labels.jsonl"; _write_jsonl(labels, [label])
        review_manifest = tmp_path / f"{slot}-review.json"; review.verify(assignment=assignment, assignment_manifest=assignment_manifest, completed_labels=labels, reviewer_slot=slot, reviewer_id=reviewer_id, output=review_manifest)
        result[f"{slot}_assignment"] = assignment; result[f"{slot}_labels"] = labels; result[f"{slot}_review_manifest"] = review_manifest
    reconciliation = reconcile.reconcile(assignment_manifest=assignment_manifest, reviewer_a_assignment=result["reviewer_a_assignment"], reviewer_b_assignment=result["reviewer_b_assignment"], reviewer_a_labels=result["reviewer_a_labels"], reviewer_b_labels=result["reviewer_b_labels"], reviewer_a_review_manifest=result["reviewer_a_review_manifest"], reviewer_b_review_manifest=result["reviewer_b_review_manifest"], output_dir=tmp_path / "reconciliation")
    result["reconciliation_manifest"] = Path(reconciliation["manifest_path"])
    if lane == "query_program_shadow_completion":
        formula = tmp_path / "formula.jsonl"
        _write_jsonl(formula, [{"id": 7, "formula": {"formula_id": plan["formula_id"]}, "evidence_completeness": "complete"}])
        _write_json(formula.with_suffix(".manifest.json"), {"sidecar_sha256": sha256_file(formula), "bundle_review_items_sha256": sha256_file(review_items)})
        program = tmp_path / "program.jsonl"
        _write_jsonl(program, [{"id": 7, "shadow_execution": None}])
        _write_json(program.with_suffix(".manifest.json"), {"formula_evidence_sha256": sha256_file(formula), "sidecar_sha256": sha256_file(program), "submission_eligible": False})
        result["formula_evidence"] = formula; result["query_program"] = program
    return result


@pytest.mark.parametrize(
    ("lane", "operator", "state"),
    [
        ("deterministic_executor_compile", "staged_program", "executor_contract_source_validated_non_materializable"),
        ("exact_source_conflict_adjudication", "lookup", "conflict_contract_source_validated_non_materializable"),
        ("query_program_shadow_completion", "subtract", "query_program_contract_source_validated_non_materializable"),
    ],
)
def test_remainder_consensus_validator_keeps_each_lane_nonmaterializing(tmp_path: Path, lane: str, operator: str, state: str) -> None:
    result = mod.validate(**_fixture(tmp_path, lane), output_dir=tmp_path / "out")
    assert result["remediation_lane"] == lane
    assert result["counts"] == {"reconciled_review_count": 1, "agreed_accept_source_validated_count": 1, "operator_counts": {operator: 1}}
    row = json.loads((tmp_path / "out" / "production_release_remainder_independent_review_consensus_validation_v1.jsonl").read_text())
    assert row["validation_state"] == state
    assert row["remediation_status"] == "rebuild_required_not_run"
    assert row["materialization_allowed"] is False


def test_remainder_consensus_validator_rejects_mutated_reconciliation(tmp_path: Path) -> None:
    paths = _fixture(tmp_path / "unsafe", "exact_source_conflict_adjudication")
    reconciled = paths["reconciliation_manifest"].parent / "production_release_intake_reconciled_reviews_v1.jsonl"
    rows = [json.loads(line) for line in reconciled.read_text().splitlines()]
    rows[0]["consensus_proposal"]["conflict_disposition"] = "select_highest_confidence_candidate"
    _write_jsonl(reconciled, rows)
    manifest = json.loads(paths["reconciliation_manifest"].read_text()); manifest["outputs"]["reconciled"]["sha256"] = sha256_file(reconciled)
    paths["reconciliation_manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match independently recomputed|consensus hashes are invalid"):
        mod.validate(**paths, output_dir=tmp_path / "unsafe-out")
