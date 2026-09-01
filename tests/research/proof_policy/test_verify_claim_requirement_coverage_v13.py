from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).parents[3]
spec = importlib.util.spec_from_file_location(
    "verify_claim_requirement_coverage_v13",
    ROOT / "scripts" / "research" / "proof_policy" / "verify_claim_requirement_coverage_v13.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def built_artifact(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("v13-hermetic")
    routes = root / "routes.jsonl"
    periods = root / "periods.jsonl"
    bindings = root / "bindings.jsonl"
    certificates = root / "certificates.jsonl"
    route_rows = []
    period_rows = []
    binding_rows = []
    certificate_rows = []
    for question_id in range(1, 1013):
        route_rows.append({
            "question_id": question_id,
            "question": "Chỉ tiêu của DLG năm 2022 là bao nhiêu?",
            "route_status": "route_complete",
            "question_context": {"entities": ["DLG"], "years": [2022]},
            "requested_output_unit": {"kind": "currency", "unit": "vnd", "source": "literal_question"},
            "required_operations": ["reported_value"],
            "covered_operations": ["reported_value"],
            "missing_operations": [],
        })
        period_rows.append({"question_id": question_id, "packet_status": "packet_blocked", "stages": []})
        binding_rows.append({"question_id": question_id, "stages": []})
        certificate_rows.append({
            "question_id": question_id,
            "authorization_status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
            "answer_certificate": {
                "status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
                "answer_certificate_id": f"certificate-{question_id}",
                "binding_receipts": [],
            },
        })
    for path, rows in ((routes, route_rows), (periods, period_rows), (bindings, binding_rows), (certificates, certificate_rows)):
        _write_jsonl(path, rows)

    contract = {
        "research_only": True,
        "evidence_eligible": False,
        "may_materialize_answer": False,
        "submission_eligible": False,
        "training_eligible": False,
        "promotion_allowed": False,
        "release_authorized": False,
    }
    routes_manifest = root / "routes.manifest.json"
    periods_manifest = root / "periods.manifest.json"
    bindings_manifest = root / "bindings.manifest.json"
    certificates_manifest = root / "certificates.manifest.json"
    _write_json(routes_manifest, {"protocol": "route_completeness_overlay_v3", "outputs": {"overlay": {"sha256": module.sha256_file(routes)}}})
    _write_json(periods_manifest, {"protocol": "period_column_candidate_packets_v2_exact_source_title", "outputs": {"period_packets": {"sha256": module.sha256_file(periods)}}})
    _write_json(bindings_manifest, {"protocol": "exact_cell_unit_binding_candidates_v6_cross_entity_composition", "outputs": {"bindings": {"sha256": module.sha256_file(bindings)}}, "source_contract": contract})
    _write_json(certificates_manifest, {"protocol": "vifinqa_grounded_authorization_replay_v1", "inputs": {"bindings": {"sha256": module.sha256_file(bindings)}}, "outputs": {"answer_certificates": {"sha256": module.sha256_file(certificates)}}, "source_contract": contract})
    locked_paths = {
        "routes": routes,
        "routes_manifest": routes_manifest,
        "periods": periods,
        "periods_manifest": periods_manifest,
        "bindings": bindings,
        "bindings_manifest": bindings_manifest,
        "certificates": certificates,
        "certificates_manifest": certificates_manifest,
    }
    config = root / "config.json"
    _write_json(config, {
        "schema_version": 1,
        "protocol": module.PROTOCOL,
        "mode": "additive_shadow_audit",
        "input_lineage": "vifinqa-grounded-e2e-relational-entity-role-v12_20260823-lock1",
        "locked_input_sha256": {name: module.sha256_file(path) for name, path in locked_paths.items()},
    })
    output = root / "artifact"
    module.build_v13(
        config=config,
        routes=routes,
        routes_manifest=routes_manifest,
        periods=periods,
        periods_manifest=periods_manifest,
        bindings=bindings,
        bindings_manifest=bindings_manifest,
        certificates=certificates,
        certificates_manifest=certificates_manifest,
        output_dir=output,
    )
    return output


def _copy_artifact(source: Path, tmp_path: Path) -> Path:
    target = tmp_path / "v13"
    shutil.copytree(source, target)
    return target / "claim_requirement_coverage_v13.manifest.json"


def test_hermetic_artifact_verifies_all_lineage_and_release_invariants(built_artifact: Path) -> None:
    result = module.verify_v13_artifact(built_artifact / "claim_requirement_coverage_v13.manifest.json")
    assert result["status"] == "STRUCTURALLY_VERIFIED_V13_SHADOW_ARTIFACT"
    assert result["verification_scope"] == "structural_only"
    assert result["trust_root_verified"] is False
    assert result["question_count"] == 1012
    assert result["release_status"] == "blocked"
    assert result["inputs_verified"] is True
    assert result["outputs_replayed"] is True


def test_skip_inputs_can_never_confer_authoritative_status(built_artifact: Path) -> None:
    result = module.verify_v13_artifact(
        built_artifact / "claim_requirement_coverage_v13.manifest.json",
        verify_inputs=False,
    )
    assert result["status"] == "STRUCTURALLY_VERIFIED_V13_SHADOW_ARTIFACT"
    assert result["verification_scope"] == "structural_only"
    assert result["trust_root_verified"] is False
    assert result["inputs_verified"] is False
    assert result["outputs_replayed"] is False


def test_verifier_rejects_tampered_output_even_when_json_remains_valid(built_artifact: Path, tmp_path: Path) -> None:
    manifest_path = _copy_artifact(built_artifact, tmp_path)
    requirements = manifest_path.parent / "claim_requirement_sets_v1.jsonl"
    requirements.write_text(requirements.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch for output claim_requirements"):
        module.verify_v13_artifact(manifest_path)


def test_verifier_replays_sources_and_rejects_forged_false_pass(built_artifact: Path, tmp_path: Path) -> None:
    manifest_path = _copy_artifact(built_artifact, tmp_path)
    coverage_path = manifest_path.parent / "semantic_coverage_certificates_v2.jsonl"
    rows = module.load_jsonl(coverage_path)
    row = rows[0]
    for check in row["proof_obligations"]:
        if check["dimension"] in {"source.integrity", "variable.metric"}:
            check["status"] = "PASS"
            check["reason_codes"] = []
    row["required_unresolved_dimensions"].remove("source.integrity")
    row["required_unresolved_dimensions"].remove("variable.metric")
    row["semantic_coverage_certificate_id"] = module.canonical_sha256({key: value for key, value in row.items() if key != "semantic_coverage_certificate_id"})
    _write_jsonl(coverage_path, rows)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"]["semantic_coverage"]["sha256"] = module.sha256_file(coverage_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="deterministic replay mismatch for semantic_coverage_certificates_v2.jsonl"):
        module.verify_v13_artifact(manifest_path)


def test_verifier_rejects_release_overclaim(built_artifact: Path, tmp_path: Path) -> None:
    manifest_path = _copy_artifact(built_artifact, tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_decision"]["status"] = "ready"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="release must remain blocked"):
        module.verify_v13_artifact(manifest_path, verify_inputs=False)


def test_verifier_rejects_definition_bundle_detached_from_builder_input(built_artifact: Path, tmp_path: Path) -> None:
    manifest_path = _copy_artifact(built_artifact, tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["definition_bundle"]["builder_sha256"] = "0" * 64
    definition_payload = {key: value for key, value in manifest["definition_bundle"].items() if key != "definition_id"}
    manifest["definition_bundle"]["definition_id"] = module.canonical_sha256(definition_payload)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="definition builder hash mismatch"):
        module.verify_v13_artifact(manifest_path, verify_inputs=False)
