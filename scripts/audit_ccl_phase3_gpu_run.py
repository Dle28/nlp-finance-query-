#!/usr/bin/env python3
"""Audit a downloaded, route-scoped CCL Phase 3 GPU run without promotion."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "ccl_phase3_gpu_run_audit_v1"
QWEN_ROUTE = "qwen3_8b_primary_graph_v2"
REQUIRED_OUTPUTS = (
    "llm_raw_responses_v1.jsonl",
    "model_execution_report.json",
    "model_execution_manifest.json",
    "llm_proposals_validated_v1.jsonl",
    "llm_response_validation_report.json",
    "proposal_validation_manifest.json",
    "ccl_phase3_kaggle_receipt_v1.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"expected JSONL objects: {path}")
    return values


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError(f"SHA-256 mismatch: {label}")


def _require_non_promotable(value: Mapping[str, Any]) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise ValueError("GPU artifact violates non-promotable contract")


def audit_gpu_run(
    *,
    job_manifest: Path,
    requests: Path,
    source_manifest: Path,
    artifact_dir: Path,
    output: Path,
    route_id: str = QWEN_ROUTE,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite GPU audit: {output}")
    job = _json(job_manifest)
    if (
        job.get("protocol") != "vifinqa_ccl_phase3_bakeoff_v1"
        or job.get("run_status") != "prepared_phase_3_inference_not_executed"
        or job.get("training_eligible") is not False
        or job.get("model_execution_recorded") is not False
    ):
        raise ValueError("input job is not a non-promotable prepared bake-off")
    _require_hash(requests, ((job.get("outputs") or {}).get(requests.name) or {}).get("sha256"), label="requests")
    artifacts = {name: artifact_dir / name for name in REQUIRED_OUTPUTS}
    if any(not path.is_file() for path in artifacts.values()):
        missing = sorted(name for name, path in artifacts.items() if not path.is_file())
        raise FileNotFoundError(f"missing downloaded GPU artifacts: {missing}")
    routes = {str(route.get("route_id")): route for route in job.get("routes") or [] if isinstance(route, dict)}
    route = routes.get(route_id)
    if not route or not bool(route.get("enabled")):
        raise ValueError("requested route is absent or disabled in job manifest")
    expected_requests = [item for item in _jsonl(requests) if item.get("route_id") == route_id]
    expected_ids = {str(item.get("request_id")) for item in expected_requests}
    if len(expected_ids) != len(expected_requests) or not expected_ids:
        raise ValueError("Qwen request identity is invalid")

    execution = _json(artifacts["model_execution_manifest.json"])
    if execution.get("run_status") != "model_execution_complete_responses_unvalidated":
        raise ValueError("GPU model execution did not complete")
    _require_non_promotable(execution)
    if execution.get("route", {}).get("route_id") != route_id or execution.get("load_in_4bit") is not True:
        raise ValueError("GPU execution did not use the declared 4-bit route")
    runtime = execution.get("runtime") or {}
    capability = runtime.get("gpu_compute_capability")
    expected_arch = None
    if isinstance(capability, list) and len(capability) == 2 and all(isinstance(value, int) for value in capability):
        expected_arch = f"sm_{capability[0]}{capability[1]}"
    if (
        expected_arch is None
        or tuple(capability) < (6, 0)
        or expected_arch not in set(runtime.get("torch_arch_list") or [])
    ):
        raise ValueError("GPU execution runtime is not proven compatible with the declared 4-bit route")
    execution_inputs = execution.get("inputs") or {}
    _require_hash(job_manifest, (execution_inputs.get("job_manifest") or {}).get("sha256"), label="execution job")
    _require_hash(requests, (execution_inputs.get("requests") or {}).get("sha256"), label="execution requests")
    _require_hash(
        artifacts["llm_raw_responses_v1.jsonl"],
        ((execution.get("outputs") or {}).get("llm_raw_responses_v1.jsonl") or {}).get("sha256"),
        label="raw responses",
    )

    raw_rows = _jsonl(artifacts["llm_raw_responses_v1.jsonl"])
    raw_ids = {str(item.get("request_id")) for item in raw_rows}
    if len(raw_rows) != len(raw_ids) or raw_ids != expected_ids:
        raise ValueError("raw GPU response coverage does not match Qwen requests")
    if any(
        item.get("route_id") != route_id
        or item.get("model_id") != route.get("model_id")
        or item.get("model_revision") != route.get("revision")
        or item.get("training_eligible") is not False
        for item in raw_rows
    ):
        raise ValueError("raw GPU response row violates route contract")

    validation = _json(artifacts["proposal_validation_manifest.json"])
    if validation.get("run_status") != "proposal_validation_complete_not_certified":
        raise ValueError("GPU response validator did not complete")
    _require_non_promotable(validation)
    if validation.get("route_id") != route_id:
        raise ValueError("validator did not scope itself to the requested route")
    validation_inputs = validation.get("inputs") or {}
    _require_hash(job_manifest, (validation_inputs.get("job_manifest") or {}).get("sha256"), label="validation job")
    _require_hash(requests, (validation_inputs.get("requests") or {}).get("sha256"), label="validation requests")
    _require_hash(
        artifacts["llm_raw_responses_v1.jsonl"],
        (validation_inputs.get("raw_responses") or {}).get("sha256"),
        label="validation raw responses",
    )
    _require_hash(
        artifacts["llm_proposals_validated_v1.jsonl"],
        ((validation.get("outputs") or {}).get("llm_proposals_validated_v1.jsonl") or {}).get("sha256"),
        label="validated proposals",
    )
    validated_rows = _jsonl(artifacts["llm_proposals_validated_v1.jsonl"])
    validated_ids = {str(item.get("request_id")) for item in validated_rows}
    if len(validated_rows) != len(validated_ids) or validated_ids != expected_ids:
        raise ValueError("validated output coverage does not match Qwen requests")
    if any(item.get("training_eligible") is not False or item.get("certification_allowed") is not False for item in validated_rows):
        raise ValueError("validated proposal violates non-promotable contract")
    validation_error_counts = Counter(
        str(error)
        for item in validated_rows
        for error in (item.get("validation_errors") or [])
    )
    raw_response_lengths = [len(str(item.get("raw_response") or "")) for item in raw_rows]
    raw_json_object_count = 0
    for item in raw_rows:
        try:
            raw_value = json.loads(str(item.get("raw_response") or ""))
        except json.JSONDecodeError:
            continue
        raw_json_object_count += int(isinstance(raw_value, dict))
    report = _json(artifacts["llm_response_validation_report.json"])
    if (
        report.get("expected_response_count") != len(expected_ids)
        or report.get("observed_raw_response_count") != len(raw_rows)
        or report.get("valid_proposal_only_count", 0) + report.get("invalid_or_missing_unresolved_count", 0)
        != len(expected_ids)
    ):
        raise ValueError("validator report counts are inconsistent")

    source = _json(source_manifest)
    receipt = _json(artifacts["ccl_phase3_kaggle_receipt_v1.json"])
    _require_non_promotable(receipt)
    if (
        receipt.get("job_manifest_sha256") != sha256_file(job_manifest)
        or receipt.get("source_tree_sha256") != (source.get("source_bundle") or {}).get("source_tree_sha256")
        or receipt.get("qwen_execution_manifest_sha256") != sha256_file(artifacts["model_execution_manifest.json"])
        or receipt.get("qwen_validation_manifest_sha256") != sha256_file(artifacts["proposal_validation_manifest.json"])
    ):
        raise ValueError("Kaggle receipt provenance does not match downloaded artifacts")
    gpu = receipt.get("gpu") or {}
    if not isinstance(gpu.get("vram_gib"), (int, float)) or float(gpu["vram_gib"]) < 12:
        raise ValueError("Kaggle receipt records insufficient GPU VRAM")
    if receipt.get("model_runtime") != runtime:
        raise ValueError("Kaggle receipt runtime does not match model execution runtime")

    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "audit_passed": True,
        "kernel_route": route_id,
        "input_hashes": {
            "job_manifest": sha256_file(job_manifest),
            "requests": sha256_file(requests),
            "source_manifest": sha256_file(source_manifest),
        },
        "gpu": gpu,
        "counts": {
            "qwen_request_count": len(expected_ids),
            "raw_response_count": len(raw_rows),
            "validated_status_counts": dict(sorted(Counter(str(item.get("status")) for item in validated_rows).items())),
            "valid_proposal_only_count": report["valid_proposal_only_count"],
            "invalid_or_missing_unresolved_count": report["invalid_or_missing_unresolved_count"],
        },
        "diagnostics": {
            "raw_json_object_count": raw_json_object_count,
            "raw_non_object_or_parse_failure_count": len(raw_rows) - raw_json_object_count,
            "raw_response_character_count": {
                "minimum": min(raw_response_lengths),
                "median": sorted(raw_response_lengths)[len(raw_response_lengths) // 2],
                "maximum": max(raw_response_lengths),
            },
            "validation_error_counts": dict(sorted(validation_error_counts.items())),
        },
        "training_eligible": False,
        "certification_allowed": False,
        "next_gate": "phase_4_5_deterministic_semantic_verifier",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--route-id", default=QWEN_ROUTE)
    args = parser.parse_args()
    print(json.dumps(audit_gpu_run(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
