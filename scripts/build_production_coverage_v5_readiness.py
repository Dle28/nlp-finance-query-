#!/usr/bin/env python3
"""Build a fail-closed V5 production-readiness checkpoint.

This checkpoint joins the independently audited source-bound critic cohort and
the full-corpus abstained-route handoff.  It reports operational readiness
only; it never changes a route, emits a label, promotes provenance, or marks
any answer/submission eligible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "production_coverage_v5_readiness_v1"
GPU_AUDIT_PROTOCOL = "grounded_critic_gpu_run_audit_v2"
CRITIC_ASSIGNMENT_PROTOCOL = "grounded_critic_independent_review_assignment_v1"
ROUTE_QUEUE_PROTOCOL = "route_coverage_adjudication_v1"
ROUTE_ASSIGNMENT_PROTOCOL = "route_coverage_independent_review_assignment_v1"
ROUTE_RECONCILIATION_PROTOCOL = "route_coverage_independent_review_reconciliation_v1"
ROUTE_CONSENSUS_PROTOCOL = "route_coverage_consensus_handoff_v1"
NON_PROMOTABLE_KEYS = (
    "evidence_eligible",
    "training_eligible",
    "submission_eligible",
    "promotion_allowed",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
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


def require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or expected != actual:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_non_promotable_contract(value: object, label: str) -> None:
    if not isinstance(value, Mapping) or any(value.get(key) is not False for key in NON_PROMOTABLE_KEYS):
        raise ValueError(f"{label} violates the non-promotable source contract")


def require_manifest_output(path: Path, manifest: Mapping[str, Any], name: str, label: str) -> str:
    record = (manifest.get("outputs") or {}).get(name) or {}
    return require_hash(path, record.get("sha256"), label)


def require_assignment_outputs(manifest: Mapping[str, Any], protocol: str, label: str) -> int:
    if manifest.get("protocol") != protocol:
        raise ValueError(f"{label} protocol mismatch")
    count = manifest.get("assignment_count_per_reviewer")
    if type(count) is not int or count <= 0:
        raise ValueError(f"{label} assignment count is invalid")
    if manifest.get("labels_prepopulated") is not False:
        raise ValueError(f"{label} must contain blank labels")
    require_non_promotable_contract(manifest.get("source_contract"), label)
    for slot in ("reviewer_a", "reviewer_b"):
        output = (manifest.get("outputs") or {}).get(slot) or {}
        for key in ("assignment", "label_template"):
            path_value = output.get(f"{key}_path")
            if not isinstance(path_value, str):
                raise ValueError(f"{label} {slot} {key} path is missing")
            require_hash(Path(path_value), output.get(f"{key}_sha256"), f"{label} {slot} {key}")
    return count


def require_route_reconciliation(
    *,
    reconciliation: Path | None,
    reconciliation_manifest: Path | None,
    route_ids: set[int],
) -> dict[str, Any] | None:
    """Validate completed route reviews without treating them as a route patch."""
    if reconciliation is None and reconciliation_manifest is None:
        return None
    if reconciliation is None or reconciliation_manifest is None:
        raise ValueError("Route reconciliation and its manifest must be supplied together")
    manifest = load_json(reconciliation_manifest)
    if (
        manifest.get("protocol") != ROUTE_RECONCILIATION_PROTOCOL
        or manifest.get("reconciliation_complete") is not True
        or manifest.get("materialization_allowed") is not False
    ):
        raise ValueError("Route reconciliation is not a complete non-materializable artifact")
    require_non_promotable_contract(manifest.get("source_contract"), "route reconciliation")
    reconciliation_sha = require_manifest_output(
        reconciliation, manifest, "reconciled", "route reconciliation"
    )
    rows = load_jsonl(reconciliation)
    ids = {row.get("question_id") for row in rows}
    if (
        len(rows) != len(ids)
        or ids != route_ids
        or any(row.get("protocol") != ROUTE_RECONCILIATION_PROTOCOL for row in rows)
        or any(row.get("route_status_after_reconciliation") != "abstain" for row in rows)
        or any(row.get("materialization_allowed") is not False for row in rows)
    ):
        raise ValueError("Route reconciliation does not preserve the complete abstained queue")
    counts = manifest.get("counts") or {}
    if counts.get("review_count") != len(rows):
        raise ValueError("Route reconciliation review count mismatch")
    return {
        "path": str(reconciliation),
        "sha256": reconciliation_sha,
        "manifest_path": str(reconciliation_manifest),
        "manifest_sha256": sha256_file(reconciliation_manifest),
        "counts": counts,
    }


def require_route_consensus_handoff(
    *,
    handoff: Path | None,
    handoff_manifest: Path | None,
    reconciliation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate a consensus handoff while preserving materialization=false."""
    if handoff is None and handoff_manifest is None:
        return None
    if handoff is None or handoff_manifest is None or reconciliation is None:
        raise ValueError("Route consensus handoff requires a validated route reconciliation")
    manifest = load_json(handoff_manifest)
    if (
        manifest.get("protocol") != ROUTE_CONSENSUS_PROTOCOL
        or manifest.get("materialization_allowed") is not False
    ):
        raise ValueError("Route consensus handoff is not non-materializable")
    require_non_promotable_contract(manifest.get("source_contract"), "route consensus handoff")
    inputs = manifest.get("inputs") or {}
    if (
        ((inputs.get("reconciliation") or {}).get("sha256")) != reconciliation["sha256"]
        or ((inputs.get("reconciliation_manifest") or {}).get("sha256")) != reconciliation["manifest_sha256"]
    ):
        raise ValueError("Route consensus handoff is not bound to the reconciliation")
    handoff_sha = require_manifest_output(handoff, manifest, "handoff", "route consensus handoff")
    rows = load_jsonl(handoff)
    ids = {row.get("question_id") for row in rows}
    if len(ids) != len(rows):
        raise ValueError("Route consensus handoff has duplicate question IDs")
    for row in rows:
        question_id = row.get("question_id")
        if (
            row.get("protocol") != ROUTE_CONSENSUS_PROTOCOL
            or row.get("handoff_state") != "requires_source_bound_validation"
            or row.get("route_status") != "abstain"
            or row.get("materialization_allowed") is not False
            or not isinstance(row.get("consensus_proposal"), dict)
        ):
            raise ValueError("Route consensus handoff violates the abstain-only contract")
        proposal_sha = canonical_sha256(row["consensus_proposal"])
        proposal_hashes = row.get("reviewer_proposal_sha256") or {}
        if (
            row.get("consensus_proposal_sha256") != proposal_sha
            or proposal_hashes.get("reviewer_a") != proposal_sha
            or proposal_hashes.get("reviewer_b") != proposal_sha
        ):
            raise ValueError(f"Q{question_id}: route consensus proposal hashes are invalid")
        source_coordinates = row.get("consensus_source_coordinates_checked")
        if not isinstance(source_coordinates, list) or not source_coordinates:
            raise ValueError(f"Q{question_id}: route consensus lacks source coordinates")
        for coordinate in source_coordinates:
            allowed = {"source_locator", "document_id", "page_no", "section", "notes"}
            if (
                not isinstance(coordinate, dict)
                or set(coordinate).difference(allowed)
                or not isinstance(coordinate.get("source_locator"), str)
                or not coordinate["source_locator"].strip()
                or (
                    "page_no" in coordinate
                    and (type(coordinate["page_no"]) is not int or coordinate["page_no"] < 1)
                )
            ):
                raise ValueError(f"Q{question_id}: route consensus source coordinate is invalid")
        source_sha = canonical_sha256({"source_coordinates_checked": source_coordinates})
        source_hashes = row.get("reviewer_source_coordinates_sha256") or {}
        if (
            row.get("consensus_source_coordinates_sha256") != source_sha
            or source_hashes.get("reviewer_a") != source_sha
            or source_hashes.get("reviewer_b") != source_sha
        ):
            raise ValueError(f"Q{question_id}: route consensus source-coordinate hashes are invalid")
    counts = manifest.get("counts") or {}
    if counts.get("consensus_candidate_count") != len(rows):
        raise ValueError("Route consensus handoff count mismatch")
    return {"path": str(handoff), "sha256": handoff_sha, "manifest_path": str(handoff_manifest), "manifest_sha256": sha256_file(handoff_manifest), "count": len(rows)}


def build(
    *,
    critic_packets: Path,
    critic_packets_manifest: Path,
    critic_results: Path,
    critic_results_manifest: Path,
    gpu_run_audit: Path,
    calibration_manifest: Path,
    calibration_score_manifest: Path,
    critic_assignments_manifest: Path,
    route_queue: Path,
    route_queue_manifest: Path,
    route_assignments_manifest: Path,
    output: Path,
    route_reconciliation: Path | None = None,
    route_reconciliation_manifest: Path | None = None,
    route_consensus_handoff: Path | None = None,
    route_consensus_handoff_manifest: Path | None = None,
) -> dict[str, Any]:
    """Verify V5 handoff lineage and write an explicitly non-production checkpoint."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite production-readiness checkpoint: {output}")

    packet_manifest = load_json(critic_packets_manifest)
    packet_sha = require_manifest_output(critic_packets, packet_manifest, "packets", "critic packets")
    result_manifest = load_json(critic_results_manifest)
    if result_manifest.get("protocol") != "grounded_critic_results_v2" or result_manifest.get("schema_version") != 2:
        raise ValueError("Critic result manifest protocol/schema mismatch")
    result_sha = require_manifest_output(critic_results, result_manifest, "results", "critic results")
    result_inputs = result_manifest.get("inputs") or {}
    if (
        ((result_inputs.get("packets") or {}).get("sha256")) != packet_sha
        or ((result_inputs.get("packets_manifest") or {}).get("sha256")) != sha256_file(critic_packets_manifest)
        or result_inputs.get("source_bundle") is None
    ):
        raise ValueError("Critic result manifest is not bound to source-backed packet lineage")
    require_non_promotable_contract(result_manifest.get("source_contract"), "critic result manifest")

    audit = load_json(gpu_run_audit)
    if audit.get("protocol") != GPU_AUDIT_PROTOCOL or audit.get("audit_passed") is not True:
        raise ValueError("GPU run audit did not pass with the required protocol")
    audit_inputs = audit.get("inputs") or {}
    expected_audit_hashes = {
        "packets": packet_sha,
        "packets_manifest": sha256_file(critic_packets_manifest),
        "results": result_sha,
        "results_manifest": sha256_file(critic_results_manifest),
    }
    for key, expected in expected_audit_hashes.items():
        if ((audit_inputs.get(key) or {}).get("sha256")) != expected:
            raise ValueError(f"GPU run audit {key} hash does not bind the V5 cohort")
    if audit_inputs.get("source_bundle") is None:
        raise ValueError("GPU run audit lacks source-bundle verification")
    require_non_promotable_contract(audit.get("source_contract"), "GPU run audit")

    calibration = load_json(calibration_manifest)
    if calibration.get("protocol") != "grounded_critic_calibration_v1":
        raise ValueError("Critic calibration manifest protocol mismatch")
    calibration_inputs = calibration.get("inputs") or {}
    for key, expected in {
        "critic_packets": packet_sha,
        "critic_packets_manifest": sha256_file(critic_packets_manifest),
        "critic_results": result_sha,
        "critic_results_manifest": sha256_file(critic_results_manifest),
        "gpu_run_audit": sha256_file(gpu_run_audit),
    }.items():
        if ((calibration_inputs.get(key) or {}).get("sha256")) != expected:
            raise ValueError(f"Critic calibration {key} hash does not bind the V5 cohort")
    calibration_items_path = Path(((calibration.get("outputs") or {}).get("items") or {}).get("path", ""))
    if not calibration_items_path.is_file():
        raise ValueError("Critic calibration items output is missing")
    calibration_items_sha = require_manifest_output(
        calibration_items_path, calibration, "items", "critic calibration items"
    )
    calibration_counts = calibration.get("counts") or {}
    independent_label_count = calibration_counts.get("independent_label_count")
    needs_human_count = calibration_counts.get("needs_human_count")
    if (
        calibration_counts.get("item_count") != len(load_jsonl(critic_packets))
        or type(independent_label_count) is not int
        or type(needs_human_count) is not int
        or independent_label_count + needs_human_count != calibration_counts.get("item_count")
    ):
        raise ValueError("Critic calibration coverage/counts are invalid")
    require_non_promotable_contract(calibration.get("source_contract"), "critic calibration")

    score_manifest = load_json(calibration_score_manifest)
    if score_manifest.get("protocol") != "grounded_critic_calibration_v1":
        raise ValueError("Critic calibration score manifest protocol mismatch")
    score_inputs = score_manifest.get("inputs") or {}
    if (
        ((score_inputs.get("items") or {}).get("sha256")) != calibration_items_sha
        or ((score_inputs.get("items_manifest") or {}).get("sha256")) != sha256_file(calibration_manifest)
    ):
        raise ValueError("Critic calibration score is not bound to audited calibration items")
    unresolved_path = Path(((score_manifest.get("outputs") or {}).get("unresolved") or {}).get("path", ""))
    unresolved_sha = require_manifest_output(unresolved_path, score_manifest, "unresolved", "critic unresolved queue")
    score_counts = score_manifest.get("counts") or {}
    if score_counts.get("item_count") != calibration_counts.get("item_count"):
        raise ValueError("Critic calibration score does not cover the full calibration cohort")
    if (score_manifest.get("outputs") or {}).get("promoted", {}).get("sha256") != sha256_file(
        Path(((score_manifest.get("outputs") or {}).get("promoted") or {}).get("path", ""))
    ):
        raise ValueError("Critic calibration promoted output hash mismatch")
    require_non_promotable_contract(score_manifest.get("source_contract"), "critic calibration score")

    critic_assignments = load_json(critic_assignments_manifest)
    critic_assignment_count = require_assignment_outputs(
        critic_assignments, CRITIC_ASSIGNMENT_PROTOCOL, "critic independent assignments"
    )
    if critic_assignments.get("blind_to_qwen_decision") is not True:
        raise ValueError("Critic independent assignments must remain blind to Qwen decisions")
    assignment_inputs = critic_assignments.get("inputs") or {}
    for key, expected in {
        "packets": packet_sha,
        "packets_manifest": sha256_file(critic_packets_manifest),
        "critic_results": result_sha,
        "critic_results_manifest": sha256_file(critic_results_manifest),
        "gpu_run_audit": sha256_file(gpu_run_audit),
        "unresolved_queue": unresolved_sha,
    }.items():
        if ((assignment_inputs.get(key) or {}).get("sha256")) != expected:
            raise ValueError(f"Critic independent assignments {key} hash mismatch")
    if critic_assignment_count != calibration_counts.get("item_count"):
        raise ValueError("Critic assignment coverage does not match the calibrated cohort")

    route_manifest = load_json(route_queue_manifest)
    if route_manifest.get("protocol") != ROUTE_QUEUE_PROTOCOL or route_manifest.get("repairs_materialized") is not False:
        raise ValueError("Route-coverage queue manifest is not a non-materialized V1 queue")
    require_non_promotable_contract(route_manifest.get("source_contract"), "route-coverage queue")
    route_queue_sha = require_manifest_output(route_queue, route_manifest, "queue", "route-coverage queue")
    route_rows = load_jsonl(route_queue)
    route_ids = {row.get("question_id") for row in route_rows}
    if not route_rows or len(route_ids) != len(route_rows) or any(row.get("route_status") != "abstain" for row in route_rows):
        raise ValueError("Route-coverage queue must contain unique abstained routes")
    route_counts = route_manifest.get("counts") or {}
    if route_counts.get("abstain_count") != len(route_rows):
        raise ValueError("Route-coverage abstain count mismatch")

    route_assignments = load_json(route_assignments_manifest)
    route_assignment_count = require_assignment_outputs(
        route_assignments, ROUTE_ASSIGNMENT_PROTOCOL, "route independent assignments"
    )
    if (
        route_assignments.get("blind_to_other_review") is not True
        or route_assignments.get("materialization_allowed") is not False
        or ((route_assignments.get("inputs") or {}).get("queue") or {}).get("sha256") != route_queue_sha
        or ((route_assignments.get("inputs") or {}).get("queue_manifest") or {}).get("sha256") != sha256_file(route_queue_manifest)
        or route_assignment_count != len(route_rows)
    ):
        raise ValueError("Route independent assignments do not bind the abstained queue")
    reconciled_routes = require_route_reconciliation(
        reconciliation=route_reconciliation,
        reconciliation_manifest=route_reconciliation_manifest,
        route_ids={int(question_id) for question_id in route_ids},
    )
    consensus_handoff = require_route_consensus_handoff(
        handoff=route_consensus_handoff,
        handoff_manifest=route_consensus_handoff_manifest,
        reconciliation=reconciled_routes,
    )

    route_blockers = (
        [
            {
                "code": "ROUTE_COVERAGE_REVIEW_PENDING",
                "count": len(route_rows),
                "detail": "Full-corpus abstained routes have blank independent review assignments only.",
            },
            {
                "code": "ROUTE_MATERIALIZATION_FORBIDDEN",
                "count": len(route_rows),
                "detail": "No source-bound route materializer has approved any reviewed proposal.",
            },
        ]
        if reconciled_routes is None
        else [
            {
                "code": "ROUTE_SOURCE_VALIDATION_PENDING",
                "count": consensus_handoff["count"] if consensus_handoff is not None else 0,
                "detail": "Reconciled route proposals require a separate source-bound upstream validator before any route change.",
            },
            {
                "code": "ROUTE_MATERIALIZATION_FORBIDDEN",
                "count": len(route_rows),
                "detail": "Reconciliation and consensus handoff preserve every original route as abstain.",
            },
        ]
    )

    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "readiness_status": "blocked",
        "production_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "blockers": [
            *(
                [{
                    "code": "CRITIC_INDEPENDENT_REVIEW_PENDING",
                    "count": needs_human_count,
                    "detail": "Audited Qwen outputs remain machine_provisional until independent labels and later policy gates exist.",
                }]
                if needs_human_count
                else []
            ),
            {
                "code": "CRITIC_CALIBRATION_POLICY_PENDING",
                "count": calibration_counts["item_count"],
                "detail": "Independent critic labels alone cannot enable production without the configured calibration-policy gates.",
            },
            *route_blockers,
        ],
        "inputs": {
            "critic_packets": {"path": str(critic_packets), "sha256": packet_sha},
            "critic_packets_manifest": {"path": str(critic_packets_manifest), "sha256": sha256_file(critic_packets_manifest)},
            "critic_results": {"path": str(critic_results), "sha256": result_sha},
            "critic_results_manifest": {"path": str(critic_results_manifest), "sha256": sha256_file(critic_results_manifest)},
            "gpu_run_audit": {"path": str(gpu_run_audit), "sha256": sha256_file(gpu_run_audit)},
            "calibration_manifest": {"path": str(calibration_manifest), "sha256": sha256_file(calibration_manifest)},
            "calibration_score_manifest": {"path": str(calibration_score_manifest), "sha256": sha256_file(calibration_score_manifest)},
            "critic_assignments_manifest": {"path": str(critic_assignments_manifest), "sha256": sha256_file(critic_assignments_manifest)},
            "route_queue": {"path": str(route_queue), "sha256": route_queue_sha},
            "route_queue_manifest": {"path": str(route_queue_manifest), "sha256": sha256_file(route_queue_manifest)},
            "route_assignments_manifest": {"path": str(route_assignments_manifest), "sha256": sha256_file(route_assignments_manifest)},
            "route_reconciliation": reconciled_routes,
            "route_consensus_handoff": consensus_handoff,
        },
        "counts": {
            "audited_critic_packet_count": calibration_counts["item_count"],
            "critic_independent_label_count": independent_label_count,
            "critic_needs_human_count": needs_human_count,
            "critic_unresolved_count": score_counts.get("unresolved_count"),
            "route_abstain_count": len(route_rows),
            "route_assignment_count_per_reviewer": route_assignment_count,
            "route_reconciliation_count": None if reconciled_routes is None else reconciled_routes["counts"].get("review_count"),
            "route_consensus_candidate_count": None if consensus_handoff is None else consensus_handoff["count"],
        },
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "critic-packets", "critic-packets-manifest", "critic-results", "critic-results-manifest",
        "gpu-run-audit", "calibration-manifest", "calibration-score-manifest",
        "critic-assignments-manifest", "route-queue", "route-queue-manifest",
        "route-assignments-manifest", "route-reconciliation", "route-reconciliation-manifest",
        "route-consensus-handoff", "route-consensus-handoff-manifest", "output",
    ):
        parser.add_argument(f"--{name}", type=Path, required=name == "output" or name not in {
            "route-reconciliation", "route-reconciliation-manifest", "route-consensus-handoff", "route-consensus-handoff-manifest",
        })
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build(**{key.replace("-", "_"): value for key, value in vars(args).items()})
    print(result["readiness_status"])


if __name__ == "__main__":
    main()
