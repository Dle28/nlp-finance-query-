#!/usr/bin/env python3
"""Build one hash-bound, single-reviewer worklist from semantic review queues."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "computational_semantic_single_reviewer_campaign_v1"
SOURCE_CONTRACT = {
    "candidate_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "materialization_allowed": False,
    "may_compute_answer": False,
    "may_select_value_cell": False,
}
QUEUE_SPECS = (
    {
        "phase": "p1_direct_source_coordinates",
        "priority": 1,
        "protocol": "computational_semantic_source_review_queue_v1",
        "queue_status": "blank_source_coordinate_review",
        "review_action": "Verify direct source coordinates for a complete typed plan.",
        "after_accept_gate": "Independent source binding and provenance validation; no automatic materialization.",
    },
    {
        "phase": "p2_scope_resolution",
        "priority": 2,
        "protocol": "computational_semantic_scope_review_queue_v1",
        "queue_status": "blank_scope_review",
        "review_action": "Prove report scope from source context, then route the result to source-coordinate review.",
        "after_accept_gate": "Independent source binding remains required; scope acceptance is not a source/evidence decision.",
    },
    {
        "phase": "p3_component_source_coordinates",
        "priority": 3,
        "protocol": "computational_semantic_component_source_review_queue_v1",
        "queue_status": "blank_component_source_coordinate_review",
        "review_action": "Verify each literal component and its population, without accepting the unresolved composition.",
        "after_accept_gate": "A separately reviewed controlled composition template is required before any execution.",
    },
    {
        "phase": "p4_dimension_contracts",
        "priority": 4,
        "protocol": "computational_semantic_dimension_review_queue_v1",
        "queue_status": "blank_dimension_contract_review",
        "review_action": "Confirm the detail dimension contract; do not map a broad statement line to a qualified fact.",
        "after_accept_gate": "Independent taxonomy, normalization and source-binding gates are required.",
    },
)


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


def _validate_queue(
    *,
    queue: Path,
    manifest_path: Path,
    spec: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = load_json(manifest_path)
    queue_sha = sha256_file(queue)
    expected_sha = ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256")
    if (
        not isinstance(expected_sha, str)
        or queue_sha != expected_sha
        or manifest.get("protocol") != spec["protocol"]
        or manifest.get("queue_status") != spec["queue_status"]
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or not isinstance(manifest.get("source_contract"), Mapping)
    ):
        raise ValueError(f"{spec['phase']}: queue manifest is invalid or not blank/non-materializable")
    rows = load_jsonl(queue)
    question_ids = [row.get("question_id") for row in rows]
    if (
        not rows
        or any(type(question_id) is not int for question_id in question_ids)
        or len(set(question_ids)) != len(question_ids)
        or manifest.get("question_count") != len(rows)
        or manifest.get("question_ids") != sorted(question_ids)
    ):
        raise ValueError(f"{spec['phase']}: queue manifest does not cover question IDs exactly")
    for row in rows:
        question_id = int(row["question_id"])
        if (
            row.get("schema_version") != 1
            or row.get("protocol") != spec["protocol"]
            or row.get("materialization_allowed") is not False
            or row.get("source_contract") != manifest.get("source_contract")
            or canonical_sha256(row.get("review_context")) != row.get("immutable_review_context_sha256")
            or (row.get("review_decision_contract") or {}).get("decision") is not None
        ):
            raise ValueError(f"{spec['phase']}: Q{question_id} immutable queue packet is malformed")
    return rows, manifest


def build(
    *,
    source_queue: Path,
    source_manifest: Path,
    scope_queue: Path,
    scope_manifest: Path,
    component_queue: Path,
    component_manifest: Path,
    dimension_queue: Path,
    dimension_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create a priority worklist while keeping all review packets immutable."""
    output_dir.mkdir(parents=True, exist_ok=True)
    worklist_path = output_dir / "computational_semantic_single_reviewer_campaign_v1.jsonl"
    manifest_path = output_dir / "computational_semantic_single_reviewer_campaign_v1.manifest.json"
    if worklist_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite semantic review campaign: {output_dir}")
    paths = (
        (source_queue, source_manifest),
        (scope_queue, scope_manifest),
        (component_queue, component_manifest),
        (dimension_queue, dimension_manifest),
    )
    all_rows: list[dict[str, Any]] = []
    queue_records: list[dict[str, Any]] = []
    seen_question_phase: set[tuple[str, int]] = set()
    for spec, (queue, manifest) in zip(QUEUE_SPECS, paths, strict=True):
        rows, queue_meta = _validate_queue(queue=queue, manifest_path=manifest, spec=spec)
        queue_records.append(
            {
                "phase": spec["phase"],
                "priority": spec["priority"],
                "queue": {"path": str(queue), "sha256": sha256_file(queue)},
                "queue_manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
                "question_count": len(rows),
                "queue_protocol": queue_meta["protocol"],
            }
        )
        for packet in rows:
            question_id = int(packet["question_id"])
            uniqueness = (str(spec["phase"]), question_id)
            if uniqueness in seen_question_phase:
                raise ValueError(f"Duplicate campaign work item: {uniqueness}")
            seen_question_phase.add(uniqueness)
            all_rows.append(
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "work_item_id": f"semantic-campaign-v1-{spec['phase']}-q{question_id}",
                    "priority": spec["priority"],
                    "phase": spec["phase"],
                    "queue_protocol": spec["protocol"],
                    "queue_question_id": question_id,
                    "immutable_review_context_sha256": packet["immutable_review_context_sha256"],
                    "question": (packet.get("review_context") or {}).get("question"),
                    "review_action": spec["review_action"],
                    "after_accept_gate": spec["after_accept_gate"],
                    "review_decision": None,
                    "decision_recorded_in": None,
                    "materialization_allowed": False,
                    "source_contract": SOURCE_CONTRACT,
                }
            )
    all_rows.sort(key=lambda row: (int(row["priority"]), int(row["queue_question_id"])))
    if len({row["work_item_id"] for row in all_rows}) != len(all_rows):
        raise ValueError("Campaign worklist contains duplicate IDs")
    worklist_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in all_rows),
        encoding="utf-8",
    )
    phase_counts = Counter(str(row["phase"]) for row in all_rows)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "campaign_status": "blank_single_reviewer_worklist",
        "work_item_count": len(all_rows),
        "phase_counts": dict(sorted(phase_counts.items())),
        "review_sequence": [spec["phase"] for spec in QUEUE_SPECS],
        "review_decisions_prepopulated": False,
        "requires_human_decisions": True,
        "materialization_allowed": False,
        "inputs": {record["phase"]: record for record in queue_records},
        "outputs": {"worklist": {"path": str(worklist_path), "sha256": sha256_file(worklist_path)}},
        "source_contract": SOURCE_CONTRACT,
    }
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-queue", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--scope-queue", type=Path, required=True)
    parser.add_argument("--scope-manifest", type=Path, required=True)
    parser.add_argument("--component-queue", type=Path, required=True)
    parser.add_argument("--component-manifest", type=Path, required=True)
    parser.add_argument("--dimension-queue", type=Path, required=True)
    parser.add_argument("--dimension-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build(**{name: value.resolve() for name, value in vars(args).items()})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
