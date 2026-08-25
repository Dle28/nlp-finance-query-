#!/usr/bin/env python3
"""Verify a frozen V13 shadow-audit artifact and all declared lineage edges."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance_query.research.proof_policy.claim_requirements import (
    COMPOSED_TAXONOMY_PROTOCOL,
    COVERAGE_PROTOCOL,
    PROOF_STATUSES,
    REQUIREMENT_PROTOCOL,
    ROUTE_TAXONOMY_PROTOCOL,
    TEMPORAL_PROTOCOL,
    canonical_sha256,
    index_by_question,
    load_jsonl,
    sha256_file,
    source_contract,
    status_counts,
    validate_dependency_graph,
    validate_v13_partition,
)
try:
    from scripts.research.proof_policy.build_claim_requirement_coverage_v13 import build_v13
except ModuleNotFoundError:  # Direct `python scripts/verify_...py` execution.
    from build_claim_requirement_coverage_v13 import build_v13


PROTOCOL = "vifinqa_claim_requirement_coverage_v13"
EXPECTED_OUTPUTS = {
    "claim_requirements": ("claim_requirement_sets_v1.jsonl", REQUIREMENT_PROTOCOL, 1),
    "semantic_coverage": ("semantic_coverage_certificates_v2.jsonl", COVERAGE_PROTOCOL, 2),
    "composed_taxonomy": ("composed_blocker_taxonomy_v1.jsonl", COMPOSED_TAXONOMY_PROTOCOL, 1),
    "route_taxonomy": ("route_blocker_taxonomy_v2.jsonl", ROUTE_TAXONOMY_PROTOCOL, 2),
    "temporal_semantics": ("temporal_semantics_v1.jsonl", TEMPORAL_PROTOCOL, 1),
}
SUMMARY_NAME = "claim_requirement_coverage_v13_summary.json"
AUTHORITATIVE_CONFIG_SHA256 = "e6d53e9bb4ae7dd25023f39937f8197f70dc2e04e317acbce3bf27801e7df38b"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _resolve_input(path_text: object) -> Path:
    _require(isinstance(path_text, str) and bool(path_text), "input path must be a non-empty string")
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def _verify_file_hash(path: Path, expected: object, label: str) -> None:
    _require(path.is_file(), f"{label} is missing: {path}")
    _require(isinstance(expected, str) and sha256_file(path) == expected, f"SHA-256 mismatch for {label}: {path}")


def _verify_source_contract(value: object, label: str) -> None:
    _require(value == source_contract(), f"{label} has a promotable or malformed source contract")


def _verify_row_envelope(rows: Sequence[Mapping[str, Any]], *, protocol: str, schema_version: int, label: str) -> dict[int, dict[str, Any]]:
    for row in rows:
        _require(row.get("protocol") == protocol, f"{label} row has unexpected protocol")
        _require(row.get("schema_version") == schema_version, f"{label} row has unexpected schema version")
        _verify_source_contract(row.get("source_contract"), f"{label} row {row.get('question_id')}")
    return index_by_question(rows, label)


def _verify_requirement_rows(rows: Mapping[int, Mapping[str, Any]], definition_id: str) -> None:
    for question_id, row in rows.items():
        _require((row.get("generator") or {}).get("definition_id") == definition_id, f"Q{question_id} definition lineage mismatch")
        expected_id = canonical_sha256({key: value for key, value in row.items() if key != "claim_requirement_set_id"})
        _require(row.get("claim_requirement_set_id") == expected_id, f"Q{question_id} requirement-set ID mismatch")
        requirements = [item for item in row.get("requirements") or [] if isinstance(item, Mapping)]
        _require(len(requirements) == len(row.get("requirements") or []), f"Q{question_id} has malformed requirements")
        validate_dependency_graph(requirements)
        for requirement in requirements:
            proposition = {key: value for key, value in requirement.items() if key != "obligation_id"}
            expected_obligation = canonical_sha256({"question_id": question_id, "definition_id": definition_id, **proposition})
            _require(requirement.get("obligation_id") == expected_obligation, f"Q{question_id} obligation ID mismatch")
        expected_graph = [
            {"obligation_id": item["obligation_id"], "dimension": item["dimension"], "depends_on": item["depends_on"]}
            for item in requirements
            if item.get("depends_on")
        ]
        _require(row.get("dependency_graph") == expected_graph, f"Q{question_id} dependency graph mismatch")
        _require(row.get("requirement_set_completeness") == "CLAIM_COMPLETENESS_UNESTABLISHED", f"Q{question_id} overclaims requirement completeness")


def _verify_coverage_rows(requirements: Mapping[int, Mapping[str, Any]], coverage: Mapping[int, Mapping[str, Any]]) -> None:
    _require(set(requirements) == set(coverage), "coverage question IDs do not match requirement sets")
    for question_id, row in coverage.items():
        expected_id = canonical_sha256({key: value for key, value in row.items() if key != "semantic_coverage_certificate_id"})
        _require(row.get("semantic_coverage_certificate_id") == expected_id, f"Q{question_id} coverage certificate ID mismatch")
        requirement_row = requirements[question_id]
        _require(row.get("claim_requirement_set_id") == requirement_row.get("claim_requirement_set_id"), f"Q{question_id} requirement link mismatch")
        requirement_by_dimension = {str(item["dimension"]): item for item in requirement_row["requirements"]}
        checks = [item for item in row.get("proof_obligations") or [] if isinstance(item, Mapping)]
        _require(len(checks) == len(row.get("proof_obligations") or []), f"Q{question_id} has malformed proof obligations")
        _require(len(checks) == len(requirement_by_dimension), f"Q{question_id} proof obligation count mismatch")
        check_by_dimension = {str(item.get("dimension")): item for item in checks}
        _require(set(check_by_dimension) == set(requirement_by_dimension), f"Q{question_id} proof dimensions mismatch")
        for dimension, check in check_by_dimension.items():
            requirement = requirement_by_dimension[dimension]
            _require(check.get("obligation_id") == requirement.get("obligation_id"), f"Q{question_id} {dimension} obligation link mismatch")
            _require(check.get("applicability") == requirement.get("applicability"), f"Q{question_id} {dimension} applicability mismatch")
            _require(check.get("status") in PROOF_STATUSES, f"Q{question_id} {dimension} has invalid status")
            if check.get("status") == "PASS":
                blockers = [dependency for dependency in requirement.get("depends_on") or [] if check_by_dimension[dependency].get("status") != "PASS"]
                _require(not blockers, f"Q{question_id} {dimension} PASS has non-PASS dependencies")
        unchecked = sorted(dimension for dimension, check in check_by_dimension.items() if check.get("status") == "NOT_CHECKED")
        unresolved = sorted(
            dimension
            for dimension, check in check_by_dimension.items()
            if requirement_by_dimension[dimension].get("applicability") == "REQUIRED" and check.get("status") != "PASS"
        )
        internally_complete = not unchecked and not unresolved
        expected_internal = "INTERNALLY_COMPLETE" if internally_complete else "INTERNAL_COVERAGE_INCOMPLETE"
        _require(row.get("internal_coverage_status") == expected_internal, f"Q{question_id} internal completeness mismatch")
        _require(row.get("unchecked_dimensions") == unchecked and row.get("unchecked_dimension_count") == len(unchecked), f"Q{question_id} unchecked dimension mismatch")
        _require(row.get("required_unresolved_dimensions") == unresolved, f"Q{question_id} unresolved dimension mismatch")
        _require(row.get("claim_complete") is False, f"Q{question_id} may not claim completeness")
        _require(row.get("claim_completeness_status") == "CLAIM_COMPLETENESS_UNESTABLISHED", f"Q{question_id} overclaims claim completeness")
        _require(row.get("release_effect") == "NONE_V13_SHADOW_ONLY", f"Q{question_id} has invalid release effect")


def _replay_outputs(inputs: Mapping[str, Mapping[str, Any]], artifact_dir: Path) -> None:
    resolved = {name: _resolve_input(entry.get("path")) for name, entry in inputs.items()}
    with tempfile.TemporaryDirectory(prefix="v13-verify-replay-", dir=artifact_dir.parent) as temp_dir:
        replay_dir = Path(temp_dir) / "artifact"
        build_v13(
            config=resolved["config"],
            routes=resolved["routes"],
            routes_manifest=resolved["routes_manifest"],
            periods=resolved["periods"],
            periods_manifest=resolved["periods_manifest"],
            bindings=resolved["bindings"],
            bindings_manifest=resolved["bindings_manifest"],
            certificates=resolved["certificates"],
            certificates_manifest=resolved["certificates_manifest"],
            output_dir=replay_dir,
        )
        for filename, _, _ in EXPECTED_OUTPUTS.values():
            _require((artifact_dir / filename).read_bytes() == (replay_dir / filename).read_bytes(), f"deterministic replay mismatch for {filename}")
        _require((artifact_dir / SUMMARY_NAME).read_bytes() == (replay_dir / SUMMARY_NAME).read_bytes(), "deterministic replay mismatch for summary")


def verify_v13_artifact(manifest_path: Path, *, verify_inputs: bool = True) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    artifact_dir = manifest_path.parent
    manifest = _json(manifest_path)
    _require(manifest.get("protocol") == PROTOCOL and manifest.get("schema_version") == 2, "invalid V13 manifest protocol or schema")
    _require(manifest.get("status") == "v13_shadow_audit_complete_no_internal_or_claim_completeness_established", "invalid V13 manifest status")
    _verify_source_contract(manifest.get("source_contract"), "manifest")
    release = manifest.get("release_decision") or {}
    _require(release.get("status") == "blocked" and release.get("changed_from_v12") is False, "V13 release must remain blocked")
    _require(
        set(release.get("reason_codes") or [])
        == {"INTERNAL_COVERAGE_UNESTABLISHED", "CLAIM_COMPLETENESS_UNESTABLISHED", "V13_IS_SHADOW_ONLY", "PRODUCTION_LEDGER_INCOMPLETE"},
        "V13 release reason codes mismatch",
    )

    inputs = manifest.get("inputs") or {}
    _require(isinstance(inputs, Mapping), "manifest inputs must be an object")
    required_inputs = {"config", "builder_source", "definition_source", "verifier_source", "routes", "routes_manifest", "periods", "periods_manifest", "bindings", "bindings_manifest", "certificates", "certificates_manifest"}
    _require(set(inputs) == required_inputs, "manifest input set mismatch")
    if verify_inputs:
        for name, entry in inputs.items():
            _require(isinstance(entry, Mapping), f"input {name} must be an object")
            _verify_file_hash(_resolve_input(entry.get("path")), entry.get("sha256"), f"input {name}")
    trust_root_verified = bool(
        verify_inputs
        and (inputs.get("config") or {}).get("sha256") == AUTHORITATIVE_CONFIG_SHA256
    )

    definition = manifest.get("definition_bundle") or {}
    _require(isinstance(definition, Mapping), "definition bundle must be an object")
    definition_payload = {key: value for key, value in definition.items() if key != "definition_id"}
    _require(definition.get("definition_id") == canonical_sha256(definition_payload), "definition ID mismatch")
    _require(definition.get("config_sha256") == (inputs.get("config") or {}).get("sha256"), "definition config hash mismatch")
    _require(definition.get("builder_sha256") == (inputs.get("builder_source") or {}).get("sha256"), "definition builder hash mismatch")
    _require(definition.get("implementation_sha256") == (inputs.get("definition_source") or {}).get("sha256"), "definition implementation hash mismatch")
    _require(definition.get("verifier_sha256") == (inputs.get("verifier_source") or {}).get("sha256"), "definition verifier hash mismatch")

    outputs = manifest.get("outputs") or {}
    _require(isinstance(outputs, Mapping), "manifest outputs must be an object")
    _require(set(outputs) == {*EXPECTED_OUTPUTS, "summary"}, "manifest output set mismatch")
    row_indexes: dict[str, dict[int, dict[str, Any]]] = {}
    for name, (filename, protocol, schema_version) in EXPECTED_OUTPUTS.items():
        entry = outputs[name]
        _require(isinstance(entry, Mapping) and Path(str(entry.get("path"))).name == filename, f"output {name} path mismatch")
        path = artifact_dir / filename
        _verify_file_hash(path, entry.get("sha256"), f"output {name}")
        rows = load_jsonl(path)
        row_indexes[name] = _verify_row_envelope(rows, protocol=protocol, schema_version=schema_version, label=name)

    summary_entry = outputs["summary"]
    _require(isinstance(summary_entry, Mapping) and Path(str(summary_entry.get("path"))).name == SUMMARY_NAME, "summary path mismatch")
    summary_path = artifact_dir / SUMMARY_NAME
    _verify_file_hash(summary_path, summary_entry.get("sha256"), "output summary")
    summary = _json(summary_path)
    expected_summary = {key: value for key, value in manifest.items() if key not in {"inputs", "outputs"}}
    _require(summary == expected_summary, "summary and manifest metadata diverge")

    requirement_rows = row_indexes["claim_requirements"]
    coverage_rows = row_indexes["semantic_coverage"]
    _verify_requirement_rows(requirement_rows, str(definition.get("definition_id")))
    _verify_coverage_rows(requirement_rows, coverage_rows)
    question_ids = set(requirement_rows)
    _require(len(question_ids) == 1012, "V13 artifact must cover exactly 1,012 questions")
    partition_counts = validate_v13_partition(
        question_ids=question_ids,
        composed_rows=list(row_indexes["composed_taxonomy"].values()),
        route_rows=list(row_indexes["route_taxonomy"].values()),
        temporal_rows=list(row_indexes["temporal_semantics"].values()),
        coverage_rows=list(coverage_rows.values()),
    )
    expected_counts = {
        "question_count": len(question_ids),
        "first_blocker_partition_counts": partition_counts,
        "internal_coverage_status_counts": status_counts(list(coverage_rows.values()), "internal_coverage_status"),
        "claim_completeness_status_counts": status_counts(list(coverage_rows.values()), "claim_completeness_status"),
        "composed_primary_blocker_counts": status_counts(list(row_indexes["composed_taxonomy"].values()), "primary_blocker"),
        "route_primary_blocker_counts": status_counts(list(row_indexes["route_taxonomy"].values()), "primary_blocker"),
        "temporal_claim_kind_counts": status_counts(
            [{"kind": (row.get("claim_requirement") or {}).get("kind")} for row in row_indexes["temporal_semantics"].values()],
            "kind",
        ),
    }
    _require(manifest.get("counts") == expected_counts, "manifest counts do not match verified rows")
    if verify_inputs:
        _replay_outputs(inputs, artifact_dir)
    return {
        "status": (
            "AUTHORITATIVE_VERIFIED_V13_SHADOW_ARTIFACT"
            if trust_root_verified
            else "STRUCTURALLY_VERIFIED_V13_SHADOW_ARTIFACT"
        ),
        "verification_scope": "authoritative_locked_run" if trust_root_verified else "structural_only",
        "trust_root_verified": trust_root_verified,
        "question_count": len(question_ids),
        "manifest_sha256": sha256_file(manifest_path),
        "release_status": "blocked",
        "inputs_verified": verify_inputs,
        "outputs_replayed": verify_inputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--skip-inputs",
        action="store_true",
        help="Run an untrusted structural check only; does not verify inputs, replay outputs, or confer authoritative status",
    )
    args = parser.parse_args()
    print(json.dumps(verify_v13_artifact(args.manifest, verify_inputs=not args.skip_inputs), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
