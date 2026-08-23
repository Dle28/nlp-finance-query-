#!/usr/bin/env python3
"""Create blind, hash-bound independent reviews for abstained question routes.

The assignment is operational handoff only.  It does not resolve entity,
scope, year, taxonomy, operation, source coordinates, or route status, and it
never materializes an upstream patch from a review label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from finance_query.route_coverage_adjudication import (
    PROTOCOL as QUEUE_PROTOCOL,
    TEMPLATE_PROTOCOL,
    canonical_sha256,
    sha256_file,
    source_contract,
)


PROTOCOL = "route_coverage_independent_review_assignment_v1"
LABEL_PROTOCOL = "route_coverage_independent_review_label_v1"
SLOTS = ("reviewer_a", "reviewer_b")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite route-review assignment: {path}")
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _assignment_key(slot: str, question_id: int) -> str:
    return hashlib.sha256(f"{slot}|{question_id}".encode("utf-8")).hexdigest()


def _immutable_queue_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "question_id",
            "question",
            "normalized_question",
            "route_status",
            "route_reason_codes",
            "question_context",
            "route_stages",
            "review_tracks",
            "missing_context",
        )
    }


def _assignment_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_immutable_queue_payload(row),
        "immutable_route_sha256": row.get("immutable_route_sha256"),
        "immutable_queue_payload_sha256": row.get("immutable_queue_payload_sha256"),
        "source_contract": row.get("source_contract"),
    }


def _validate_inputs(
    *, queue_path: Path, template_path: Path, manifest_path: Path
) -> tuple[dict[int, dict[str, Any]], str, str, str]:
    manifest = _load_json(manifest_path)
    if manifest.get("protocol") != QUEUE_PROTOCOL:
        raise ValueError("Route-coverage manifest has unsupported protocol")
    outputs = manifest.get("outputs") or {}
    queue_sha = _require_hash(queue_path, ((outputs.get("queue") or {}).get("sha256")), "route-coverage queue")
    template_sha = _require_hash(
        template_path,
        ((outputs.get("label_template") or {}).get("sha256")),
        "route-coverage label template",
    )
    if manifest.get("repairs_materialized") is not False or manifest.get("source_contract") != source_contract():
        raise ValueError("Route-coverage input must remain non-materializable")
    queue_rows = _load_jsonl(queue_path)
    template_rows = _load_jsonl(template_path)
    queue_by_id: dict[int, dict[str, Any]] = {}
    for row in queue_rows:
        question_id = row.get("question_id")
        if type(question_id) is not int or question_id in queue_by_id:
            raise ValueError("Route-coverage queue has missing or duplicate question IDs")
        if row.get("protocol") != QUEUE_PROTOCOL or row.get("route_status") != "abstain":
            raise ValueError(f"Q{question_id}: route-review queue must contain only abstentions")
        if row.get("source_contract") != source_contract():
            raise ValueError(f"Q{question_id}: route-review queue contract mismatch")
        if canonical_sha256(_immutable_queue_payload(row)) != row.get("immutable_queue_payload_sha256"):
            raise ValueError(f"Q{question_id}: immutable route-review payload hash mismatch")
        decision = row.get("review_decision_contract") or {}
        if any(decision.get(key) is not None for key in decision if key != "eligible_for_materialization"):
            raise ValueError(f"Q{question_id}: route-review queue already contains a decision")
        if decision.get("eligible_for_materialization") is not False:
            raise ValueError(f"Q{question_id}: route-review queue enables materialization")
        queue_by_id[question_id] = row
    template_by_id: dict[int, dict[str, Any]] = {}
    for row in template_rows:
        question_id = row.get("question_id")
        if type(question_id) is not int or question_id in template_by_id:
            raise ValueError("Route-coverage label template has missing or duplicate question IDs")
        if row.get("protocol") != TEMPLATE_PROTOCOL or row.get("is_blank_template") is not True:
            raise ValueError(f"Q{question_id}: route-review label template is not blank")
        if row.get("source_contract") != source_contract():
            raise ValueError(f"Q{question_id}: route-review label-template contract mismatch")
        if any(
            row.get(key) is not None
            for key in (
                "decision",
                "decision_provenance",
                "reviewer_id",
                "reviewed_at",
                "source_coordinates_checked",
                "proposed_question_plan",
                "proposed_taxonomy_alias",
                "proposed_operation_contract",
            )
        ):
            raise ValueError(f"Q{question_id}: route-review label template already contains a decision")
        template_by_id[question_id] = row
    if not queue_by_id or set(queue_by_id) != set(template_by_id):
        raise ValueError("Route-coverage queue/template ID coverage mismatch")
    for question_id, queue in queue_by_id.items():
        if template_by_id[question_id].get("immutable_queue_payload_sha256") != queue["immutable_queue_payload_sha256"]:
            raise ValueError(f"Q{question_id}: route-review template is not bound to queue payload")
    return queue_by_id, queue_sha, template_sha, sha256_file(manifest_path)


def build_assignments(
    *, queue_path: Path, template_path: Path, manifest_path: Path, output_dir: Path
) -> dict[str, Any]:
    queue_by_id, queue_sha, template_sha, manifest_sha = _validate_inputs(
        queue_path=queue_path,
        template_path=template_path,
        manifest_path=manifest_path,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, str]] = {}
    instructions = (
        "Reopen only independently identified source material before proposing any route-context, taxonomy, or operation change.",
        "Record accept, reject, or abstain; abstain if source coordinates or a complete route contract cannot be established.",
        "Do not infer a missing entity, year, scope, taxonomy alias, operation, table, cell, value, formula, or answer from model/retrieval convenience.",
        "Do not inspect another reviewer label before submitting this review.",
        "This label remains non-materializable until a separate source-bound materializer and all policy gates accept it.",
    )
    for slot in SLOTS:
        assignments: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        for position, question_id in enumerate(
            sorted(queue_by_id, key=lambda item: _assignment_key(slot, item)), start=1
        ):
            queue = queue_by_id[question_id]
            payload = _assignment_payload(queue)
            assignment_id = f"route-coverage-v1-{slot}-q{question_id}"
            assignments.append(
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "assignment_id": assignment_id,
                    "reviewer_slot": slot,
                    "assignment_position": position,
                    "question_id": question_id,
                    "immutable_queue_payload_sha256": queue["immutable_queue_payload_sha256"],
                    "immutable_assignment_payload_sha256": canonical_sha256(payload),
                    "queue_payload": payload,
                    "review_instructions": list(instructions),
                    "source_contract": source_contract(),
                }
            )
            templates.append(
                {
                    "schema_version": 1,
                    "protocol": LABEL_PROTOCOL,
                    "assignment_id": assignment_id,
                    "question_id": question_id,
                    "immutable_queue_payload_sha256": queue["immutable_queue_payload_sha256"],
                    "immutable_assignment_payload_sha256": canonical_sha256(payload),
                    "decision": None,
                    "decision_provenance": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_coordinates_checked": None,
                    "proposed_question_plan": None,
                    "proposed_taxonomy_alias": None,
                    "proposed_operation_contract": None,
                    "notes": "",
                    "is_blank_template": True,
                    "source_contract": source_contract(),
                }
            )
        assignment_path = output_dir / f"route_coverage_{slot}_assignment_v1.jsonl"
        template_path_out = output_dir / f"route_coverage_{slot}_label_template_v1.jsonl"
        _write_jsonl(assignment_path, assignments)
        _write_jsonl(template_path_out, templates)
        outputs[slot] = {
            "assignment_path": str(assignment_path),
            "assignment_sha256": sha256_file(assignment_path),
            "label_template_path": str(template_path_out),
            "label_template_sha256": sha256_file(template_path_out),
        }
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "reviewer_slots": list(SLOTS),
        "assignment_count_per_reviewer": len(queue_by_id),
        "blind_to_other_review": True,
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "queue": {"path": str(queue_path), "sha256": queue_sha},
            "label_template": {"path": str(template_path), "sha256": template_sha},
            "queue_manifest": {"path": str(manifest_path), "sha256": manifest_sha},
        },
        "outputs": outputs,
        "source_contract": source_contract(),
    }
    output_manifest = output_dir / "route_coverage_independent_review_assignments_v1.manifest.json"
    if output_manifest.exists():
        raise FileExistsError(f"Refusing to overwrite route-review assignment manifest: {output_manifest}")
    output_manifest.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(output_manifest)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--label-template", type=Path, required=True)
    parser.add_argument("--queue-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        build_assignments(
            queue_path=args.queue,
            template_path=args.label_template,
            manifest_path=args.queue_manifest,
            output_dir=args.output_dir,
        )["manifest_path"]
    )


if __name__ == "__main__":
    main()
