#!/usr/bin/env python3
"""Build a hash-bound calibration set without manufacturing independent labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.grounded_critic_protocol import source_contract, validate_critic_response


GPU_RUN_AUDIT_PROTOCOL = "grounded_critic_gpu_run_audit_v2"
INDEPENDENT_ASSIGNMENT_PROTOCOL = "grounded_critic_independent_review_assignment_v1"
AI_SOURCE_REVIEW_PROTOCOL = "grounded_critic_independent_ai_source_review_v1"
HUMAN_REVIEW_PROTOCOL = "grounded_critic_independent_human_review_v1"
BLIND_PACKET_FIELDS = (
    "schema_version",
    "protocol",
    "question_id",
    "question_context",
    "execution_status",
    "deterministic_execution_trace",
    "bounded_source_excerpts",
    "allowed_decisions",
    "allowed_reason_codes",
    "allowed_packet_evidence_ids",
    "source_contract",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def blind_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Match the intentionally reduced immutable packet seen by reviewers."""
    return {key: packet.get(key) for key in BLIND_PACKET_FIELDS}


def require_optional_source_bundle_provenance(value: object) -> None:
    """Validate operational code binding when a critic cohort declares one.

    Older V2 cohorts did not record code provenance and remain audit-only under
    the existing backward-compatible path.  A newer cohort that declares a
    bundle must provide all three cryptographic bindings; none grants evidence,
    training, submission, or promotion eligibility.
    """
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("Critic result manifest source bundle provenance is malformed")
    manifest = value.get("manifest") or {}
    archive = value.get("archive") or {}
    for label, record in (("manifest", manifest), ("archive", archive)):
        digest = record.get("sha256") if isinstance(record, dict) else None
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Critic result manifest source bundle {label} SHA-256 is invalid")
    tree_sha = value.get("source_tree_sha256")
    if not isinstance(tree_sha, str) or len(tree_sha) != 64:
        raise ValueError("Critic result manifest source bundle tree SHA-256 is invalid")


def require_source_bound_gpu_audit(
    *,
    result_manifest: dict[str, Any],
    packets: Path,
    packets_manifest: Path,
    results: Path,
    results_manifest: Path,
    audit_path: Path | None,
) -> dict[str, str] | None:
    """Require an independently generated audit for a source-bound GPU cohort.

    A source bundle binds executable code to a cohort, but it does not prove
    that the reported rows were actually produced by the audited GPU run.  For
    any manifest that declares such a bundle, require an audit whose immutable
    input hashes and code identity exactly match this calibration invocation.
    The audit remains operational provenance only; it cannot promote a row.
    """
    source_bundle = (result_manifest.get("inputs") or {}).get("source_bundle")
    if source_bundle is None:
        if audit_path is not None:
            raise ValueError("GPU run audit is only valid for a source-bound critic cohort")
        return None
    if audit_path is None:
        raise ValueError("Source-bound critic results require a matching passed GPU run audit")

    audit = load_json(audit_path)
    if audit.get("protocol") != GPU_RUN_AUDIT_PROTOCOL or audit.get("audit_passed") is not True:
        raise ValueError("GPU run audit protocol/pass state is invalid")
    audit_inputs = audit.get("inputs") or {}
    expected_hashes = {
        "packets": sha(packets),
        "packets_manifest": sha(packets_manifest),
        "results": sha(results),
        "results_manifest": sha(results_manifest),
    }
    for key, expected_sha in expected_hashes.items():
        if ((audit_inputs.get(key) or {}).get("sha256")) != expected_sha:
            raise ValueError(f"GPU run audit {key} hash does not bind this calibration cohort")

    audit_bundle = audit_inputs.get("source_bundle") or {}
    declared = audit_bundle.get("declared_input") or {}
    verified = audit_bundle.get("verified") or {}
    for key in ("manifest", "archive"):
        declared_sha = (source_bundle.get(key) or {}).get("sha256")
        if (
            (declared.get(key) or {}).get("sha256") != declared_sha
            or verified.get(f"{key}_sha256") != declared_sha
        ):
            raise ValueError(f"GPU run audit source bundle {key} does not match critic results")
    if (
        verified.get("source_tree_sha256") != source_bundle.get("source_tree_sha256")
        or verified.get("git_revision") != source_bundle.get("git_revision")
    ):
        raise ValueError("GPU run audit source bundle identity does not match critic results")
    if audit.get("source_contract") != source_contract():
        raise ValueError("GPU run audit violates the non-promotable source contract")
    return {"path": str(audit_path), "sha256": sha(audit_path)}


def require_source_bound_independent_labels(
    *,
    result_manifest: Mapping[str, Any],
    packets: Mapping[int, Mapping[str, Any]],
    packets_path: Path,
    packets_manifest_path: Path,
    results_path: Path,
    results_manifest: Path,
    gpu_run_audit: Mapping[str, str] | None,
    labels: Mapping[int, Mapping[str, Any]],
    labels_path: Path | None,
    labels_manifest_path: Path | None,
) -> dict[str, str] | None:
    """Accept source-bound labels only through the blind-review assignment.

    A provenance string alone is never sufficient calibration truth.  A
    source-bound cohort must keep the label file hash-bound to its original
    blank, Qwen-blind assignment and the audit that admitted the GPU cohort.
    This permits independent AI or human review without allowing a caller to
    inject a standalone JSONL as supposed ground truth.
    """
    is_source_bound = ((result_manifest.get("inputs") or {}).get("source_bundle")) is not None
    if not labels:
        if labels_manifest_path is not None:
            raise ValueError("Independent label manifest was supplied without independent labels")
        return None
    if labels_path is None:
        raise ValueError("Independent labels path is required")
    if not is_source_bound:
        if labels_manifest_path is not None:
            raise ValueError("Independent label manifest is only supported for source-bound critic cohorts")
        return None
    if labels_manifest_path is None or gpu_run_audit is None:
        raise ValueError("Source-bound independent labels require a matching label manifest and GPU audit")

    label_manifest = load_json(labels_manifest_path)
    if label_manifest.get("protocol") not in {AI_SOURCE_REVIEW_PROTOCOL, HUMAN_REVIEW_PROTOCOL}:
        raise ValueError("Independent label manifest protocol is unsupported")
    if label_manifest.get("blind_to_qwen_decision") is not True:
        raise ValueError("Independent label manifest is not blind to Qwen decisions")
    reviewer_slot = label_manifest.get("reviewer_slot")
    if reviewer_slot not in {"reviewer_a", "reviewer_b"}:
        raise ValueError("Independent label manifest reviewer slot is invalid")
    manifest_outputs = label_manifest.get("outputs") or {}
    require_hash(labels_path, (manifest_outputs.get("labels") or {}).get("sha256"), "independent labels")
    counts = label_manifest.get("counts") or {}
    if counts.get("label_count") != len(labels):
        raise ValueError("Independent label manifest count does not cover label rows")
    require_optional_source_bundle_provenance(result_manifest.get("inputs", {}).get("source_bundle"))
    require_contract = label_manifest.get("source_contract")
    if require_contract != source_contract():
        raise ValueError("Independent label manifest violates the non-promotable source contract")

    label_inputs = label_manifest.get("inputs") or {}
    assignment_path = Path(str((label_inputs.get("assignment") or {}).get("path") or ""))
    assignment_manifest_path = Path(str((label_inputs.get("assignment_manifest") or {}).get("path") or ""))
    if not assignment_path.is_file() or not assignment_manifest_path.is_file():
        raise ValueError("Independent label manifest assignment lineage is missing")
    require_hash(assignment_path, (label_inputs.get("assignment") or {}).get("sha256"), "independent review assignment")
    require_hash(
        assignment_manifest_path,
        (label_inputs.get("assignment_manifest") or {}).get("sha256"),
        "independent review assignment manifest",
    )
    assignment_manifest = load_json(assignment_manifest_path)
    if (
        assignment_manifest.get("protocol") != INDEPENDENT_ASSIGNMENT_PROTOCOL
        or assignment_manifest.get("blind_to_qwen_decision") is not True
        or assignment_manifest.get("labels_prepopulated") is not False
    ):
        raise ValueError("Independent labels do not originate from a blank Qwen-blind assignment")
    assignment_contract = assignment_manifest.get("source_contract")
    if assignment_contract != source_contract():
        raise ValueError("Independent assignment violates the non-promotable source contract")
    output = (assignment_manifest.get("outputs") or {}).get(reviewer_slot) or {}
    require_hash(assignment_path, output.get("assignment_sha256"), "assigned reviewer packet set")
    assignment_inputs = assignment_manifest.get("inputs") or {}
    expected_assignment_hashes = {
        "packets": sha(packets_path),
        "packets_manifest": sha(packets_manifest_path),
        "critic_results": sha(results_path),
        "critic_results_manifest": sha(results_manifest),
        "gpu_run_audit": gpu_run_audit["sha256"],
    }
    for key, expected in expected_assignment_hashes.items():
        if ((assignment_inputs.get(key) or {}).get("sha256")) != expected:
            raise ValueError(f"Independent assignment {key} is not bound to this audited critic cohort")

    assignment_rows = load_jsonl(assignment_path)
    assignments: dict[int, Mapping[str, Any]] = {}
    for assignment in assignment_rows:
        question_id = assignment.get("question_id")
        if type(question_id) is not int or question_id in assignments:
            raise ValueError("Independent assignment has missing or duplicate question IDs")
        packet = assignment.get("packet") or {}
        if (
            assignment.get("protocol") != INDEPENDENT_ASSIGNMENT_PROTOCOL
            or assignment.get("reviewer_slot") != reviewer_slot
            or assignment.get("assignment_id") != f"critic-v2-{reviewer_slot}-q{question_id}"
            or assignment.get("immutable_packet_sha256") != hashlib.sha256(
                json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            or packet.get("question_id") != question_id
        ):
            raise ValueError(f"Q{question_id}: malformed immutable independent assignment")
        if question_id not in packets or packet != blind_packet(packets[question_id]):
            raise ValueError(f"Q{question_id}: independent assignment packet differs from calibration packet")
        assignments[question_id] = assignment
    if set(assignments) != set(packets) or set(labels) != set(assignments):
        raise ValueError("Independent labels must cover exactly the immutable reviewer assignment")
    for question_id, label in labels.items():
        assignment = assignments[question_id]
        if (
            label.get("assignment_id") != assignment.get("assignment_id")
            or label.get("immutable_packet_sha256") != assignment.get("immutable_packet_sha256")
            or label.get("source_coordinates_checked") is not True
        ):
            raise ValueError(f"Q{question_id}: independent label does not bind the blind assignment")
        contract = label.get("source_contract")
        if contract != source_contract():
            raise ValueError(f"Q{question_id}: independent label violates the non-promotable source contract")
    return {"path": str(labels_manifest_path), "sha256": sha(labels_manifest_path)}


def require_closed_world_result_manifest(
    packets: Path,
    packets_manifest: Path,
    results: Path,
    results_manifest: Path,
) -> dict[str, Any]:
    """Require result provenance before Qwen output can enter calibration.

    A hash match of a JSONL file alone is not sufficient: the result manifest
    must also bind the result to this exact packet manifest and record the
    closed-world, non-promotable critic protocol.
    """
    packet_manifest = load_json(packets_manifest)
    packet_sha = require_hash(
        packets,
        ((packet_manifest.get("outputs") or {}).get("packets") or {}).get("sha256"),
        "critic packets",
    )
    result_manifest = load_json(results_manifest)
    if result_manifest.get("protocol") != "grounded_critic_results_v2":
        raise ValueError("Critic result manifest protocol must be grounded_critic_results_v2")
    if result_manifest.get("schema_version") != 2:
        raise ValueError("Critic result manifest schema version must be 2")
    require_hash(
        results,
        ((result_manifest.get("outputs") or {}).get("results") or {}).get("sha256"),
        "critic results",
    )
    inputs = result_manifest.get("inputs") or {}
    if ((inputs.get("packets") or {}).get("sha256") != packet_sha or
            (inputs.get("packets_manifest") or {}).get("sha256") != sha(packets_manifest)):
        raise ValueError("Critic result manifest is not bound to the supplied packet lineage")
    require_optional_source_bundle_provenance(inputs.get("source_bundle"))
    contract = result_manifest.get("source_contract") or {}
    required_contract = source_contract()
    required_permission_keys = tuple(key for key in required_contract if key != "candidate_only")
    if any(contract.get(key) is not required_contract[key] for key in required_permission_keys):
        raise ValueError("Critic result manifest violates the non-promotable source contract")
    # `candidate_only` was added to the protocol after the first V2 Kaggle
    # manifests.  Its absence does not confer any permission, but an explicit
    # false value would weaken the declared closed-world boundary.
    if contract.get("candidate_only") not in {None, True}:
        raise ValueError("Critic result manifest violates the candidate-only source contract")
    return result_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--critic-packets", type=Path, required=True)
    parser.add_argument("--critic-packets-manifest", type=Path, required=True)
    parser.add_argument("--critic-results", type=Path, required=True)
    parser.add_argument("--critic-results-manifest", type=Path, required=True)
    parser.add_argument("--gpu-run-audit", type=Path)
    parser.add_argument("--independent-labels", type=Path)
    parser.add_argument("--independent-labels-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite grounded-critic calibration output: {args.output_dir}")
    result_manifest = require_closed_world_result_manifest(
        args.critic_packets,
        args.critic_packets_manifest,
        args.critic_results,
        args.critic_results_manifest,
    )
    gpu_run_audit = require_source_bound_gpu_audit(
        result_manifest=result_manifest,
        packets=args.critic_packets,
        packets_manifest=args.critic_packets_manifest,
        results=args.critic_results,
        results_manifest=args.critic_results_manifest,
        audit_path=args.gpu_run_audit,
    )
    packet_rows = load_jsonl(args.critic_packets)
    packets = {int(row["question_id"]): row for row in packet_rows}
    result_rows = load_jsonl(args.critic_results)
    results = {int(row["question_id"]): row for row in result_rows}
    if len(packets) != len(packet_rows) or len(results) != len(result_rows):
        raise ValueError("Critic packet/result question IDs must be unique")
    if set(packets) != set(results):
        raise ValueError("Critic packet/result ID mismatch")
    for question_id, result in results.items():
        original_response = {key: value for key, value in result.items() if key != "source_contract"}
        validated = validate_critic_response(packets[question_id], original_response)
        if validated != result:
            raise ValueError(f"Q{question_id}: critic result is not a closed-world validated response")
    counts = result_manifest.get("counts") or {}
    if any(counts.get(key) != len(packets) for key in ("packet_count", "result_count", "schema_valid_count", "machine_provisional_count")):
        raise ValueError("Critic result manifest counts do not cover every calibration packet")
    if any(counts.get(key) != 0 for key in (
        "invalid_schema_count",
        "external_evidence_reference_count",
        "numeric_invention_count",
        "candidate_selection_count",
    )):
        raise ValueError("Critic result manifest records a closed-world violation")

    label_rows = load_jsonl(args.independent_labels) if args.independent_labels else []
    labels = {int(row["question_id"]): row for row in label_rows}
    if len(labels) != len(label_rows):
        raise ValueError("Independent calibration labels must have unique question IDs")
    for label in labels.values():
        if label.get("provenance") not in {"human_verified", "independent_ai_source_review"}:
            raise ValueError("Calibration labels must be independently reviewed")
    independent_labels_manifest = require_source_bound_independent_labels(
        result_manifest=result_manifest,
        packets=packets,
        packets_path=args.critic_packets,
        packets_manifest_path=args.critic_packets_manifest,
        results_path=args.critic_results,
        results_manifest=args.critic_results_manifest,
        gpu_run_audit=gpu_run_audit,
        labels=labels,
        labels_path=args.independent_labels,
        labels_manifest_path=args.independent_labels_manifest,
    )

    items = []
    for question_id in sorted(packets):
        packet, result = packets[question_id], results[question_id]
        stages = packet.get("typed_stage_plan") or []
        label = labels.get(question_id)
        scope = (packet.get("question_context") or {}).get("scope") or "unknown"
        items.append(
            {
                "schema_version": 1,
                "protocol": "grounded_critic_calibration_v1",
                "question_id": question_id,
                "stratum": {
                    "metric_or_concept": "|".join(str(stage.get("metric_id") or "unknown") for stage in stages) or "unknown",
                    "table_type": "unknown",
                    "sector": "unknown",
                    "scope": scope,
                    "period_type": "unknown",
                    "evidence_state": packet.get("execution_status"),
                    "source_quality": "not_assessed",
                    "stage_shape": "multi_stage" if len(stages) > 1 else "single_stage",
                    "critic_status": result.get("status"),
                },
                "critic_result": result,
                "independent_label": label,
                "label_provenance": label.get("provenance") if label else "needs_human",
                "calibration_eligible": bool(label),
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "calibration_items_v1.jsonl"
    output.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in items),
        encoding="utf-8",
    )
    manifest = {
        "protocol": "grounded_critic_calibration_v1",
        "inputs": {
            "critic_packets": {"path": str(args.critic_packets), "sha256": sha(args.critic_packets)},
            "critic_packets_manifest": {"path": str(args.critic_packets_manifest), "sha256": sha(args.critic_packets_manifest)},
            "critic_results": {"path": str(args.critic_results), "sha256": sha(args.critic_results)},
            "critic_results_manifest": {"path": str(args.critic_results_manifest), "sha256": sha(args.critic_results_manifest)},
            "gpu_run_audit": gpu_run_audit,
            "independent_labels": None if not args.independent_labels else {"path": str(args.independent_labels), "sha256": sha(args.independent_labels)},
            "independent_labels_manifest": independent_labels_manifest,
        },
        "outputs": {"items": {"path": str(output), "sha256": sha(output)}},
        "counts": {
            "item_count": len(items),
            "independent_label_count": sum(item["calibration_eligible"] for item in items),
            "needs_human_count": sum(not item["calibration_eligible"] for item in items),
        },
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    manifest_path = args.output_dir / "grounded_critic_calibration_v1.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()
