#!/usr/bin/env python3
"""Create blind, hash-bound independent review assignments for critic V2."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.grounded_critic_protocol import source_contract


PROTOCOL = "grounded_critic_independent_review_assignment_v1"
LABEL_PROTOCOL = "grounded_critic_independent_label_v1"
GPU_RUN_AUDIT_PROTOCOL = "grounded_critic_gpu_run_audit_v2"
SLOTS = ("reviewer_a", "reviewer_b")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_source_bound_gpu_audit(
    *,
    result_manifest: Mapping[str, Any],
    packets: Path,
    packets_manifest: Path,
    results: Path,
    results_manifest: Path,
    audit_path: Path | None,
) -> dict[str, str] | None:
    """Bind a source-backed assignment to the audit that admitted its cohort.

    The assignments remain blind to model decisions and non-materializable,
    but they must not lose the operational provenance gate that protected the
    source-bound results before they entered calibration.
    """
    source_bundle = (result_manifest.get("inputs") or {}).get("source_bundle")
    if source_bundle is None:
        if audit_path is not None:
            raise ValueError("GPU run audit is only valid for a source-bound critic cohort")
        return None
    if audit_path is None:
        raise ValueError("Source-bound critic assignments require a matching passed GPU run audit")
    audit = load_json(audit_path)
    if audit.get("protocol") != GPU_RUN_AUDIT_PROTOCOL or audit.get("audit_passed") is not True:
        raise ValueError("GPU run audit protocol/pass state is invalid")
    audit_inputs = audit.get("inputs") or {}
    expected_hashes = {
        "packets": sha256_file(packets),
        "packets_manifest": sha256_file(packets_manifest),
        "results": sha256_file(results),
        "results_manifest": sha256_file(results_manifest),
    }
    for key, expected_sha in expected_hashes.items():
        if ((audit_inputs.get(key) or {}).get("sha256")) != expected_sha:
            raise ValueError(f"GPU run audit {key} hash does not bind this reviewer cohort")
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
    return {"path": str(audit_path), "sha256": sha256_file(audit_path)}


def blind_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return only closed-world evidence, deliberately excluding Qwen output."""
    return {
        key: packet.get(key)
        for key in (
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
    }


def assignment_key(slot: str, question_id: int) -> str:
    return hashlib.sha256(f"{slot}|{question_id}".encode("utf-8")).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packets-manifest", type=Path, required=True)
    parser.add_argument("--critic-results", type=Path, required=True)
    parser.add_argument("--critic-results-manifest", type=Path, required=True)
    parser.add_argument("--gpu-run-audit", type=Path)
    parser.add_argument("--unresolved-queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    packet_manifest = load_json(args.packets_manifest)
    result_manifest = load_json(args.critic_results_manifest)
    packet_sha = require_hash(
        args.packets, ((packet_manifest.get("outputs") or {}).get("packets") or {}).get("sha256"), "critic packets"
    )
    packet_manifest_sha = sha256_file(args.packets_manifest)
    require_hash(
        args.critic_results,
        ((result_manifest.get("outputs") or {}).get("results") or {}).get("sha256"),
        "critic results",
    )
    manifest_inputs = result_manifest.get("inputs") or {}
    if ((manifest_inputs.get("packets") or {}).get("sha256") != packet_sha or
            (manifest_inputs.get("packets_manifest") or {}).get("sha256") != packet_manifest_sha):
        raise ValueError("Critic result manifest is not bound to the supplied packet lineage")
    gpu_run_audit = require_source_bound_gpu_audit(
        result_manifest=result_manifest,
        packets=args.packets,
        packets_manifest=args.packets_manifest,
        results=args.critic_results,
        results_manifest=args.critic_results_manifest,
        audit_path=args.gpu_run_audit,
    )

    packets = {int(row["question_id"]): row for row in load_jsonl(args.packets)}
    results = {int(row["question_id"]): row for row in load_jsonl(args.critic_results)}
    unresolved = {int(row["question_id"]): row for row in load_jsonl(args.unresolved_queue)}
    if len(packets) != len(load_jsonl(args.packets)) or set(packets) != set(results):
        raise ValueError("Critic packet/result ID coverage mismatch")
    if set(unresolved) != set(packets):
        raise ValueError("Unresolved queue ID coverage mismatch")
    for question_id, result in results.items():
        if result.get("provenance") != "machine_provisional":
            raise ValueError(f"Q{question_id}: only machine_provisional critic output may enter this assignment")
        queue_row = unresolved[question_id]
        if queue_row.get("reason") != "INSUFFICIENT_INDEPENDENT_CALIBRATION":
            raise ValueError(f"Q{question_id}: unresolved queue reason is not calibration insufficiency")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, str]] = {}
    review_instructions = [
        "Reopen the immutable source table at every bounded evidence coordinate.",
        "Decide accept, reject, or abstain only for support of the deterministic trace.",
        "Do not compute or write an answer, value, formula, candidate, or repair.",
        "Do not inspect the Qwen result or another reviewer label before submitting your label.",
        "Use independent_ai_source_review only for a genuinely independent source review; use human_verified only for a human review.",
    ]
    for slot in SLOTS:
        ordered_ids = sorted(packets, key=lambda question_id: assignment_key(slot, question_id))
        assignments: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        for position, question_id in enumerate(ordered_ids, start=1):
            packet = blind_packet(packets[question_id])
            assignment_id = f"critic-v2-{slot}-q{question_id}"
            assignment = {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "assignment_id": assignment_id,
                "reviewer_slot": slot,
                "assignment_position": position,
                "question_id": question_id,
                "immutable_packet_sha256": canonical_sha256(packet),
                "packet": packet,
                "review_instructions": review_instructions,
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                    "may_select_final_candidate": False,
                    "may_select_value": False,
                    "may_execute_formula": False,
                },
            }
            assignments.append(assignment)
            templates.append(
                {
                    "schema_version": 1,
                    "protocol": LABEL_PROTOCOL,
                    "assignment_id": assignment_id,
                    "question_id": question_id,
                    "immutable_packet_sha256": assignment["immutable_packet_sha256"],
                    "status": None,
                    "provenance": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_coordinates_checked": None,
                    "source_coordinate_agree": None,
                    "unit_period_agree": None,
                    "deterministic_replay_agree": None,
                    "unsupported_evidence": None,
                    "notes": "",
                    "is_blank_template": True,
                    "source_contract": assignment["source_contract"],
                }
            )
        assignment_path = args.output_dir / f"grounded_critic_{slot}_assignment_v1.jsonl"
        template_path = args.output_dir / f"grounded_critic_{slot}_label_template_v1.jsonl"
        write_jsonl(assignment_path, assignments)
        write_jsonl(template_path, templates)
        outputs[slot] = {
            "assignment_path": str(assignment_path),
            "assignment_sha256": sha256_file(assignment_path),
            "label_template_path": str(template_path),
            "label_template_sha256": sha256_file(template_path),
        }

    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "reviewer_slots": list(SLOTS),
        "assignment_count_per_reviewer": len(packets),
        "blind_to_qwen_decision": True,
        "labels_prepopulated": False,
        "inputs": {
            "packets": {"path": str(args.packets), "sha256": packet_sha},
            "packets_manifest": {"path": str(args.packets_manifest), "sha256": packet_manifest_sha},
            "critic_results": {"path": str(args.critic_results), "sha256": sha256_file(args.critic_results)},
            "critic_results_manifest": {"path": str(args.critic_results_manifest), "sha256": sha256_file(args.critic_results_manifest)},
            "gpu_run_audit": gpu_run_audit,
            "unresolved_queue": {"path": str(args.unresolved_queue), "sha256": sha256_file(args.unresolved_queue)},
        },
        "outputs": outputs,
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    manifest_path = args.output_dir / "grounded_critic_independent_review_assignments_v1.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()
