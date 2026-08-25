#!/usr/bin/env python3
"""Build an atomic, hash-bound V13 shadow audit over the locked V12 run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.proof_policy.claim_requirements import (
    COMPOSED_TAXONOMY_PROTOCOL,
    COVERAGE_PROTOCOL,
    REQUIREMENT_PROTOCOL,
    ROUTE_TAXONOMY_PROTOCOL,
    TEMPORAL_PROTOCOL,
    build_claim_requirement_set,
    build_semantic_coverage_certificate,
    build_temporal_semantics,
    canonical_sha256,
    classify_composed_blocker,
    classify_route_blocker,
    index_by_question,
    load_jsonl,
    sha256_file,
    source_contract,
    status_counts,
    validate_v13_partition,
)


PROTOCOL = "vifinqa_claim_requirement_coverage_v13"
BUILDER_SOURCE = Path(__file__).resolve()
VERIFIER_SOURCE = REPO_ROOT / "scripts" / "research" / "proof_policy" / "verify_claim_requirement_coverage_v13.py"
DEFINITION_SOURCE = REPO_ROOT / "src" / "finance_query" / "research" / "proof_policy" / "claim_requirements.py"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _manifest_output(manifest: Mapping[str, Any], *names: str) -> str:
    for name in names:
        expected = ((manifest.get("outputs") or {}).get(name) or {}).get("sha256")
        if isinstance(expected, str):
            return expected
    raise ValueError(f"manifest does not declare any of outputs {names}")


def _require_manifest_output(manifest: Mapping[str, Any], artifact_path: Path, *names: str) -> None:
    if _manifest_output(manifest, *names) != sha256_file(artifact_path):
        raise ValueError(f"SHA-256 mismatch for {artifact_path}")


def _validate_lineage(
    *,
    config: Mapping[str, Any],
    routes_manifest: Mapping[str, Any],
    periods_manifest: Mapping[str, Any],
    bindings_manifest: Mapping[str, Any],
    certificates_manifest: Mapping[str, Any],
    bindings: Path,
    input_hashes: Mapping[str, str],
) -> None:
    if config.get("protocol") != PROTOCOL or config.get("mode") != "additive_shadow_audit":
        raise ValueError("invalid V13 config protocol or mode")
    if config.get("input_lineage") != "vifinqa-grounded-e2e-relational-entity-role-v12_20260823-lock1":
        raise ValueError("V13 config must pin the locked V12 input lineage")
    locked_hashes = config.get("locked_input_sha256")
    if not isinstance(locked_hashes, Mapping) or set(locked_hashes) != set(input_hashes):
        raise ValueError("V13 config must declare the exact locked input hash set")
    mismatched = [name for name, value in input_hashes.items() if locked_hashes.get(name) != value]
    if mismatched:
        raise ValueError(f"V13 input hash does not match locked run: {mismatched}")
    if routes_manifest.get("protocol") != "route_completeness_overlay_v3":
        raise ValueError("unexpected route overlay protocol")
    if periods_manifest.get("protocol") != "period_column_candidate_packets_v2_exact_source_title":
        raise ValueError("unexpected period packet protocol")
    if bindings_manifest.get("protocol") != "exact_cell_unit_binding_candidates_v6_cross_entity_composition":
        raise ValueError("unexpected V12 binding protocol")
    if certificates_manifest.get("protocol") != "vifinqa_grounded_authorization_replay_v1":
        raise ValueError("unexpected V12 certificate protocol")
    selected_binding_sha = sha256_file(bindings)
    certificate_binding_sha = ((certificates_manifest.get("inputs") or {}).get("bindings") or {}).get("sha256")
    if certificate_binding_sha != selected_binding_sha:
        raise ValueError("certificate manifest is not linked to the selected V12 bindings")
    if _manifest_output(bindings_manifest, "bindings") != selected_binding_sha:
        raise ValueError("binding manifest lineage mismatch")
    for label, contract in (
        ("bindings", bindings_manifest.get("source_contract") or {}),
        ("certificates", certificates_manifest.get("source_contract") or {}),
    ):
        if contract.get("research_only") is not True:
            raise ValueError(f"{label} input does not explicitly declare research_only")
        for field in ("evidence_eligible", "may_materialize_answer", "submission_eligible", "training_eligible", "promotion_allowed"):
            if field not in contract or contract[field] is not False:
                raise ValueError(f"{label} input lacks explicit non-promotable field {field}")
        if "release_authorized" in contract and contract["release_authorized"] is not False:
            raise ValueError(f"{label} input unexpectedly authorizes release")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_v13(
    *,
    config: Path,
    routes: Path,
    routes_manifest: Path,
    periods: Path,
    periods_manifest: Path,
    bindings: Path,
    bindings_manifest: Path,
    certificates: Path,
    certificates_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite V13 artifact directory: {output_dir}")
    config_data = _json(config)
    route_manifest_data = _json(routes_manifest)
    period_manifest_data = _json(periods_manifest)
    binding_manifest_data = _json(bindings_manifest)
    certificate_manifest_data = _json(certificates_manifest)
    locked_input_paths = {
        "routes": routes,
        "routes_manifest": routes_manifest,
        "periods": periods,
        "periods_manifest": periods_manifest,
        "bindings": bindings,
        "bindings_manifest": bindings_manifest,
        "certificates": certificates,
        "certificates_manifest": certificates_manifest,
    }
    locked_input_hashes = {name: sha256_file(path) for name, path in locked_input_paths.items()}
    _require_manifest_output(route_manifest_data, routes, "overlay")
    _require_manifest_output(period_manifest_data, periods, "period_packets")
    _require_manifest_output(binding_manifest_data, bindings, "bindings")
    _require_manifest_output(certificate_manifest_data, certificates, "answer_certificates")
    _validate_lineage(
        config=config_data,
        routes_manifest=route_manifest_data,
        periods_manifest=period_manifest_data,
        bindings_manifest=binding_manifest_data,
        certificates_manifest=certificate_manifest_data,
        bindings=bindings,
        input_hashes=locked_input_hashes,
    )
    definition_bundle = {
        "config_sha256": sha256_file(config),
        "builder_sha256": sha256_file(BUILDER_SOURCE),
        "implementation_sha256": sha256_file(DEFINITION_SOURCE),
        "verifier_sha256": sha256_file(VERIFIER_SOURCE),
        "config_protocol": config_data["protocol"],
        "schema_version": config_data["schema_version"],
    }
    definition_id = canonical_sha256(definition_bundle)
    indexes = {
        "routes": index_by_question(load_jsonl(routes), "routes"),
        "periods": index_by_question(load_jsonl(periods), "periods"),
        "bindings": index_by_question(load_jsonl(bindings), "bindings"),
        "certificates": index_by_question(load_jsonl(certificates), "certificates"),
    }
    question_ids = set(indexes["routes"])
    if len(question_ids) != 1012 or any(set(index) != question_ids for index in indexes.values()):
        raise ValueError("V13 inputs must cover the same 1,012 questions")
    requirement_rows = [build_claim_requirement_set(indexes["routes"][qid], definition_id=definition_id) for qid in sorted(question_ids)]
    coverage_rows = [build_semantic_coverage_certificate(row, indexes["certificates"][int(row["question_id"])]) for row in requirement_rows]
    composed_rows = [row for qid in sorted(question_ids) if (row := classify_composed_blocker(indexes["routes"][qid], indexes["bindings"][qid], indexes["certificates"][qid])) is not None]
    route_rows = [row for qid in sorted(question_ids) if (row := classify_route_blocker(indexes["routes"][qid])) is not None]
    temporal_rows = [row for qid in sorted(question_ids) if (row := build_temporal_semantics(indexes["routes"][qid], indexes["periods"][qid], indexes["certificates"][qid])) is not None]
    partition_counts = validate_v13_partition(question_ids=question_ids, composed_rows=composed_rows, route_rows=route_rows, temporal_rows=temporal_rows, coverage_rows=coverage_rows)
    summary = {
        "schema_version": 2,
        "protocol": PROTOCOL,
        "status": "v13_shadow_audit_complete_no_internal_or_claim_completeness_established",
        "definition_bundle": {**definition_bundle, "definition_id": definition_id},
        "counts": {
            "question_count": len(question_ids),
            "first_blocker_partition_counts": partition_counts,
            "internal_coverage_status_counts": status_counts(coverage_rows, "internal_coverage_status"),
            "claim_completeness_status_counts": status_counts(coverage_rows, "claim_completeness_status"),
            "composed_primary_blocker_counts": status_counts(composed_rows, "primary_blocker"),
            "route_primary_blocker_counts": status_counts(route_rows, "primary_blocker"),
            "temporal_claim_kind_counts": status_counts([{"kind": (row.get("claim_requirement") or {}).get("kind")} for row in temporal_rows], "kind"),
        },
        "protocols": {
            "claim_requirements": REQUIREMENT_PROTOCOL,
            "semantic_coverage": COVERAGE_PROTOCOL,
            "composed_taxonomy": COMPOSED_TAXONOMY_PROTOCOL,
            "route_taxonomy": ROUTE_TAXONOMY_PROTOCOL,
            "temporal_semantics": TEMPORAL_PROTOCOL,
        },
        "release_decision": {
            "status": "blocked",
            "changed_from_v12": False,
            "reason_codes": ["INTERNAL_COVERAGE_UNESTABLISHED", "CLAIM_COMPLETENESS_UNESTABLISHED", "V13_IS_SHADOW_ONLY", "PRODUCTION_LEDGER_INCOMPLETE"],
        },
        "source_contract": source_contract(),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        names_and_rows = {
            "claim_requirements": ("claim_requirement_sets_v1.jsonl", requirement_rows),
            "semantic_coverage": ("semantic_coverage_certificates_v2.jsonl", coverage_rows),
            "composed_taxonomy": ("composed_blocker_taxonomy_v1.jsonl", composed_rows),
            "route_taxonomy": ("route_blocker_taxonomy_v2.jsonl", route_rows),
            "temporal_semantics": ("temporal_semantics_v1.jsonl", temporal_rows),
        }
        staged_paths: dict[str, Path] = {}
        for name, (filename, rows) in names_and_rows.items():
            staged_paths[name] = staging / filename
            _write_jsonl(staged_paths[name], rows)
        summary_staged = staging / "claim_requirement_coverage_v13_summary.json"
        summary_staged.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        inputs = {
            "config": config,
            "builder_source": BUILDER_SOURCE,
            "definition_source": DEFINITION_SOURCE,
            "verifier_source": VERIFIER_SOURCE,
            "routes": routes,
            "routes_manifest": routes_manifest,
            "periods": periods,
            "periods_manifest": periods_manifest,
            "bindings": bindings,
            "bindings_manifest": bindings_manifest,
            "certificates": certificates,
            "certificates_manifest": certificates_manifest,
        }
        outputs = {
            name: {"path": str(output_dir / path.name), "sha256": sha256_file(path)}
            for name, path in staged_paths.items()
        }
        outputs["summary"] = {"path": str(output_dir / summary_staged.name), "sha256": sha256_file(summary_staged)}
        manifest = {
            **summary,
            "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
            "outputs": outputs,
        }
        manifest_staged = staging / "claim_requirement_coverage_v13.manifest.json"
        manifest_staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**manifest, "manifest_path": str(output_dir / "claim_requirement_coverage_v13.manifest.json")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    for name in ("routes", "routes_manifest", "periods", "periods_manifest", "bindings", "bindings_manifest", "certificates", "certificates_manifest"):
        parser.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = build_v13(**vars(args))
    print(json.dumps({"status": result["status"], "counts": result["counts"], "manifest_path": result["manifest_path"]}, indent=2))


if __name__ == "__main__":
    main()
