#!/usr/bin/env python3
"""Build the additive V13 claim-requirement shadow audit over locked V12."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from finance_query.claim_requirements import (
    COMPOSED_TAXONOMY_PROTOCOL,
    COVERAGE_PROTOCOL,
    REQUIREMENT_PROTOCOL,
    ROUTE_TAXONOMY_PROTOCOL,
    TEMPORAL_PROTOCOL,
    build_claim_requirement_set,
    build_semantic_coverage_certificate,
    build_temporal_semantics,
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


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _require_manifest_output(manifest_path: Path, artifact_path: Path, *names: str) -> None:
    manifest = _json(manifest_path)
    expected = None
    for name in names:
        expected = ((manifest.get("outputs") or {}).get(name) or {}).get("sha256")
        if isinstance(expected, str):
            break
    if not isinstance(expected, str) or expected != sha256_file(artifact_path):
        raise ValueError(f"SHA-256 mismatch for {artifact_path}")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_v13(
    *,
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
    _require_manifest_output(routes_manifest, routes, "overlay")
    _require_manifest_output(periods_manifest, periods, "period_packets")
    _require_manifest_output(bindings_manifest, bindings, "bindings")
    _require_manifest_output(certificates_manifest, certificates, "answer_certificates")

    indexes = {
        "routes": index_by_question(load_jsonl(routes), "routes"),
        "periods": index_by_question(load_jsonl(periods), "periods"),
        "bindings": index_by_question(load_jsonl(bindings), "bindings"),
        "certificates": index_by_question(load_jsonl(certificates), "certificates"),
    }
    question_ids = set(indexes["routes"])
    if len(question_ids) != 1012 or any(set(index) != question_ids for index in indexes.values()):
        raise ValueError("V13 inputs must cover the same 1,012 questions")

    requirement_rows = [build_claim_requirement_set(indexes["routes"][qid]) for qid in sorted(question_ids)]
    coverage_rows = [
        build_semantic_coverage_certificate(requirement, indexes["certificates"][int(requirement["question_id"])])
        for requirement in requirement_rows
    ]
    composed_rows = [
        row
        for qid in sorted(question_ids)
        if (row := classify_composed_blocker(
            indexes["routes"][qid], indexes["bindings"][qid], indexes["certificates"][qid]
        )) is not None
    ]
    route_rows = [
        row
        for qid in sorted(question_ids)
        if (row := classify_route_blocker(indexes["routes"][qid])) is not None
    ]
    temporal_rows = [
        row
        for qid in sorted(question_ids)
        if (row := build_temporal_semantics(
            indexes["routes"][qid], indexes["periods"][qid], indexes["certificates"][qid]
        )) is not None
    ]
    partition_counts = validate_v13_partition(
        question_ids=question_ids,
        composed_rows=composed_rows,
        route_rows=route_rows,
        temporal_rows=temporal_rows,
        coverage_rows=coverage_rows,
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {
        "claim_requirements": output_dir / "claim_requirement_sets_v1.jsonl",
        "semantic_coverage": output_dir / "semantic_coverage_certificates_v2.jsonl",
        "composed_taxonomy": output_dir / "composed_blocker_taxonomy_v1.jsonl",
        "route_taxonomy": output_dir / "route_blocker_taxonomy_v2.jsonl",
        "temporal_semantics": output_dir / "temporal_semantics_v1.jsonl",
    }
    for name, rows in (
        ("claim_requirements", requirement_rows),
        ("semantic_coverage", coverage_rows),
        ("composed_taxonomy", composed_rows),
        ("route_taxonomy", route_rows),
        ("temporal_semantics", temporal_rows),
    ):
        _write_jsonl(outputs[name], rows)

    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "v13_shadow_audit_complete_claim_completeness_unestablished",
        "counts": {
            "question_count": len(question_ids),
            "first_blocker_partition_counts": partition_counts,
            "internal_coverage_status_counts": status_counts(coverage_rows, "internal_coverage_status"),
            "claim_completeness_status_counts": status_counts(coverage_rows, "claim_completeness_status"),
            "composed_primary_blocker_counts": status_counts(composed_rows, "primary_blocker"),
            "route_primary_blocker_counts": status_counts(route_rows, "primary_blocker"),
            "temporal_kind_counts": status_counts(
                [{"kind": (row.get("required_temporal_object") or {}).get("kind")} for row in temporal_rows],
                "kind",
            ),
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
            "reason_codes": [
                "CLAIM_COMPLETENESS_UNESTABLISHED",
                "V13_IS_SHADOW_ONLY",
                "PRODUCTION_LEDGER_INCOMPLETE",
            ],
        },
        "source_contract": source_contract(),
    }
    summary_path = output_dir / "claim_requirement_coverage_v13_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    inputs = {
        "routes": routes,
        "routes_manifest": routes_manifest,
        "periods": periods,
        "periods_manifest": periods_manifest,
        "bindings": bindings,
        "bindings_manifest": bindings_manifest,
        "certificates": certificates,
        "certificates_manifest": certificates_manifest,
    }
    manifest = {
        **summary,
        "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
        "outputs": {
            **{name: {"path": str(path), "sha256": sha256_file(path)} for name, path in outputs.items()},
            "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
        },
    }
    manifest_path = output_dir / "claim_requirement_coverage_v13.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "routes", "routes_manifest", "periods", "periods_manifest", "bindings",
        "bindings_manifest", "certificates", "certificates_manifest",
    ):
        parser.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = build_v13(**vars(args))
    print(json.dumps({"status": result["status"], "counts": result["counts"], "manifest_path": result["manifest_path"]}, indent=2))


if __name__ == "__main__":
    main()
