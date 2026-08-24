"""Portable regression coverage for the V13 evidence-closure workbench."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.evidence_closure import build_workbench, load_jsonl, sha256_file


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _requirements(question_id: int) -> dict[str, object]:
    return {
        "question_id": question_id,
        "claim": f"Claim Q{question_id}",
        "claim_requirement_set_id": f"requirements-{question_id}",
        "requirements": [
            {"dimension": "variable.metric", "obligation_id": f"metric-{question_id}"},
            {"dimension": "formula.definition", "obligation_id": f"formula-{question_id}", "expected": {"required_operations": ["subtract_or_difference"]}},
            {"dimension": "operand.set", "obligation_id": f"operand-{question_id}"},
            {"dimension": "operand.compatibility", "obligation_id": f"compatibility-{question_id}"},
            {"dimension": "temporal.period", "obligation_id": f"temporal-{question_id}"},
        ],
    }


def _build_fixture(root: Path) -> Path:
    data = root / "data"
    v13 = data / "v13"
    question_ids = range(1, 1013)
    _write_jsonl(v13 / "claim_requirement_sets_v1.jsonl", [_requirements(question_id) for question_id in question_ids])
    _write_jsonl(v13 / "semantic_coverage_certificates_v2.jsonl", [
        {
            "question_id": question_id,
            "semantic_coverage_certificate_id": f"coverage-{question_id}",
            "v12_certificate_status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY" if question_id >= 987 else "ABSTAIN",
            "required_unresolved_dimensions": ["formula.definition"],
            "unchecked_dimensions": ["source.truth_tier"],
        }
        for question_id in question_ids
    ])
    _write_jsonl(v13 / "composed_blocker_taxonomy_v1.jsonl", [
        {"question_id": question_id, "primary_blocker": "FORMULA_DEFINITION_INCOMPLETE", "missing_operations": ["subtract_or_difference"]}
        for question_id in range(1, 502)
    ] + [
        {"question_id": question_id, "primary_blocker": "OPERAND_SET_INCOMPLETE", "missing_operations": ["reported_value"]}
        for question_id in range(502, 596)
    ])
    _write_jsonl(v13 / "route_blocker_taxonomy_v2.jsonl", [
        {"question_id": question_id, "primary_blocker": "TABLE_OR_METRIC_BINDING_UNRESOLVED", "classification_basis": "fixture", "missing_operations": ["reported_value"], "covered_operations": [], "route_reason_codes": []}
        for question_id in range(596, 846)
    ] + [
        {"question_id": question_id, "primary_blocker": "FORMULA_OR_OPERATOR_DEFINITION_UNRESOLVED", "classification_basis": "fixture", "missing_operations": ["ratio_or_percent"], "covered_operations": ["reported_value"], "route_reason_codes": []}
        for question_id in range(846, 883)
    ] + [
        {"question_id": question_id, "primary_blocker": "ROUTE_CAUSE_UNESTABLISHED", "classification_basis": "fixture", "missing_operations": [], "covered_operations": [], "route_reason_codes": []}
        for question_id in range(883, 949)
    ])
    _write_jsonl(v13 / "temporal_semantics_v1.jsonl", [
        {"question_id": question_id, "claim_requirement": {"kind": "duration", "requested_years": [2022]}, "source_observation": {"period_types": ["fiscal_year"]}, "proof_status": "UNRESOLVED"}
        for question_id in range(949, 987)
    ])
    output_names = {
        "claim_requirements": "claim_requirement_sets_v1.jsonl",
        "semantic_coverage": "semantic_coverage_certificates_v2.jsonl",
        "composed_taxonomy": "composed_blocker_taxonomy_v1.jsonl",
        "route_taxonomy": "route_blocker_taxonomy_v2.jsonl",
        "temporal_semantics": "temporal_semantics_v1.jsonl",
    }
    _write_json(v13 / "manifest.json", {
        "protocol": "vifinqa_claim_requirement_coverage_v13",
        "release_decision": {"status": "blocked"},
        "outputs": {name: {"sha256": _sha(v13 / filename)} for name, filename in output_names.items()},
    })
    plans = [
        {
            "question_id": question_id,
            "decomposition_status": "complete",
            "effective_family": "direct_lookup",
            "formula_id": None,
            "plan_fingerprint": f"plan-{question_id}",
            "operation_ast": {"op": "lookup", "args": ["x0"]},
            "operands": [{"operand_id": "x0", "role": "x0", "entity": "AAA", "scope": "separate", "years": [2022]}],
        }
        for question_id in question_ids
    ]
    typed = data / "typed_plans.jsonl"
    _write_jsonl(typed, plans)
    _write_json(data / "typed_plans.manifest.json", {"protocol": "typed_operand_decomposition_fail_closed_v1", "sidecar_sha256": _sha(typed)})
    formula = data / "formula.jsonl"
    _write_jsonl(formula, [
        {"id": question_id, "formula": {"formula_id": "fixture_formula", "definition_status": "defined"}, "operand_coverage_status": "complete", "evidence_completeness": "complete", "execution_status": "not_executed_source_evidence_only"}
        for question_id in range(1, 79)
    ])
    _write_json(data / "formula.manifest.json", {"sidecar_sha256": _sha(formula)})
    config = root / "configs" / "closure.json"
    inputs = {
        "v13_manifest": data / "v13" / "manifest.json",
        "typed_plans": typed,
        "typed_plans_manifest": data / "typed_plans.manifest.json",
        "formula_evidence": formula,
        "formula_evidence_manifest": data / "formula.manifest.json",
    }
    _write_json(config, {
        "schema_version": 1,
        "protocol": "vifinqa_v13_evidence_closure_workbench_v1",
        "mode": "additive_receipt_intake",
        "input_paths": {name: str(path.relative_to(root)) for name, path in inputs.items()},
        "locked_input_sha256": {name: _sha(path) for name, path in inputs.items()},
    })
    return config


def test_workbench_materializes_all_queues_without_answers(tmp_path: Path) -> None:
    config = _build_fixture(tmp_path)
    output = tmp_path / "out"
    manifest = build_workbench(config_path=config, output_dir=output)
    assert manifest["counts"] == {
        "formula_definition_receipt_intake": 501,
        "operand_compatibility_receipt_intake": 94,
        "route_binding_receipt_intake": 250,
        "route_operator_receipt_intake": 37,
        "route_cause_investigation_intake": 66,
        "temporal_receipt_intake": 38,
        "v12_candidate_recertification_intake": 26,
        "independent_requirement_review_packets": 1012,
        "production_execution_ledger_intake": 1012,
    }
    ledger = load_jsonl(output / "production_execution_ledger_intake_v1.jsonl")
    assert len(ledger) == 1012
    assert {row["entry_status"] for row in ledger} == {"BLOCKED"}
    assert all(row["contains_answer"] is False and row["submission_eligible"] is False for row in ledger)
    assert manifest["source_contract"]["release_authorized"] is False


def test_workbench_rejects_tampered_pinned_input(tmp_path: Path) -> None:
    config = _build_fixture(tmp_path)
    typed = tmp_path / "data" / "typed_plans.jsonl"
    typed.write_text(typed.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch for typed_plans"):
        build_workbench(config_path=config, output_dir=tmp_path / "out")
