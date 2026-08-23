#!/usr/bin/env python3
"""Audit a hash-bound CCL Phase 5 component-selection GPU smoke run.

The audit is deliberately evidence-only: it proves that a closed-world model
selection referred to literal source components, but it never treats that
selection as a semantic label, training datum, or certified table meaning.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.certified_canonical.phase5_component_selection import _is_nonselectable_numeric_value


PROTOCOL = "ccl_phase5_component_selection_smoke_audit_v1"
PILOT_AUDIT_PROTOCOL = "ccl_phase5_component_selection_pilot_audit_v1"
SOURCE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_v1"
SMOKE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_smoke_v1"
PILOT_PROTOCOL = "vifinqa_ccl_phase5_component_selection_pilot_v1"
VALID_STATUSES = {"VALID_COMPONENT_SELECTION_ONLY", "VALID_ABSTENTION_ONLY", "INVALID_UNRESOLVED"}
_JOB_CONTRACTS = {
    SMOKE_PROTOCOL: ("prepared_component_selection_smoke_not_executed", 5, 5, PROTOCOL),
    PILOT_PROTOCOL: ("prepared_component_selection_pilot_not_executed", 1, 32, PILOT_AUDIT_PROTOCOL),
}


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


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise ValueError(f"{label} violates the non-promotable contract")


def _output_hash(value: Mapping[str, Any], file_name: str, *, label: str) -> object:
    return ((value.get("outputs") or {}).get(file_name) or {}).get("sha256")


def _input_hash(value: Mapping[str, Any], file_name: str, *, label: str) -> object:
    result = ((value.get("inputs") or {}).get(file_name) or {}).get("sha256")
    if result is None:
        raise ValueError(f"{label} does not bind {file_name}")
    return result


def _component_summary(packet: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
    components = {str(item.get("component_id") or ""): item for item in packet.get("components") or []}
    if not components or len(components) != len(packet.get("components") or []):
        raise ValueError("packet component menu is invalid")
    primary_id = selection.get("primary_component_id")
    supporting_ids = selection.get("supporting_component_ids")
    unresolved = selection.get("unresolved_conditions")
    if not isinstance(supporting_ids, list) or not isinstance(unresolved, list):
        raise ValueError("validated selection has malformed lists")
    if primary_id is None:
        if supporting_ids or not unresolved:
            raise ValueError("validated abstention violates its closed-world contract")
        return {"selection_kind": "abstention", "unresolved_conditions": unresolved}
    primary = components.get(str(primary_id))
    if not isinstance(primary, Mapping) or primary.get("role") != "report_scope_or_selected_relation_context":
        raise ValueError("validated primary component is not a source context")
    supporting: list[dict[str, str]] = []
    for component_id in supporting_ids:
        component = components.get(str(component_id))
        if not isinstance(component, Mapping) or component.get("role") not in {"column_header", "row_label"}:
            raise ValueError("validated supporting component is not a source header or row label")
        literal = component.get("literal")
        if not isinstance(literal, str) or not literal:
            raise ValueError("validated component is missing its source literal")
        supporting.append({"role": str(component["role"]), "literal": literal})
    literal = primary.get("literal")
    if not isinstance(literal, str) or not literal:
        raise ValueError("validated primary component is missing its source literal")
    return {
        "selection_kind": "component_selection",
        "primary": {"role": str(primary["role"]), "literal": literal},
        "supporting": supporting,
        "unresolved_conditions": unresolved,
    }


def audit_component_selection_smoke(
    *,
    source_packets: Path,
    source_manifest: Path,
    smoke_job_manifest: Path,
    smoke_packets: Path,
    smoke_packet_manifest: Path,
    requests: Path,
    token_preflight: Path | None = None,
    source_bundle_manifest: Path,
    artifact_dir: Path,
    output: Path,
) -> dict[str, Any]:
    """Verify a completed five-packet smoke against its exact input lineage."""
    if output.exists():
        raise FileExistsError(f"refusing to overwrite component-selection audit: {output}")
    inputs = [
        source_packets,
        source_manifest,
        smoke_job_manifest,
        smoke_packets,
        smoke_packet_manifest,
        requests,
        source_bundle_manifest,
    ]
    if token_preflight is not None:
        inputs.append(token_preflight)
    if any(not path.is_file() for path in inputs):
        missing = [str(path) for path in inputs if not path.is_file()]
        raise FileNotFoundError(f"missing audit input: {missing}")

    source_manifest_json = _json(source_manifest)
    if (
        source_manifest_json.get("protocol") != SOURCE_PROTOCOL
        or source_manifest_json.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
    ):
        raise ValueError("source packet manifest is unsupported")
    _require_non_promotable(source_manifest_json, label="source packet manifest")
    _require_hash(source_packets, _output_hash(source_manifest_json, source_packets.name, label="source packet manifest"), label="source packets")
    source_rows = _jsonl(source_packets)
    source_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in source_rows}
    if not source_by_id or len(source_by_id) != len(source_rows):
        raise ValueError("source packet identities are invalid")
    component_menu_stats = [row.get("component_menu_stats") for row in source_rows]
    numeric_guard_applied = bool(component_menu_stats) and all(
        isinstance(stats, Mapping) and "numeric_value_header_exclusion_count" in stats
        for stats in component_menu_stats
    )
    if any(stats is not None for stats in component_menu_stats) and not numeric_guard_applied:
        raise ValueError("source packet numeric-guard statistics are incomplete")
    if numeric_guard_applied and any(
        not isinstance(stats["numeric_value_header_exclusion_count"], int)
        or isinstance(stats["numeric_value_header_exclusion_count"], bool)
        or stats["numeric_value_header_exclusion_count"] < 0
        for stats in component_menu_stats
    ):
        raise ValueError("source packet numeric-guard exclusion counts are invalid")
    numeric_header_violations = [
        {
            "component_selection_packet_id": packet_id,
            "literal": str(component.get("literal") or ""),
        }
        for packet_id, packet in source_by_id.items()
        for component in packet.get("components") or []
        if isinstance(component, Mapping)
        and component.get("role") == "column_header"
        and _is_nonselectable_numeric_value(str(component.get("literal") or ""))
    ]
    if numeric_guard_applied and numeric_header_violations:
        raise ValueError("numeric-guard source menu exposes numeric data as a column header")
    numeric_guard_summary = {
        "applied": numeric_guard_applied,
        "source_numeric_header_violation_count": len(numeric_header_violations),
        "source_numeric_value_header_exclusion_count": (
            sum(int(stats["numeric_value_header_exclusion_count"]) for stats in component_menu_stats)
            if numeric_guard_applied
            else None
        ),
    }

    job = _json(smoke_job_manifest)
    job_contract = _JOB_CONTRACTS.get(job.get("protocol"))
    if job_contract is None or job.get("run_status") != job_contract[0]:
        raise ValueError("component-selection job manifest is unsupported")
    _require_non_promotable(job, label="component-selection job manifest")

    smoke_manifest_json = _json(smoke_packet_manifest)
    if (
        smoke_manifest_json.get("protocol") != SOURCE_PROTOCOL
        or smoke_manifest_json.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
        or smoke_manifest_json.get("source_packet_manifest_sha256") != sha256_file(source_manifest)
    ):
        raise ValueError("smoke packet manifest does not bind the source packet set")
    _require_non_promotable(smoke_manifest_json, label="smoke packet manifest")
    selected_ids = smoke_manifest_json.get("selected_packet_ids")
    if (
        not isinstance(selected_ids, list)
        or not job_contract[1] <= len(selected_ids) <= job_contract[2]
        or len(selected_ids) != len(set(selected_ids))
    ):
        raise ValueError("component-selection packet manifest violates its job packet-count contract")
    if not set(selected_ids).issubset(source_by_id):
        raise ValueError("smoke packets are not drawn from the declared source packet set")

    smoke_rows = _jsonl(smoke_packets)
    smoke_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in smoke_rows}
    if set(smoke_by_id) != set(selected_ids) or len(smoke_by_id) != len(smoke_rows):
        raise ValueError("smoke packet coverage does not match selected packet identities")
    for packet_id in selected_ids:
        if smoke_by_id[packet_id] != source_by_id[packet_id]:
            raise ValueError("smoke packet differs from its source packet")

    _require_hash(smoke_packets, _output_hash(job, smoke_packets.name, label="smoke job manifest"), label="smoke packets")
    _require_hash(smoke_packet_manifest, _output_hash(job, smoke_packet_manifest.name, label="smoke job manifest"), label="smoke packet manifest")
    _require_hash(requests, _output_hash(job, requests.name, label="smoke job manifest"), label="smoke requests")
    route = job.get("route") or {}
    route_id = route.get("route_id")
    if (
        not isinstance(route_id, str)
        or route.get("load_in_4bit") is not True
        or route.get("parameter_count_billions") is None
        or float(route["parameter_count_billions"]) >= 14.7
        or route.get("packet_ids") != selected_ids
    ):
        raise ValueError("smoke route is not the declared sub-14.7B 4-bit packet route")

    token_preflight_summary: dict[str, Any] | None = None
    if job.get("protocol") == PILOT_PROTOCOL:
        if token_preflight is None:
            raise ValueError("pilot audit requires the exact token preflight artifact")
        _require_hash(
            token_preflight,
            _output_hash(job, token_preflight.name, label="pilot job manifest"),
            label="pilot token preflight",
        )
        preflight = _json(token_preflight)
        if (
            preflight.get("protocol") != PILOT_PROTOCOL
            or preflight.get("route_id") != route_id
            or preflight.get("max_input_tokens") != route.get("max_input_tokens")
            or preflight.get("training_eligible") is not False
            or preflight.get("certification_allowed") is not False
        ):
            raise ValueError("pilot token preflight does not bind the declared route budget")
        tokenizer = preflight.get("tokenizer") or {}
        if (
            tokenizer.get("model_id") != route.get("model_id")
            or tokenizer.get("revision") != route.get("revision")
            or tokenizer.get("local_files_only") is not True
            or not isinstance(tokenizer.get("chat_template_sha256"), str)
        ):
            raise ValueError("pilot token preflight does not bind the exact route tokenizer")
        preflight_rows = preflight.get("results")
        if not isinstance(preflight_rows, list):
            raise ValueError("pilot token preflight has no packet measurements")
        preflight_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in preflight_rows if isinstance(row, Mapping)}
        if not preflight_by_id or len(preflight_by_id) != len(preflight_rows):
            raise ValueError("pilot token preflight packet identities are invalid")
        selected_counts = ((smoke_manifest_json.get("selection") or {}).get("selected_input_token_counts"))
        if not isinstance(selected_counts, Mapping) or set(selected_counts) != set(selected_ids):
            raise ValueError("pilot packet manifest lacks selected token counts")
        if ((smoke_manifest_json.get("selection") or {}).get("token_preflight_sha256")) != sha256_file(token_preflight):
            raise ValueError("pilot packet manifest does not bind the token preflight")
        for packet_id in selected_ids:
            entry = preflight_by_id.get(packet_id)
            if (
                not isinstance(entry, Mapping)
                or entry.get("within_max_input_tokens") is not True
                or entry.get("exclusion_reason") is not None
                or not isinstance(entry.get("input_token_count"), int)
                or entry["input_token_count"] > route.get("max_input_tokens")
                or selected_counts.get(packet_id) != entry["input_token_count"]
            ):
                raise ValueError("pilot selected packet violates its exact input-token preflight")
        token_preflight_summary = {
            "sha256": sha256_file(token_preflight),
            "max_input_tokens": route.get("max_input_tokens"),
            "selected_input_token_counts": {packet_id: preflight_by_id[packet_id]["input_token_count"] for packet_id in selected_ids},
        }
    elif token_preflight is not None:
        raise ValueError("token preflight is only valid for a bounded pilot audit")

    expected_requests = _jsonl(requests)
    request_ids = {str(row.get("component_selection_packet_id") or "") for row in expected_requests}
    if request_ids != set(selected_ids) or len(request_ids) != len(expected_requests):
        raise ValueError("smoke request coverage does not match selected packet identities")

    raw_dir = artifact_dir / "ccl_phase5_component_selection_smoke_raw"
    validated_dir = artifact_dir / "ccl_phase5_component_selection_smoke_validated"
    raw_responses = raw_dir / "component_selection_raw_responses_v1.jsonl"
    execution_manifest = raw_dir / "component_selection_model_execution_manifest.json"
    validation_manifest = validated_dir / "phase5_component_selection_validation_manifest.json"
    results_path = validated_dir / "phase5_component_selection_results_v1.jsonl"
    receipt_path = artifact_dir / "ccl_phase5_component_selection_kaggle_receipt_v1.json"
    required_outputs = [raw_responses, execution_manifest, validation_manifest, results_path, receipt_path]
    if any(not path.is_file() for path in required_outputs):
        missing = [str(path) for path in required_outputs if not path.is_file()]
        raise FileNotFoundError(f"missing downloaded GPU artifact: {missing}")

    execution = _json(execution_manifest)
    if execution.get("protocol") != job.get("protocol") or execution.get("run_status") != "component_selection_model_execution_complete_responses_unvalidated":
        raise ValueError("model execution did not complete")
    _require_non_promotable(execution, label="execution manifest")
    if execution.get("route") != route:
        raise ValueError("execution route differs from the smoke job")
    _require_hash(smoke_job_manifest, _input_hash(execution, "job_manifest", label="execution manifest"), label="execution job manifest")
    _require_hash(requests, _input_hash(execution, "requests", label="execution manifest"), label="execution requests")
    _require_hash(raw_responses, _output_hash(execution, raw_responses.name, label="execution manifest"), label="raw responses")
    raw_rows = _jsonl(raw_responses)
    raw_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in raw_rows}
    if set(raw_by_id) != set(selected_ids) or len(raw_by_id) != len(raw_rows):
        raise ValueError("raw response coverage does not match selected packet identities")
    if any(set(row) != {"component_selection_packet_id", "response"} or not isinstance(row.get("response"), str) for row in raw_rows):
        raise ValueError("raw response envelope is invalid")

    validation = _json(validation_manifest)
    if (
        validation.get("protocol") != SOURCE_PROTOCOL
        or validation.get("run_status") != "phase_5_component_selection_validation_complete_not_certified"
        or validation.get("route_id") != route_id
        or validation.get("input_hashes_unchanged") is not True
    ):
        raise ValueError("response validation manifest is unsupported")
    _require_non_promotable(validation, label="response validation manifest")
    _require_hash(smoke_packets, _input_hash(validation, smoke_packets.name, label="validation manifest"), label="validation packets")
    _require_hash(smoke_packet_manifest, _input_hash(validation, smoke_packet_manifest.name, label="validation manifest"), label="validation packet manifest")
    _require_hash(raw_responses, _input_hash(validation, raw_responses.name, label="validation manifest"), label="validation raw responses")
    _require_hash(results_path, _output_hash(validation, results_path.name, label="validation manifest"), label="validated results")

    results = _jsonl(results_path)
    result_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in results}
    if set(result_by_id) != set(selected_ids) or len(result_by_id) != len(results):
        raise ValueError("validated result coverage does not match selected packet identities")
    if any(
        row.get("status") not in VALID_STATUSES
        or row.get("training_eligible") is not False
        or row.get("certification_allowed") is not False
        for row in results
    ):
        raise ValueError("validated result violates its restricted contract")

    source_bundle = _json(source_bundle_manifest)
    source_tree_sha256 = ((source_bundle.get("source_bundle") or {}).get("source_tree_sha256"))
    if not isinstance(source_tree_sha256, str):
        raise ValueError("source bundle manifest lacks its source tree hash")
    receipt = _json(receipt_path)
    _require_non_promotable(receipt, label="Kaggle receipt")
    if (
        receipt.get("job_manifest_sha256") != sha256_file(smoke_job_manifest)
        or receipt.get("source_tree_sha256") != source_tree_sha256
        or receipt.get("execution_report_sha256") != sha256_file(raw_dir / "component_selection_model_execution_report.json")
        or receipt.get("validation_manifest_sha256") != sha256_file(validation_manifest)
    ):
        raise ValueError("Kaggle receipt provenance does not match the downloaded output")
    gpu = receipt.get("gpu") or {}
    runtime = receipt.get("model_runtime") or {}
    architecture = f"sm_{runtime.get('gpu_compute_capability', [None, None])[0]}{runtime.get('gpu_compute_capability', [None, None])[1]}"
    if (
        not isinstance(gpu.get("vram_gib"), (int, float))
        or float(gpu["vram_gib"]) < 12.0
        or runtime.get("cuda_available") is not True
        or architecture not in set(runtime.get("torch_arch_list") or [])
    ):
        raise ValueError("Kaggle runtime is not proven compatible with the recorded GPU route")
    receipt_counts = receipt.get("validation_status_counts")
    status_counts = dict(sorted(Counter(str(row["status"]) for row in results).items()))
    if receipt_counts != status_counts:
        raise ValueError("Kaggle receipt validation counts do not match validated results")

    literal_audit: list[dict[str, Any]] = []
    for packet_id in sorted(selected_ids):
        result = result_by_id[packet_id]
        selection = result.get("selection")
        if result["status"] == "INVALID_UNRESOLVED":
            if selection is not None:
                raise ValueError("invalid result unexpectedly includes a selection")
            literal_audit.append({"component_selection_packet_id": packet_id, "status": result["status"], "selection_kind": "invalid"})
            continue
        if not isinstance(selection, Mapping):
            raise ValueError("valid result lacks a closed-world selection")
        literal_audit.append(
            {
                "component_selection_packet_id": packet_id,
                "internal_table_uid": result.get("internal_table_uid"),
                "status": result["status"],
                **_component_summary(smoke_by_id[packet_id], selection),
            }
        )

    result = {
        "schema_version": 1,
        "protocol": job_contract[3],
        "audit_passed": True,
        "route": {
            "route_id": route_id,
            "model_id": route.get("model_id"),
            "parameter_count_billions": route.get("parameter_count_billions"),
            "load_in_4bit": route.get("load_in_4bit"),
        },
        "input_hashes": {path.name: sha256_file(path) for path in inputs},
        "artifact_hashes": {
            raw_responses.name: sha256_file(raw_responses),
            execution_manifest.name: sha256_file(execution_manifest),
            validation_manifest.name: sha256_file(validation_manifest),
            results_path.name: sha256_file(results_path),
            receipt_path.name: sha256_file(receipt_path),
        },
        "gpu": gpu,
        "counts": {
            "selected_packet_count": len(selected_ids),
            "raw_response_count": len(raw_rows),
            "validated_status_counts": status_counts,
        },
        "token_preflight": token_preflight_summary,
        "numeric_guard": numeric_guard_summary,
        "source_literal_selection_audit": literal_audit,
        "training_eligible": False,
        "certification_allowed": False,
        "next_gate": "independent_model_agreement_and_calibration_before_any_semantic_or_training_use",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-packets", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--smoke-job-manifest", type=Path, required=True)
    parser.add_argument("--smoke-packets", type=Path, required=True)
    parser.add_argument("--smoke-packet-manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--token-preflight", type=Path)
    parser.add_argument("--source-bundle-manifest", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit_component_selection_smoke(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
