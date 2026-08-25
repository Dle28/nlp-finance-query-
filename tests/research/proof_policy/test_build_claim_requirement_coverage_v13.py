from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "build_claim_requirement_coverage_v13",
    Path(__file__).parents[3] / "scripts" / "research" / "proof_policy" / "build_claim_requirement_coverage_v13.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _inputs(binding_sha: str = "a" * 64):
    input_hashes = {
        "routes": "1" * 64,
        "routes_manifest": "2" * 64,
        "periods": "3" * 64,
        "periods_manifest": "4" * 64,
        "bindings": binding_sha,
        "bindings_manifest": "5" * 64,
        "certificates": "6" * 64,
        "certificates_manifest": "7" * 64,
    }
    config = {
        "protocol": module.PROTOCOL,
        "mode": "additive_shadow_audit",
        "input_lineage": "vifinqa-grounded-e2e-relational-entity-role-v12_20260823-lock1",
        "locked_input_sha256": dict(input_hashes),
    }
    routes = {"protocol": "route_completeness_overlay_v3"}
    periods = {"protocol": "period_column_candidate_packets_v2_exact_source_title"}
    bindings = {
        "protocol": "exact_cell_unit_binding_candidates_v6_cross_entity_composition",
        "outputs": {"bindings": {"sha256": binding_sha}},
        "source_contract": {"research_only": True, "evidence_eligible": False, "may_materialize_answer": False, "submission_eligible": False, "training_eligible": False, "promotion_allowed": False},
    }
    certificates = {
        "protocol": "vifinqa_grounded_authorization_replay_v1",
        "inputs": {"bindings": {"sha256": binding_sha}},
        "source_contract": {"research_only": True, "evidence_eligible": False, "may_materialize_answer": False, "submission_eligible": False, "training_eligible": False, "promotion_allowed": False},
    }
    return config, routes, periods, bindings, certificates, input_hashes


def test_lineage_validator_rejects_mixed_binding_and_certificate_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, routes, periods, bindings, certificates, input_hashes = _inputs()
    binding_file = tmp_path / "bindings.jsonl"
    binding_file.write_text("selected binding bytes", encoding="utf-8")
    selected_sha = module.sha256_file(binding_file)
    input_hashes["bindings"] = selected_sha
    config["locked_input_sha256"]["bindings"] = selected_sha
    bindings["outputs"]["bindings"]["sha256"] = selected_sha
    certificates["inputs"]["bindings"]["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="not linked"):
        module._validate_lineage(
            config=config,
            routes_manifest=routes,
            periods_manifest=periods,
            bindings_manifest=bindings,
            certificates_manifest=certificates,
            bindings=binding_file,
            input_hashes=input_hashes,
        )


def test_lineage_validator_accepts_exact_locked_edge(tmp_path: Path) -> None:
    binding_file = tmp_path / "bindings.jsonl"
    binding_file.write_text("selected binding bytes", encoding="utf-8")
    selected_sha = module.sha256_file(binding_file)
    config, routes, periods, bindings, certificates, input_hashes = _inputs(selected_sha)
    module._validate_lineage(
        config=config,
        routes_manifest=routes,
        periods_manifest=periods,
        bindings_manifest=bindings,
        certificates_manifest=certificates,
        bindings=binding_file,
        input_hashes=input_hashes,
    )


def test_lineage_validator_rejects_self_consistent_but_unlocked_run(tmp_path: Path) -> None:
    binding_file = tmp_path / "bindings.jsonl"
    binding_file.write_text("selected binding bytes", encoding="utf-8")
    selected_sha = module.sha256_file(binding_file)
    config, routes, periods, bindings, certificates, input_hashes = _inputs(selected_sha)
    input_hashes["certificates"] = "8" * 64
    with pytest.raises(ValueError, match="does not match locked run"):
        module._validate_lineage(
            config=config,
            routes_manifest=routes,
            periods_manifest=periods,
            bindings_manifest=bindings,
            certificates_manifest=certificates,
            bindings=binding_file,
            input_hashes=input_hashes,
        )
