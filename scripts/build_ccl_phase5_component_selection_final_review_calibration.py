#!/usr/bin/env python3
"""Build a blind, hash-bound final-review calibration package for Phase 5.

This turns a two-model component-selection agreement into a small human review
assignment.  It deliberately does not copy a model decision into the reviewer
packet and cannot create a semantic label, a training example, or a release
certificate.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
SOURCE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_v1"
AGREEMENT_PROTOCOL = "ccl_phase5_component_selection_agreement_v1"
AUDIT_PROTOCOL = "ccl_phase5_component_selection_smoke_audit_v1"
PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_calibration_v1"
RESPONSE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_response_v1"
ASSIGNMENT_NAME = "component_selection_final_review_assignment_v1.jsonl"
RESPONSE_TEMPLATE_NAME = "component_selection_final_review_response_template_v1.jsonl"
LEDGER_NAME = "component_selection_model_comparison_ledger_v1.jsonl"
REPORT_NAME = "component_selection_final_review_calibration_report.json"
MANIFEST_NAME = "component_selection_final_review_calibration_manifest.json"
NAVIGATION_INPUT_NAMES = frozenset(
    {"report_navigation_overlay_v1.jsonl", "report_navigation_overlay_manifest.json"}
)
SAFE_AGREEMENT_STRATA = {
    "EXACT_CLOSED_WORLD_AGREEMENT",
    "PRIMARY_CONTEXT_AGREEMENT_SUPPORT_DIFFERENCE",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object: {path}:{line_number}")
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or expected != sha256_file(path):
        raise ValueError(f"SHA-256 mismatch for {label}")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise ValueError(f"{label} is unexpectedly promotable")


def _require_non_promotable_agreement_report(value: Mapping[str, Any]) -> None:
    if value.get("training_eligible_output_count") != 0 or value.get("certification_allowed") is not False:
        raise ValueError("agreement report is unexpectedly promotable")


def _require_source_manifest(*, packets: Path, manifest: Path) -> None:
    payload = _json(manifest)
    if (
        payload.get("protocol") != SOURCE_PROTOCOL
        or payload.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
    ):
        raise ValueError("unsupported source packet manifest")
    _require_non_promotable(payload, label="source packet manifest")
    navigation_inputs = payload.get("inputs") or {}
    if (
        payload.get("navigation_overlay_required") is not True
        or not isinstance(navigation_inputs, Mapping)
        or not NAVIGATION_INPUT_NAMES.issubset(navigation_inputs)
    ):
        raise ValueError("source packet manifest is missing the required navigation overlay")
    _require_hash(
        packets,
        ((payload.get("outputs") or {}).get(packets.name) or {}).get("sha256"),
        label="source packets",
    )


def _require_guarded_audit(*, audit_path: Path, packets: Path, label: str) -> dict[str, Any]:
    audit = _json(audit_path)
    if audit.get("protocol") != AUDIT_PROTOCOL or audit.get("audit_passed") is not True:
        raise ValueError(f"{label} audit did not pass")
    _require_non_promotable(audit, label=f"{label} audit")
    source_hash = (audit.get("input_hashes") or {}).get(packets.name)
    if source_hash != sha256_file(packets):
        raise ValueError(f"{label} audit does not bind the supplied source packets")
    guard = audit.get("numeric_guard") or {}
    if (
        guard.get("applied") is not True
        or guard.get("source_numeric_header_violation_count") != 0
        or not isinstance(guard.get("source_numeric_value_header_exclusion_count"), int)
        or guard["source_numeric_value_header_exclusion_count"] < 0
    ):
        raise ValueError(f"{label} audit does not prove the numeric-header guard")
    route = audit.get("route") or {}
    if not isinstance(route.get("route_id"), str) or not isinstance(route.get("model_id"), str):
        raise ValueError(f"{label} audit route is malformed")
    return audit


def _agreement_input_hash(manifest_inputs: Mapping[str, Any], *, role: str, legacy_name: str) -> object:
    """Read role-bound comparison inputs while accepting older unique basenames."""
    contract = manifest_inputs.get(role)
    if contract is None:
        contract = manifest_inputs.get(legacy_name)
    return (contract or {}).get("sha256") if isinstance(contract, Mapping) else None


def _component_menu(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    components = packet.get("components")
    if not isinstance(components, list):
        raise ValueError("source packet component menu is malformed")
    menu = {str(component.get("component_id") or ""): component for component in components if isinstance(component, Mapping)}
    if not menu or len(menu) != len(components):
        raise ValueError("source packet component IDs are malformed")
    return menu


def _require_model_selection(selection: Mapping[str, Any], menu: Mapping[str, Mapping[str, Any]], *, label: str) -> None:
    if selection.get("result_status") != "VALID_COMPONENT_SELECTION_ONLY":
        raise ValueError(f"{label} is not a valid component selection")
    primary = selection.get("primary_component_id")
    supporting = selection.get("supporting_component_ids")
    unresolved = selection.get("unresolved_conditions")
    if not isinstance(primary, str) or menu.get(primary, {}).get("role") != "report_scope_or_selected_relation_context":
        raise ValueError(f"{label} primary component is not in the closed-world context menu")
    if (
        not isinstance(supporting, list)
        or len(supporting) > 4
        or len(set(supporting)) != len(supporting)
        or not all(isinstance(component_id, str) for component_id in supporting)
        or not isinstance(unresolved, list)
        or not all(isinstance(condition, str) for condition in unresolved)
    ):
        raise ValueError(f"{label} supporting component selection is malformed")
    for component_id in supporting:
        if menu.get(component_id, {}).get("role") not in {"column_header", "row_label"}:
            raise ValueError(f"{label} supporting component is outside the permitted menu")


def _review_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return source evidence needed by a reviewer, never a model selection."""
    fields = (
        "component_selection_packet_id",
        "internal_table_uid",
        "phase3_request_id",
        "phase45_assertion_id",
        "route_id",
        "source_first_semantic_route_id",
        "source_first_route_status",
        "table_structure_context_id",
        "source_quality_status",
        "source_quality_reason_codes",
        "component_menu_stats",
        "components",
        "task",
    )
    return {field: packet.get(field) for field in fields}


def build_final_review_calibration(
    *,
    source_packets: Path,
    source_manifest: Path,
    agreement_results: Path,
    agreement_report: Path,
    agreement_manifest: Path,
    qwen_audit: Path,
    mistral_audit: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a blind final-review assignment from one guarded two-model run."""
    inputs = {
        "source_packets": source_packets.resolve(),
        "source_manifest": source_manifest.resolve(),
        "agreement_results": agreement_results.resolve(),
        "agreement_report": agreement_report.resolve(),
        "agreement_manifest": agreement_manifest.resolve(),
        "qwen_audit": qwen_audit.resolve(),
        "mistral_audit": mistral_audit.resolve(),
    }
    if any(not path.is_file() for path in inputs.values()):
        missing = [str(path) for path in inputs.values() if not path.is_file()]
        raise FileNotFoundError(f"missing calibration input: {missing}")
    if output_dir.exists() or output_dir.resolve() in {path.resolve() for path in inputs.values()}:
        raise FileExistsError("final-review calibration output-dir must be new and distinct from its inputs")
    before_hashes = {name: sha256_file(path) for name, path in inputs.items()}

    _require_source_manifest(packets=inputs["source_packets"], manifest=inputs["source_manifest"])
    qwen = _require_guarded_audit(audit_path=inputs["qwen_audit"], packets=inputs["source_packets"], label="Qwen")
    mistral = _require_guarded_audit(audit_path=inputs["mistral_audit"], packets=inputs["source_packets"], label="Mistral")
    qwen_route = qwen["route"]
    mistral_route = mistral["route"]
    if qwen_route["route_id"] != mistral_route["route_id"] or qwen_route["model_id"] == mistral_route["model_id"]:
        raise ValueError("audits must bind one route and two distinct models")

    manifest = _json(inputs["agreement_manifest"])
    report = _json(inputs["agreement_report"])
    if manifest.get("protocol") != AGREEMENT_PROTOCOL or manifest.get("run_status") != "component_selection_agreement_complete_not_calibrated":
        raise ValueError("agreement manifest is not an incomplete agreement run")
    _require_non_promotable(manifest, label="agreement manifest")
    if report.get("protocol") != AGREEMENT_PROTOCOL or report.get("run_status") != "component_selection_agreement_complete_not_calibrated":
        raise ValueError("agreement report is not an incomplete agreement run")
    _require_non_promotable_agreement_report(report)
    _require_hash(
        inputs["agreement_results"],
        ((manifest.get("outputs") or {}).get(agreement_results.name) or {}).get("sha256"),
        label="agreement results",
    )
    _require_hash(
        inputs["agreement_report"],
        ((manifest.get("outputs") or {}).get(agreement_report.name) or {}).get("sha256"),
        label="agreement report",
    )
    manifest_inputs = manifest.get("inputs") or {}
    _require_hash(
        inputs["qwen_audit"],
        _agreement_input_hash(manifest_inputs, role="primary_audit", legacy_name=qwen_audit.name),
        label="Qwen audit",
    )
    _require_hash(
        inputs["mistral_audit"],
        _agreement_input_hash(manifest_inputs, role="challenger_audit", legacy_name=mistral_audit.name),
        label="Mistral audit",
    )
    if report.get("models") != [qwen_route["model_id"], mistral_route["model_id"]]:
        raise ValueError("agreement report model order does not match the audited runs")

    source_rows = _jsonl(inputs["source_packets"])
    source_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in source_rows}
    if not source_by_id or len(source_by_id) != len(source_rows):
        raise ValueError("source packet identities are malformed")
    agreement_rows = _jsonl(inputs["agreement_results"])
    agreement_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in agreement_rows}
    if not agreement_by_id or len(agreement_by_id) != len(agreement_rows):
        raise ValueError("agreement result identities are malformed")

    assignments: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for packet_id in sorted(agreement_by_id):
        agreement = agreement_by_id[packet_id]
        stratum = agreement.get("agreement_status")
        if stratum not in SAFE_AGREEMENT_STRATA:
            raise ValueError("primary-context disagreement requires a separate quarantine campaign")
        packet = source_by_id.get(packet_id)
        if packet is None or agreement.get("internal_table_uid") != packet.get("internal_table_uid"):
            raise ValueError("agreement row is not bound to a source packet identity")
        _require_non_promotable(agreement, label=f"agreement row {packet_id}")
        menu = _component_menu(packet)
        primary_model = agreement.get("primary_model") or {}
        challenger_model = agreement.get("challenger_model") or {}
        if primary_model.get("model_id") != qwen_route["model_id"] or challenger_model.get("model_id") != mistral_route["model_id"]:
            raise ValueError("agreement row model identity does not match the audited runs")
        _require_model_selection(primary_model, menu, label=f"Qwen agreement row {packet_id}")
        _require_model_selection(challenger_model, menu, label=f"Mistral agreement row {packet_id}")
        review_packet = _review_packet(packet)
        immutable_packet_sha256 = _canonical_sha(review_packet)
        item_identity = {
            "component_selection_packet_id": packet_id,
            "immutable_review_packet_sha256": immutable_packet_sha256,
            "review_stratum": stratum,
        }
        calibration_item_id = _canonical_sha(item_identity)
        assignment = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "calibration_item_id": calibration_item_id,
            "review_stratum": stratum,
            "model_decisions_blinded": True,
            "immutable_review_packet_sha256": immutable_packet_sha256,
            "review_packet": review_packet,
            "review_instructions": {
                "task": "Select source context components only from the immutable menu, or abstain explicitly.",
                "do_not_infer": ["table semantic label", "numeric answer", "formula", "training decision", "release decision"],
                "source_coordinates_must_be_checked": True,
            },
            "response_contract": {
                "allowed_review_decisions": ["SELECT_COMPONENTS", "ABSTAIN_UNRESOLVED"],
                "primary_component_role": "report_scope_or_selected_relation_context",
                "supporting_component_roles": ["column_header", "row_label"],
                "maximum_supporting_component_ids": 4,
                "allowed_unresolved_conditions": [
                    "ambiguous_source_components",
                    "insufficient_source_components",
                    "source_text_damage",
                ],
            },
            "training_eligible": False,
            "certification_allowed": False,
        }
        immutable_assignment_sha256 = _canonical_sha(assignment)
        assignments.append(assignment)
        templates.append(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol": RESPONSE_PROTOCOL,
                "calibration_item_id": calibration_item_id,
                "immutable_assignment_sha256": immutable_assignment_sha256,
                "reviewer_id": None,
                "reviewed_at_utc": None,
                "review_decision": None,
                "primary_component_id": None,
                "supporting_component_ids": [],
                "unresolved_conditions": [],
                "source_coordinates_checked": None,
                "training_eligible": False,
                "certification_allowed": False,
            }
        )
        ledger.append(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol": AGREEMENT_PROTOCOL,
                "calibration_item_id": calibration_item_id,
                "component_selection_packet_id": packet_id,
                "review_stratum": stratum,
                "primary_model": primary_model,
                "challenger_model": challenger_model,
                "supporting_overlap": agreement.get("supporting_overlap"),
                "training_eligible": False,
                "certification_allowed": False,
            }
        )

    after_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    if after_hashes != before_hashes:
        raise ValueError("final-review calibration construction changed a hash-bound input")
    if not assignments:
        raise ValueError("final-review calibration has no safe agreement rows")

    output_dir.mkdir(parents=True)
    assignments_path = output_dir / ASSIGNMENT_NAME
    templates_path = output_dir / RESPONSE_TEMPLATE_NAME
    ledger_path = output_dir / LEDGER_NAME
    _write_jsonl(assignments_path, assignments)
    _write_jsonl(templates_path, templates)
    _write_jsonl(ledger_path, ledger)
    stratum_counts = dict(sorted(Counter(str(row["review_stratum"]) for row in assignments).items()))
    report_payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "run_status": "final_review_calibration_assignment_ready_not_scored",
        "review_assignment_count": len(assignments),
        "review_stratum_counts": stratum_counts,
        "model_decisions_blinded": True,
        "human_review_required": True,
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "complete_hash_bound_final_review_responses_then_measure_error_by_stratum",
    }
    report_path = output_dir / REPORT_NAME
    report_path.write_text(json.dumps(report_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest_payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "run_status": "final_review_calibration_assignment_ready_not_scored",
        "inputs": {name: {"path": str(path), "sha256": before_hashes[name]} for name, path in inputs.items()},
        "outputs": {
            ASSIGNMENT_NAME: {"sha256": sha256_file(assignments_path)},
            RESPONSE_TEMPLATE_NAME: {"sha256": sha256_file(templates_path)},
            LEDGER_NAME: {"sha256": sha256_file(ledger_path)},
            REPORT_NAME: {"sha256": sha256_file(report_path)},
        },
        "model_decisions_blinded": True,
        "human_review_required": True,
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-packets", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--agreement-results", type=Path, required=True)
    parser.add_argument("--agreement-report", type=Path, required=True)
    parser.add_argument("--agreement-manifest", type=Path, required=True)
    parser.add_argument("--qwen-audit", type=Path, required=True)
    parser.add_argument("--mistral-audit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_final_review_calibration(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
