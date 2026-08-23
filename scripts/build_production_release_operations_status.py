#!/usr/bin/env python3
"""Build a read-only operations snapshot for the blocked release backlog.

The snapshot verifies the immutable V1 release gate, remediation queue and
assignment registry. It intentionally accepts no completed labels or review
receipts, so its only valid observation is that the prebuilt assignments are
ready and external human-review evidence has not been supplied to this command.
It never searches arbitrary directories and cannot infer that a blank template
is a completed review.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "verify_production_release_intake_reviews",
    ROOT / "scripts" / "verify_production_release_intake_reviews.py",
)
_review = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_review)


PROTOCOL = "production_release_operations_status_v1"
REGISTRY_PROTOCOL = "production_release_review_assignment_registry_v1"
RELEASE_GATE_PROTOCOL = "production_release_gate_v1"
REMEDIATION_PROTOCOL = "production_release_remediation_queue_v1"
SOURCE_CONTRACT = _review.SOURCE_CONTRACT
LANE_VALIDATORS = {
    "typed_plan_source_adjudication": {
        "script": "scripts/validate_typed_plan_review_consensus.py",
        "receipt_state": "typed_plan_contract_source_validated_non_materializable",
    },
    "formula_evidence_completion": {
        "script": "scripts/validate_formula_evidence_review_consensus.py",
        "receipt_state": "formula_contract_source_validated_non_materializable",
    },
    "independent_source_replay": {
        "script": "scripts/validate_independent_source_replay_consensus.py",
        "receipt_state": "replay_contract_source_validated_non_materializable",
    },
    "formula_evidence_materialization": {
        "script": "scripts/validate_formula_evidence_materialization_consensus.py",
        "receipt_state": "formula_materialization_contract_source_validated_non_materializable",
    },
    "deterministic_executor_compile": {
        "script": "scripts/validate_release_remainder_review_consensus.py",
        "receipt_state": "executor_contract_source_validated_non_materializable",
    },
    "exact_source_conflict_adjudication": {
        "script": "scripts/validate_release_remainder_review_consensus.py",
        "receipt_state": "conflict_contract_source_validated_non_materializable",
    },
    "query_program_shadow_completion": {
        "script": "scripts/validate_release_remainder_review_consensus.py",
        "receipt_state": "query_program_contract_source_validated_non_materializable",
    },
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return _review.load_jsonl(path)


def _validate_release_gate(path: Path) -> dict[str, Any]:
    gate = _load_json(path)
    if (
        gate.get("protocol") != RELEASE_GATE_PROTOCOL
        or gate.get("release_status") != "blocked"
        or gate.get("production_eligible") is not False
        or gate.get("submission_compilation_allowed") is not False
        or gate.get("answer_materialization_allowed") is not False
        or gate.get("source_contract") != SOURCE_CONTRACT
    ):
        raise ValueError("Operations status requires the current blocked non-promotable release gate")
    return gate


def _validate_remediation_queue(path: Path, manifest_path: Path, gate: Path) -> dict[int, str]:
    manifest = _load_json(manifest_path)
    if (
        manifest.get("protocol") != REMEDIATION_PROTOCOL
        or manifest.get("queue_status") != "non_materializable"
        or manifest.get("source_contract") != SOURCE_CONTRACT
        or ((manifest.get("inputs") or {}).get("release_gate") or {}).get("sha256") != _sha256_file(gate)
        or ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256") != _sha256_file(path)
    ):
        raise ValueError("Operations status requires the immutable remediation queue")
    rows = _load_jsonl(path)
    by_id: dict[int, str] = {}
    for row in rows:
        question_id, lane = row.get("question_id"), row.get("remediation_lane")
        if (
            type(question_id) is not int
            or question_id in by_id
            or lane not in LANE_VALIDATORS
            or row.get("materialization_allowed") is not False
            or row.get("source_contract") != SOURCE_CONTRACT
        ):
            raise ValueError("Remediation queue contains an invalid operational lane")
        by_id[question_id] = str(lane)
    if len(by_id) != 957:
        raise ValueError("Operations status requires the exact 957-ID remediation backlog")
    return by_id


def build(*, registry: Path, release_gate: Path, remediation_queue: Path, remediation_manifest: Path, output: Path) -> dict[str, Any]:
    """Write one snapshot of review readiness; never inspect or create labels."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite operations status: {output}")
    gate = _validate_release_gate(release_gate)
    queue_by_id = _validate_remediation_queue(remediation_queue, remediation_manifest, release_gate)
    registry_payload = _load_json(registry)
    if (
        registry_payload.get("protocol") != REGISTRY_PROTOCOL
        or registry_payload.get("assignment_status") != "blank_independent_human_review"
        or registry_payload.get("question_count") != 957
        or registry_payload.get("reviewer_slots") != list(_review.SLOTS)
        or registry_payload.get("labels_prepopulated") is not False
        or registry_payload.get("materialization_allowed") is not False
        or registry_payload.get("source_contract") != SOURCE_CONTRACT
    ):
        raise ValueError("Operations status requires a blank independent-review assignment registry")
    entries = registry_payload.get("lanes")
    if not isinstance(entries, list) or len(entries) != len(LANE_VALIDATORS):
        raise ValueError("Assignment registry lacks the seven remediation lanes")

    observed_ids: set[int] = set()
    lane_rows: list[dict[str, Any]] = []
    for entry in entries:
        lane = entry.get("lane")
        if lane not in LANE_VALIDATORS or not isinstance(entry.get("question_count"), int):
            raise ValueError("Assignment registry has an unsupported lane record")
        assignment_manifest_record = entry.get("assignment_manifest") or {}
        assignment_manifest = Path(str(assignment_manifest_record.get("path") or ""))
        if _sha256_file(assignment_manifest) != assignment_manifest_record.get("sha256"):
            raise ValueError(f"{lane}: assignment manifest hash mismatch")
        assignments_by_slot: dict[str, dict[int, dict[str, Any]]] = {}
        for slot in _review.SLOTS:
            slot_record = (entry.get("reviewer_assignments") or {}).get(slot) or {}
            assignment = Path(str(slot_record.get("assignment_path") or ""))
            validated = _review.validate_assignment(
                assignment=assignment, assignment_manifest=assignment_manifest, reviewer_slot=slot
            )
            if validated["assignment_sha256"] != slot_record.get("assignment_sha256"):
                raise ValueError(f"{lane}: {slot} assignment hash mismatch")
            assignments_by_slot[slot] = validated["assignments_by_id"]
        lane_ids = set(assignments_by_slot["reviewer_a"])
        if lane_ids != set(assignments_by_slot["reviewer_b"]) or len(lane_ids) != entry["question_count"]:
            raise ValueError(f"{lane}: reviewer slots do not cover the same immutable IDs")
        if observed_ids.intersection(lane_ids) or any(queue_by_id.get(question_id) != lane for question_id in lane_ids):
            raise ValueError(f"{lane}: assignment coverage differs from remediation queue")
        observed_ids.update(lane_ids)
        lane_rows.append(
            {
                "lane": lane,
                "question_count": len(lane_ids),
                "reviewer_assignment_status": "ready_blank_independent_assignments",
                "completed_human_labels_observed": 0,
                "completed_review_receipts_observed": 0,
                "observation_scope": "registry-only; no completed labels or review receipts were supplied",
                "next_required_external_artifact": "two complete independent human label files and their verified review manifests",
                "then_required_validator": LANE_VALIDATORS[lane],
                "materialization_allowed": False,
            }
        )
    if observed_ids != set(queue_by_id):
        raise ValueError("Assignment registry does not cover every remediation ID exactly once")
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "release_status": "blocked_awaiting_external_human_review_evidence",
        "production_eligible": False,
        "submission_compilation_allowed": False,
        "answer_materialization_allowed": False,
        "inputs": {
            "assignment_registry": {"path": str(registry), "sha256": _sha256_file(registry)},
            "release_gate": {"path": str(release_gate), "sha256": _sha256_file(release_gate)},
            "remediation_queue": {"path": str(remediation_queue), "sha256": _sha256_file(remediation_queue)},
            "remediation_manifest": {"path": str(remediation_manifest), "sha256": _sha256_file(remediation_manifest)},
        },
        "counts": {
            "remediation_question_count": len(observed_ids),
            "ready_reviewer_assignment_count": len(observed_ids) * len(_review.SLOTS),
            "completed_human_labels_observed": 0,
            "completed_review_receipts_observed": 0,
            "lane_count": len(lane_rows),
        },
        "lanes": lane_rows,
        "blocking_reason": "No completed external human-review labels or verified review receipts were supplied to this status snapshot.",
        "materialization_allowed": False,
        "source_contract": SOURCE_CONTRACT,
        "upstream_release_gate_counts": gate.get("counts"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--release-gate", type=Path, required=True)
    parser.add_argument("--remediation-queue", type=Path, required=True)
    parser.add_argument("--remediation-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(**vars(args))["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
