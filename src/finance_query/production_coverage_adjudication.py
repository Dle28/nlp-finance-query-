"""Build a hash-bound, non-promotable queue for coverage binding conflicts."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "production_coverage_binding_adjudication_v1"
EXPECTED_IDS = set(range(1, 1013))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "eligible_for_materialization": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def decision_contract() -> dict[str, Any]:
    return {
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": None,
        "reviewed_at": None,
        "source_coordinates_checked": False,
        "proposed_patch": None,
        "eligible_for_materialization": False,
    }


def require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_ids(rows: list[Mapping[str, Any]], label: str) -> dict[int, dict[str, Any]]:
    indexed = {int(row["question_id"]): dict(row) for row in rows}
    if len(indexed) != len(rows) or set(indexed) != EXPECTED_IDS:
        raise ValueError(f"{label} ID coverage mismatch")
    return indexed


def compact_source(record: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "document_id", "internal_table_uid", "row_index", "column_index", "role",
        "concept_id", "raw_source_row", "raw_source_cell", "cell_provenance",
        "header_source_cells", "period_labels", "unit_labels", "source_unit_anchors",
        "source_unit", "source_to_vnd_multiplier", "raw_decimal_candidate",
        "reason_codes",
    )
    return {key: record[key] for key in keys if key in record}


def compact_period_candidates(operand: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = (
        "internal_table_uid", "document_id", "row_index", "column_index", "source_label",
        "period_labels", "unit_labels", "raw_source_cell", "cell_provenance",
        "header_source_cells", "reason_codes",
    )
    return [{key: candidate[key] for key in keys if key in candidate}
            for candidate in operand.get("period_column_candidates") or []]


def build(
    *, bindings: Path, bindings_manifest: Path, execution: Path, execution_manifest: Path,
    period_packets: Path, period_manifest: Path, route_overlay: Path,
    route_overlay_manifest: Path, no_candidate_audit: Path, output: Path,
) -> dict[str, Any]:
    bm = json.loads(bindings_manifest.read_text(encoding="utf-8"))
    em = json.loads(execution_manifest.read_text(encoding="utf-8"))
    pm = json.loads(period_manifest.read_text(encoding="utf-8"))
    rm = json.loads(route_overlay_manifest.read_text(encoding="utf-8"))
    binding_sha = require_hash(bindings, (bm.get("outputs") or {}).get("bindings", {}).get("sha256"), "bindings")
    execution_sha = require_hash(execution, (em.get("outputs") or {}).get("execution", {}).get("sha256"), "execution")
    period_sha = require_hash(period_packets, (pm.get("outputs") or {}).get("period_packets", {}).get("sha256"), "period packets")
    route_sha = require_hash(route_overlay, (rm.get("outputs") or {}).get("overlay", {}).get("sha256"), "route overlay")
    audit_sha = require_hash(no_candidate_audit, (pm.get("outputs") or {}).get("no_candidate_audit", {}).get("sha256"), "no-candidate audit")
    if (em.get("inputs") or {}).get("bindings", {}).get("sha256") != binding_sha:
        raise ValueError("Execution manifest is not bound to the supplied bindings")

    binding_rows = require_ids(load(bindings), "bindings")
    execution_rows = require_ids(load(execution), "execution")
    period_rows = require_ids(load(period_packets), "period packets")
    route_rows = require_ids(load(route_overlay), "route overlay")
    audits = {int(row["question_id"]): row for row in load(no_candidate_audit)}
    queue: list[dict[str, Any]] = []
    for question_id in sorted(EXPECTED_IDS):
        execution_row = execution_rows[question_id]
        if execution_row.get("execution_status") != "binding_conflict":
            continue
        route = route_rows[question_id]
        if route.get("route_status") != "route_complete":
            raise ValueError(f"Q{question_id}: binding conflict is not route-complete")
        binding = binding_rows[question_id]
        period = period_rows[question_id]
        stages: list[dict[str, Any]] = []
        for stage_index, binding_stage in enumerate(binding.get("stages") or []):
            period_stage = (period.get("stages") or [])[stage_index]
            operands: list[dict[str, Any]] = []
            for operand_index, binding_operand in enumerate(binding_stage.get("required_operands") or []):
                period_operand = (period_stage.get("required_operands") or [])[operand_index]
                reasons = sorted(set(binding_operand.get("reason_codes") or []))
                if not reasons:
                    reasons = ["UNSPECIFIED_BINDING_CONFLICT"]
                source = compact_source(binding_operand) if "internal_table_uid" in binding_operand else None
                candidates = compact_period_candidates(period_operand)
                audit = audits.get(question_id)
                requires_no_candidate_audit = str(period.get("input_packet_status") or "") == "no_candidate"
                if requires_no_candidate_audit:
                    if audit is None:
                        raise ValueError(f"Q{question_id}: missing hash-bound no-candidate audit")
                operands.append({
                    "stage_id": binding_stage.get("stage_id"),
                    "operand_index": operand_index,
                    "role": binding_operand.get("role"),
                    "concept_id": binding_operand.get("concept_id"),
                    "reason_codes": reasons,
                    "binding_source": source,
                    "period_column_candidates": candidates,
                    "no_candidate_audit": None if not requires_no_candidate_audit else {
                        "question_id": audit.get("question_id"),
                        "stage_id": audit.get("stage_id"),
                        "exclusive_primary_cause": audit.get("exclusive_primary_cause"),
                        "recommended_review_queue": audit.get("recommended_review_queue"),
                        "minimal_blocker_sets": audit.get("minimal_blocker_sets"),
                        "nearby_exact_concept_candidates": audit.get("nearby_exact_concept_candidates"),
                    },
                })
            stages.append({"stage_id": binding_stage.get("stage_id"), "operands": operands})
        queue.append({
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "question_context": route.get("question_context"),
            "route_status": route.get("route_status"),
            "binding_packet_status": binding.get("binding_packet_status"),
            "execution_status": execution_row.get("execution_status"),
            "requested_output_unit": route.get("requested_output_unit"),
            "stages": stages,
            "input_identity": {
                "bindings_sha256": binding_sha,
                "execution_sha256": execution_sha,
                "period_packets_sha256": period_sha,
                "route_overlay_sha256": route_sha,
                "no_candidate_audit_sha256": audit_sha,
            },
            "decision_contract": decision_contract(),
            "source_contract": contract(),
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in queue), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "bindings": {"path": str(bindings), "sha256": binding_sha},
            "bindings_manifest": {"path": str(bindings_manifest), "sha256": sha(bindings_manifest)},
            "execution": {"path": str(execution), "sha256": execution_sha},
            "execution_manifest": {"path": str(execution_manifest), "sha256": sha(execution_manifest)},
            "period_packets": {"path": str(period_packets), "sha256": period_sha},
            "period_manifest": {"path": str(period_manifest), "sha256": sha(period_manifest)},
            "route_overlay": {"path": str(route_overlay), "sha256": route_sha},
            "route_overlay_manifest": {"path": str(route_overlay_manifest), "sha256": sha(route_overlay_manifest)},
            "no_candidate_audit": {"path": str(no_candidate_audit), "sha256": audit_sha},
        },
        "outputs": {"queue": {"path": str(output), "sha256": sha(output)}},
        "counts": {
            "queue_count": len(queue),
            "reason_counts": dict(sorted(Counter(reason for row in queue for stage in row["stages"] for operand in stage["operands"] for reason in operand["reason_codes"]).items())),
            "decision_pending_count": len(queue),
        },
        "source_contract": contract(),
        "repairs_materialized": False,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}
