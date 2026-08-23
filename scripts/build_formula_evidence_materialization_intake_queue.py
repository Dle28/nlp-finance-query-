#!/usr/bin/env python3
"""Build a blank intake for typed plans lacking any Formula EvidenceSet.

This targets only the non-overlapping ``formula_evidence_materialization``
lane from the release remediation queue.  It carries a typed contract, not a
source candidate: no table, row, column, value, evidence, formula result or
eligibility can be read from or written through this intake.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "formula_evidence_materialization_intake_queue_v1"
RELEASE_GATE_PROTOCOL = "production_release_gate_v1"
REMEDIATION_PROTOCOL = "production_release_remediation_queue_v1"
TYPED_PLAN_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"
LANE = "formula_evidence_materialization"
SOURCE_CONTRACT = {
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def index(rows: list[dict[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if type(value) is not int:
            raise ValueError(f"{label} contains a non-integer {key}")
        if value in output:
            raise ValueError(f"{label} contains duplicate question ID Q{value}")
        output[value] = row
    return output


def require_hash(path: Path, expected: object, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _release_artifact_sha(gate: Mapping[str, Any], name: str) -> str:
    value: object = (gate.get("inputs") or {}).get(name)
    if name != "bundle_review_items":
        value = value.get("artifact") if isinstance(value, Mapping) else None
    if not isinstance(value, Mapping) or not isinstance(value.get("sha256"), str):
        raise ValueError(f"Release gate lacks {name} artifact SHA-256")
    return str(value["sha256"])


def validate_release_gate(path: Path) -> dict[str, Any]:
    gate = load_json(path)
    if (
        gate.get("protocol") != RELEASE_GATE_PROTOCOL
        or gate.get("release_status") != "blocked"
        or gate.get("production_eligible") is not False
        or gate.get("submission_compilation_allowed") is not False
        or gate.get("answer_materialization_allowed") is not False
        or (gate.get("source_contract") or {}) != SOURCE_CONTRACT
    ):
        raise ValueError("Formula materialization intake requires a blocked release gate")
    return gate


def validate_remediation_queue(
    *, path: Path, manifest_path: Path, release_gate: Path, typed_plans_sha: str
) -> set[int]:
    manifest = load_json(manifest_path)
    if (
        manifest.get("protocol") != REMEDIATION_PROTOCOL
        or manifest.get("queue_status") != "non_materializable"
        or (manifest.get("source_contract") or {}) != SOURCE_CONTRACT
    ):
        raise ValueError("Remediation queue manifest is not non-materializable")
    require_hash(path, ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256"), "remediation queue")
    require_hash(release_gate, ((manifest.get("inputs") or {}).get("release_gate") or {}).get("sha256"), "release gate")
    if ((manifest.get("inputs") or {}).get("typed_plans") or {}).get("sha256") != typed_plans_sha:
        raise ValueError("Remediation queue is not bound to typed plans")
    rows = index(load_jsonl(path), "question_id", "remediation queue")
    lane_ids: set[int] = set()
    for question_id, row in rows.items():
        if (
            row.get("protocol") != REMEDIATION_PROTOCOL
            or row.get("materialization_allowed") is not False
            or (row.get("source_contract") or {}) != SOURCE_CONTRACT
        ):
            raise ValueError(f"Q{question_id}: remediation row violates non-materializable contract")
        if row.get("remediation_lane") == LANE:
            lane_ids.add(question_id)
    if not lane_ids:
        raise ValueError("Remediation queue contains no Formula EvidenceSet materialization lane")
    return lane_ids


def typed_contract(plan: Mapping[str, Any]) -> dict[str, Any]:
    operands: list[dict[str, Any]] = []
    for operand in plan.get("operands") or []:
        if not isinstance(operand, Mapping):
            raise ValueError(f"Q{plan.get('question_id')}: typed operand must be an object")
        operands.append(
            {
                key: operand.get(key)
                for key in (
                    "operand_id", "entity", "ticker", "role", "stage_id", "scope",
                    "years", "required", "metric_hints", "allowed_table_functions",
                    "grounding_contract", "unit_contract",
                )
            }
        )
    return {
        "effective_family": plan.get("effective_family"),
        "route": plan.get("route"),
        "plan_fingerprint": plan.get("plan_fingerprint"),
        "entities": plan.get("entities"),
        "years": plan.get("years"),
        "scope": plan.get("scope"),
        "requested_unit": plan.get("requested_unit"),
        "operation_ast": plan.get("operation_ast"),
        "operands": operands,
    }


def build(
    *, bundle_dir: Path, typed_plans: Path, release_gate: Path,
    remediation_queue: Path, remediation_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    """Write blank source-review intake records for FormulaSet-materialization work."""
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "formula_evidence_materialization_intake_queue_v1.jsonl"
    manifest_path = output_dir / "formula_evidence_materialization_intake_queue_v1.manifest.json"
    if queue_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite FormulaSet materialization intake: {output_dir}")
    gate = validate_release_gate(release_gate)
    typed_manifest_path = typed_plans.with_suffix(".manifest.json")
    typed_manifest = load_json(typed_manifest_path)
    typed_sha = require_hash(typed_plans, typed_manifest.get("sidecar_sha256"), "typed plans")
    review_items_path = bundle_dir / "review_items.jsonl"
    review_items_sha = sha256_file(review_items_path)
    if (
        typed_manifest.get("protocol") != TYPED_PLAN_PROTOCOL
        or typed_manifest.get("review_items_sha256") != review_items_sha
        or _release_artifact_sha(gate, "typed_plans") != typed_sha
        or _release_artifact_sha(gate, "bundle_review_items") != review_items_sha
    ):
        raise ValueError("Typed-plan/review-item lineage does not bind the release gate")
    items = index(load_jsonl(review_items_path), "id", "review items")
    plans = index(load_jsonl(typed_plans), "question_id", "typed plans")
    if set(items) != set(plans):
        raise ValueError("Typed plans must cover review items exactly")
    lane_ids = validate_remediation_queue(
        path=remediation_queue,
        manifest_path=remediation_manifest,
        release_gate=release_gate,
        typed_plans_sha=typed_sha,
    )
    rows: list[dict[str, Any]] = []
    for question_id in sorted(lane_ids):
        plan = plans[question_id]
        if plan.get("decomposition_status") != "complete":
            raise ValueError(f"Q{question_id}: FormulaSet materialization lane requires a complete typed plan")
        context = {
            "question": items[question_id].get("question"),
            "question_plan": items[question_id].get("question_plan"),
            "typed_plan_contract": typed_contract(plan),
        }
        rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "plan_fingerprint": context["typed_plan_contract"]["plan_fingerprint"],
                "immutable_review_context_sha256": canonical_sha256(context),
                "review_context": context,
                "review_instructions": [
                    "Reopen exact source material independently for each typed operand.",
                    "Materialize nothing if entity, period, scope, literal unit or exact raw-cell coordinate is ambiguous.",
                    "Do not record candidate table/cell/value, selected matches, execution results, answers, labels or eligibility decisions in this intake.",
                    "A new Formula EvidenceSet must be built and validated by the existing strict producer; this queue cannot create it.",
                ],
                "review_decision_contract": {
                    "decision": None,
                    "decision_provenance": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_coordinates_checked": None,
                    "proposed_formula_evidence_contract": None,
                    "materialization_allowed": False,
                },
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    if len({row["question_id"] for row in rows}) != len(rows) or {row["question_id"] for row in rows} != lane_ids:
        raise ValueError("FormulaSet materialization intake must cover its remediation lane exactly once")
    queue_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    family_counts = Counter(str(row["review_context"]["typed_plan_contract"]["effective_family"]) for row in rows)
    route_counts = Counter(str(row["review_context"]["typed_plan_contract"]["route"]) for row in rows)
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_status": "blank_source_review_intake",
        "question_count": len(rows),
        "family_counts": dict(sorted(family_counts.items())),
        "route_counts": dict(sorted(route_counts.items())),
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "bundle_review_items": {"path": str(review_items_path), "sha256": review_items_sha},
            "typed_plans": {"path": str(typed_plans), "sha256": typed_sha},
            "typed_plans_manifest": {"path": str(typed_manifest_path), "sha256": sha256_file(typed_manifest_path)},
            "release_gate": {"path": str(release_gate), "sha256": sha256_file(release_gate)},
            "remediation_queue": {"path": str(remediation_queue), "sha256": sha256_file(remediation_queue)},
            "remediation_manifest": {"path": str(remediation_manifest), "sha256": sha256_file(remediation_manifest)},
        },
        "outputs": {"queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)}},
        "source_contract": SOURCE_CONTRACT,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--typed-plans", type=Path, required=True)
    parser.add_argument("--release-gate", type=Path, required=True)
    parser.add_argument("--remediation-queue", type=Path, required=True)
    parser.add_argument("--remediation-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(**vars(args))["family_counts"], ensure_ascii=False, sort_keys=True))
