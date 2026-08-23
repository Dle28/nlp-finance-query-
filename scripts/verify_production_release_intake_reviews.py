#!/usr/bin/env python3
"""Verify completed human labels for a production-release remediation intake.

The command only writes a hash-bound review manifest.  It never changes an
intake, typed plan, Formula EvidenceSet, QueryProgram, review label, evidence
set, execution record, answer, provenance, or release eligibility.  A later,
lane-specific source validator must independently consume an accepted proposal
and rebuild its artifact from raw sources.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "production_release_intake_human_review_v1"
ASSIGNMENT_PROTOCOL = "production_release_intake_independent_review_assignment_v1"
LABEL_PROTOCOL = "production_release_intake_review_label_v1"
RELEASE_GATE_PROTOCOL = "production_release_gate_v1"
REMEDIATION_PROTOCOL = "production_release_remediation_queue_v1"
SOURCE_CONTRACT = {
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}

INTAKE_SPECS: dict[str, dict[str, Any]] = {
    "typed_plan_abstain_review_queue_v1": {
        "proposal_field": "proposed_typed_plan",
        "required_inputs": (
            "bundle_review_items",
            "typed_plans",
            "typed_plans_manifest",
            "release_gate",
            "remediation_queue",
            "remediation_manifest",
        ),
    },
    "formula_evidence_partial_review_queue_v1": {
        "proposal_field": "proposed_evidence_contract",
        "required_inputs": (
            "bundle_review_items",
            "formula_evidence",
            "formula_evidence_manifest",
            "release_gate",
            "remediation_queue",
            "remediation_manifest",
        ),
    },
    "independent_source_replay_intake_queue_v1": {
        "proposal_field": "proposed_replay_contract",
        "required_inputs": (
            "bundle_review_items",
            "typed_plans",
            "typed_plans_manifest",
            "release_gate",
            "remediation_queue",
            "remediation_manifest",
        ),
    },
    "formula_evidence_materialization_intake_queue_v1": {
        "proposal_field": "proposed_formula_evidence_contract",
        "required_inputs": (
            "bundle_review_items",
            "typed_plans",
            "typed_plans_manifest",
            "release_gate",
            "remediation_queue",
            "remediation_manifest",
        ),
    },
    "production_release_remainder_intakes_v1": {
        "proposal_field": "proposed_contract",
        "required_inputs": (
            "bundle_review_items",
            "typed_plans",
            "typed_plans_manifest",
            "release_gate",
            "remediation_queue",
            "remediation_manifest",
        ),
    },
}
ALLOWED_DECISIONS = frozenset({"accept", "reject", "abstain"})
SLOTS = ("reviewer_a", "reviewer_b")
ALLOWED_LABEL_FIELDS = frozenset(
    {
        "schema_version",
        "protocol",
        "assignment_id",
        "immutable_assignment_payload_sha256",
        "reviewer_slot",
        "intake_protocol",
        "intake_kind",
        "question_id",
        "immutable_review_context_sha256",
        "decision",
        "decision_provenance",
        "reviewer_id",
        "reviewed_at",
        "source_coordinates_checked",
        "source_coordinates_sha256",
        "proposed_typed_plan",
        "proposed_evidence_contract",
        "proposed_replay_contract",
        "proposed_formula_evidence_contract",
        "proposed_contract",
        "notes",
        "is_blank_template",
        "materialization_allowed",
        "source_contract",
    }
)
PROPOSAL_FIELDS = frozenset(
    {
        "proposed_typed_plan",
        "proposed_evidence_contract",
        "proposed_replay_contract",
        "proposed_formula_evidence_contract",
        "proposed_contract",
    }
)
FORBIDDEN_PROPOSAL_KEYS = frozenset(
    {
        "answer",
        "answer_value",
        "numeric_answer",
        "numeric_value",
        "raw_value",
        "parsed_value",
        "source_value",
        "selected",
        "selected_value",
        "selected_cell",
        "selected_table",
        "candidate",
        "candidates",
        "candidate_table",
        "candidate_tables",
        "table",
        "table_id",
        "table_uid",
        "internal_table_uid",
        "row",
        "row_index",
        "column",
        "column_index",
        "cell",
        "cell_id",
        "source_cell",
        "evidence_set",
        "execution",
        "execution_result",
        "result",
        "output",
        "submission",
        "promotion",
        "eligibility",
        "materialization_allowed",
    }
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


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def resolve_record_path(path_value: object, *, manifest_path: Path) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("Manifest record lacks a file path")
    candidate = Path(path_value)
    if candidate.is_absolute:
        return candidate
    # Builders record paths relative to the repository root, while compact test
    # fixtures commonly record them relative to their manifest directory.
    if candidate.is_file():
        return candidate
    return manifest_path.parent / candidate


def require_hash(path: Path, expected: object, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256_file(path)
    if actual != require_sha(expected, f"{label} SHA-256"):
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_review_time(value: object, question_id: int) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"Q{question_id}: reviewed_at must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"Q{question_id}: reviewed_at is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Q{question_id}: reviewed_at lacks UTC timezone")
    return value


def require_coordinates(value: object, question_id: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Q{question_id}: completed human review needs source coordinates")
    allowed = frozenset({"source_locator", "document_id", "page_no", "section", "notes"})
    coordinates: list[dict[str, Any]] = []
    for index, coordinate in enumerate(value):
        if not isinstance(coordinate, dict) or set(coordinate).difference(allowed):
            raise ValueError(f"Q{question_id}: source coordinate {index} has unsupported fields")
        if not isinstance(coordinate.get("source_locator"), str) or not coordinate["source_locator"].strip():
            raise ValueError(f"Q{question_id}: source coordinate {index} lacks source_locator")
        if "document_id" in coordinate and (
            not isinstance(coordinate["document_id"], str) or not coordinate["document_id"].strip()
        ):
            raise ValueError(f"Q{question_id}: source coordinate {index} has invalid document_id")
        if "section" in coordinate and (
            not isinstance(coordinate["section"], str) or not coordinate["section"].strip()
        ):
            raise ValueError(f"Q{question_id}: source coordinate {index} has invalid section")
        if "notes" in coordinate and (not isinstance(coordinate["notes"], str) or not coordinate["notes"].strip()):
            raise ValueError(f"Q{question_id}: source coordinate {index} has invalid notes")
        if "page_no" in coordinate and (type(coordinate["page_no"]) is not int or coordinate["page_no"] < 1):
            raise ValueError(f"Q{question_id}: source coordinate {index} has invalid page_no")
        coordinates.append(coordinate)
    return coordinates


def walk_proposal(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_PROPOSAL_KEYS:
                raise ValueError(f"Review proposal contains forbidden key: {key}")
            walk_proposal(child)
    elif isinstance(value, list):
        for child in value:
            walk_proposal(child)


def resolve_output_record(*, intake: Path, manifest: Mapping[str, Any], manifest_path: Path) -> tuple[str, dict[str, Any]]:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("Intake manifest has no outputs")
    matches: list[tuple[str, dict[str, Any]]] = []
    for name, record in outputs.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            continue
        try:
            candidate = resolve_record_path(record.get("path"), manifest_path=manifest_path)
        except ValueError:
            continue
        if candidate.resolve() == intake.resolve():
            matches.append((name, record))
    if len(matches) != 1:
        raise ValueError("Intake path must bind exactly one manifest output")
    return matches[0]


def validate_release_lineage(*, manifest: Mapping[str, Any], manifest_path: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("Intake manifest has no inputs")
    records: dict[str, dict[str, Any]] = {}
    for name in spec["required_inputs"]:
        record = inputs.get(name)
        if not isinstance(record, dict):
            raise ValueError(f"Intake manifest lacks required input: {name}")
        path = resolve_record_path(record.get("path"), manifest_path=manifest_path)
        require_hash(path, record.get("sha256"), name)
        records[name] = {"path": str(path), "sha256": str(record["sha256"])}

    gate = load_json(Path(records["release_gate"]["path"]))
    if (
        gate.get("protocol") != RELEASE_GATE_PROTOCOL
        or gate.get("release_status") != "blocked"
        or gate.get("production_eligible") is not False
        or gate.get("submission_compilation_allowed") is not False
        or gate.get("answer_materialization_allowed") is not False
        or gate.get("source_contract") != SOURCE_CONTRACT
    ):
        raise ValueError("Intake lineage must bind a blocked non-promotable release gate")

    remediation_manifest = load_json(Path(records["remediation_manifest"]["path"]))
    if (
        remediation_manifest.get("protocol") != REMEDIATION_PROTOCOL
        or remediation_manifest.get("queue_status") != "non_materializable"
        or remediation_manifest.get("source_contract") != SOURCE_CONTRACT
        or (((remediation_manifest.get("inputs") or {}).get("release_gate") or {}).get("sha256"))
        != records["release_gate"]["sha256"]
        or (((remediation_manifest.get("outputs") or {}).get("queue") or {}).get("sha256"))
        != records["remediation_queue"]["sha256"]
    ):
        raise ValueError("Intake lineage must bind its immutable remediation queue")
    return records


def validate_intake(*, intake: Path, intake_manifest: Path) -> dict[str, Any]:
    """Return a validated intake descriptor without creating a review manifest."""
    manifest = load_json(intake_manifest)
    protocol = manifest.get("protocol")
    spec = INTAKE_SPECS.get(str(protocol))
    if spec is None:
        raise ValueError("Unsupported production-release intake protocol")
    if (
        manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != SOURCE_CONTRACT
    ):
        raise ValueError("Intake manifest is not blank and non-materializable")
    output_name, output_record = resolve_output_record(intake=intake, manifest=manifest, manifest_path=intake_manifest)
    intake_sha = require_hash(intake, output_record.get("sha256"), "intake queue")
    lineage = validate_release_lineage(manifest=manifest, manifest_path=intake_manifest, spec=spec)

    rows = load_jsonl(intake)
    by_id = {row.get("question_id"): row for row in rows}
    expected_count: object = manifest.get("question_count")
    if protocol == "production_release_remainder_intakes_v1":
        expected_count = ((manifest.get("lane_counts") or {}).get(output_name))
    if type(expected_count) is not int or not rows or len(rows) != len(by_id) or len(rows) != expected_count:
        raise ValueError("Intake queue has missing, duplicate or incomplete question IDs")

    proposal_field = str(spec["proposal_field"])
    for question_id, row in by_id.items():
        if type(question_id) is not int or row.get("protocol") != protocol:
            raise ValueError("Intake queue contains an invalid question ID or protocol")
        context = row.get("review_context")
        decision_contract = row.get("review_decision_contract")
        expected_decision_contract = {
            "decision": None,
            "decision_provenance": None,
            "reviewer_id": None,
            "reviewed_at": None,
            "source_coordinates_checked": None,
            proposal_field: None,
            "materialization_allowed": False,
        }
        if (
            row.get("schema_version") != 1
            or not isinstance(context, dict)
            or row.get("immutable_review_context_sha256") != canonical_sha256(context)
            or decision_contract != expected_decision_contract
            or row.get("materialization_allowed") is not False
            or row.get("source_contract") != SOURCE_CONTRACT
        ):
            raise ValueError(f"Q{question_id}: intake row is not a blank immutable review packet")
        if protocol == "production_release_remainder_intakes_v1":
            if not isinstance(row.get("intake_kind"), str) or not row["intake_kind"].strip():
                raise ValueError(f"Q{question_id}: remainder intake lacks intake_kind")

    return {
        "protocol": protocol,
        "proposal_field": proposal_field,
        "output_name": output_name,
        "intake_sha256": intake_sha,
        "intake_manifest_sha256": sha256_file(intake_manifest),
        "rows_by_id": by_id,
        "lineage": lineage,
    }


def assignment_id(*, descriptor: Mapping[str, Any], reviewer_slot: str, question_id: int) -> str:
    return (
        "production-release-intake-v1-"
        f"{descriptor['protocol']}-{descriptor['output_name']}-{reviewer_slot}-q{question_id}"
    )


def blank_label_template(*, assignment_row: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explicitly blank label; it is not a completed review."""
    return {
        "schema_version": 1,
        "protocol": LABEL_PROTOCOL,
        "assignment_id": assignment_row["assignment_id"],
        "immutable_assignment_payload_sha256": assignment_row["immutable_assignment_payload_sha256"],
        "reviewer_slot": assignment_row["reviewer_slot"],
        "intake_protocol": assignment_row["intake_protocol"],
        "intake_kind": assignment_row["intake_kind"],
        "question_id": assignment_row["question_id"],
        "immutable_review_context_sha256": assignment_row["immutable_review_context_sha256"],
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": None,
        "reviewed_at": None,
        "source_coordinates_checked": None,
        "source_coordinates_sha256": None,
        "proposed_typed_plan": None,
        "proposed_evidence_contract": None,
        "proposed_replay_contract": None,
        "proposed_formula_evidence_contract": None,
        "proposed_contract": None,
        "notes": "",
        "is_blank_template": True,
        "materialization_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }


def build_assignments(*, intake: Path, intake_manifest: Path, output_dir: Path) -> dict[str, Any]:
    """Create two blind, immutable review assignments from one intake queue.

    Assignments and templates preserve the source-only intake exactly.  They do
    not complete a review and cannot materialize an evidence or route change.
    """
    descriptor = validate_intake(intake=intake, intake_manifest=intake_manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "production_release_intake_independent_review_assignments_v1.manifest.json"
    paths = {
        slot: {
            "assignment": output_dir / f"production_release_intake_{slot}_assignment_v1.jsonl",
            "label_template": output_dir / f"production_release_intake_{slot}_label_template_v1.jsonl",
        }
        for slot in SLOTS
    }
    if manifest_path.exists() or any(path.exists() for records in paths.values() for path in records.values()):
        raise FileExistsError(f"Refusing to overwrite intake-review assignments: {output_dir}")
    rows_by_id = descriptor["rows_by_id"]
    outputs: dict[str, dict[str, dict[str, str]]] = {}
    for slot in SLOTS:
        assignments: list[dict[str, Any]] = []
        for position, question_id in enumerate(sorted(rows_by_id), start=1):
            payload = rows_by_id[question_id]
            assignments.append(
                {
                    "schema_version": 1,
                    "protocol": ASSIGNMENT_PROTOCOL,
                    "assignment_id": assignment_id(
                        descriptor=descriptor, reviewer_slot=slot, question_id=question_id
                    ),
                    "assignment_position": position,
                    "reviewer_slot": slot,
                    "intake_protocol": descriptor["protocol"],
                    "intake_kind": payload.get("intake_kind"),
                    "question_id": question_id,
                    "immutable_review_context_sha256": payload["immutable_review_context_sha256"],
                    "immutable_assignment_payload_sha256": canonical_sha256(payload),
                    "intake_payload": payload,
                    "materialization_allowed": False,
                    "source_contract": SOURCE_CONTRACT,
                }
            )
        templates = [blank_label_template(assignment_row=row) for row in assignments]
        write_jsonl(paths[slot]["assignment"], assignments)
        write_jsonl(paths[slot]["label_template"], templates)
        outputs[slot] = {
            "assignment": {
                "path": str(paths[slot]["assignment"]),
                "sha256": sha256_file(paths[slot]["assignment"]),
            },
            "label_template": {
                "path": str(paths[slot]["label_template"]),
                "sha256": sha256_file(paths[slot]["label_template"]),
            },
        }
    result = {
        "schema_version": 1,
        "protocol": ASSIGNMENT_PROTOCOL,
        "reviewer_slots": list(SLOTS),
        "assignment_count_per_reviewer": len(rows_by_id),
        "blind_to_other_review": True,
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "intake": {"path": str(intake), "sha256": descriptor["intake_sha256"]},
            "intake_manifest": {"path": str(intake_manifest), "sha256": descriptor["intake_manifest_sha256"]},
            **descriptor["lineage"],
        },
        "outputs": outputs,
        "source_contract": SOURCE_CONTRACT,
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def validate_assignment(
    *, assignment: Path, assignment_manifest: Path, reviewer_slot: str
) -> dict[str, Any]:
    """Validate one immutable, blind assignment and its blank template."""
    if reviewer_slot not in SLOTS:
        raise ValueError("Reviewer slot is invalid")
    manifest = load_json(assignment_manifest)
    if (
        manifest.get("protocol") != ASSIGNMENT_PROTOCOL
        or manifest.get("reviewer_slots") != list(SLOTS)
        or manifest.get("blind_to_other_review") is not True
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != SOURCE_CONTRACT
    ):
        raise ValueError("Review assignment manifest is not blank, independent and non-materializable")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("Review assignment manifest lacks intake lineage")
    intake_record, intake_manifest_record = inputs.get("intake"), inputs.get("intake_manifest")
    if not isinstance(intake_record, Mapping) or not isinstance(intake_manifest_record, Mapping):
        raise ValueError("Review assignment manifest lacks intake records")
    intake = resolve_record_path(intake_record.get("path"), manifest_path=assignment_manifest)
    intake_manifest = resolve_record_path(intake_manifest_record.get("path"), manifest_path=assignment_manifest)
    descriptor = validate_intake(intake=intake, intake_manifest=intake_manifest)
    if (
        descriptor["intake_sha256"] != intake_record.get("sha256")
        or descriptor["intake_manifest_sha256"] != intake_manifest_record.get("sha256")
        or any((inputs.get(name) or {}).get("sha256") != record["sha256"] for name, record in descriptor["lineage"].items())
    ):
        raise ValueError("Review assignment manifest is not bound to its intake release lineage")
    output = (manifest.get("outputs") or {}).get(reviewer_slot)
    if not isinstance(output, Mapping):
        raise ValueError("Review assignment manifest lacks reviewer output")
    assignment_sha = require_hash(assignment, ((output.get("assignment") or {}).get("sha256")), "reviewer assignment")
    template_path = resolve_record_path(
        ((output.get("label_template") or {}).get("path")), manifest_path=assignment_manifest
    )
    require_hash(template_path, ((output.get("label_template") or {}).get("sha256")), "reviewer label template")
    assignments = load_jsonl(assignment)
    assignments_by_id = {row.get("question_id"): row for row in assignments}
    if (
        len(assignments) != len(assignments_by_id)
        or set(assignments_by_id) != set(descriptor["rows_by_id"])
        or len(assignments) != manifest.get("assignment_count_per_reviewer")
    ):
        raise ValueError("Reviewer assignment must cover the intake exactly once")
    for question_id, row in assignments_by_id.items():
        payload = row.get("intake_payload")
        expected_payload = descriptor["rows_by_id"][question_id]
        if (
            type(question_id) is not int
            or row.get("schema_version") != 1
            or row.get("protocol") != ASSIGNMENT_PROTOCOL
            or row.get("assignment_id") != assignment_id(
                descriptor=descriptor, reviewer_slot=reviewer_slot, question_id=question_id
            )
            or row.get("reviewer_slot") != reviewer_slot
            or row.get("intake_protocol") != descriptor["protocol"]
            or row.get("intake_kind") != expected_payload.get("intake_kind")
            or row.get("immutable_review_context_sha256") != expected_payload.get("immutable_review_context_sha256")
            or not isinstance(payload, dict)
            or payload != expected_payload
            or row.get("immutable_assignment_payload_sha256") != canonical_sha256(payload)
            or row.get("materialization_allowed") is not False
            or row.get("source_contract") != SOURCE_CONTRACT
        ):
            raise ValueError(f"Q{question_id}: reviewer assignment is malformed")
    templates = load_jsonl(template_path)
    templates_by_id = {row.get("question_id"): row for row in templates}
    if len(templates) != len(templates_by_id) or set(templates_by_id) != set(assignments_by_id):
        raise ValueError("Reviewer label template must cover the assignment exactly once")
    for question_id, template in templates_by_id.items():
        expected = blank_label_template(assignment_row=assignments_by_id[question_id])
        if template != expected:
            raise ValueError(f"Q{question_id}: reviewer label template is not an immutable blank")
    return {"descriptor": descriptor, "assignments_by_id": assignments_by_id, "assignment_sha256": assignment_sha}


def verify(
    *, assignment: Path, assignment_manifest: Path, completed_labels: Path,
    reviewer_slot: str, reviewer_id: str, output: Path,
) -> dict[str, Any]:
    """Verify a complete reviewer label file against one immutable intake."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite human-review manifest: {output}")
    if not reviewer_id.strip():
        raise ValueError("A non-empty reviewer ID is required")
    assignment_descriptor = validate_assignment(
        assignment=assignment, assignment_manifest=assignment_manifest, reviewer_slot=reviewer_slot
    )
    descriptor = assignment_descriptor["descriptor"]
    assignments_by_id = assignment_descriptor["assignments_by_id"]
    rows_by_id = descriptor["rows_by_id"]
    proposal_field = descriptor["proposal_field"]
    labels = load_jsonl(completed_labels)
    labels_by_id = {row.get("question_id"): row for row in labels}
    if len(labels) != len(labels_by_id) or set(labels_by_id) != set(rows_by_id):
        raise ValueError("Completed human labels must cover exactly the immutable intake")

    for question_id, label in labels_by_id.items():
        if type(question_id) is not int or set(label).difference(ALLOWED_LABEL_FIELDS):
            raise ValueError(f"Q{question_id}: completed human label has unsupported fields")
        row = rows_by_id[question_id]
        assignment_row = assignments_by_id[question_id]
        if (
            label.get("schema_version") != 1
            or label.get("protocol") != LABEL_PROTOCOL
            or label.get("assignment_id") != assignment_row.get("assignment_id")
            or label.get("immutable_assignment_payload_sha256")
            != assignment_row.get("immutable_assignment_payload_sha256")
            or label.get("reviewer_slot") != reviewer_slot
            or label.get("intake_protocol") != descriptor["protocol"]
            or label.get("intake_kind") != row.get("intake_kind")
            or label.get("immutable_review_context_sha256") != row.get("immutable_review_context_sha256")
            or label.get("decision") not in ALLOWED_DECISIONS
            or label.get("decision_provenance") != "human_verified"
            or label.get("reviewer_id") != reviewer_id
            or label.get("is_blank_template") is not False
            or label.get("materialization_allowed") is not False
            or label.get("source_contract") != SOURCE_CONTRACT
            or not isinstance(label.get("notes"), str)
            or not label["notes"].strip()
        ):
            raise ValueError(f"Q{question_id}: completed human label does not bind the immutable intake")
        require_review_time(label.get("reviewed_at"), question_id)
        coordinates = require_coordinates(label.get("source_coordinates_checked"), question_id)
        if label.get("source_coordinates_sha256") != canonical_sha256({"source_coordinates_checked": coordinates}):
            raise ValueError(f"Q{question_id}: source-coordinate hash does not bind checked coordinates")
        proposals = {field: label.get(field) for field in PROPOSAL_FIELDS}
        if label["decision"] == "accept":
            proposal = proposals.pop(proposal_field)
            if not isinstance(proposal, dict) or not proposal or any(value is not None for value in proposals.values()):
                raise ValueError(f"Q{question_id}: accepted review needs exactly one non-empty proposal contract")
            walk_proposal(proposal)
        elif any(value is not None for value in proposals.values()):
            raise ValueError(f"Q{question_id}: non-accept review must not include a proposal contract")

    labels_sha = sha256_file(completed_labels)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "intake_protocol": descriptor["protocol"],
        "intake_output_name": descriptor["output_name"],
        "reviewer_id": reviewer_id,
        "reviewer_slot": reviewer_slot,
        "blind_to_other_review": True,
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "answer_materialization_allowed": False,
        "inputs": {
            "assignment": {"path": str(assignment), "sha256": assignment_descriptor["assignment_sha256"]},
            "assignment_manifest": {"path": str(assignment_manifest), "sha256": sha256_file(assignment_manifest)},
            "intake": {"path": str(resolve_record_path(((load_json(assignment_manifest).get("inputs") or {}).get("intake") or {}).get("path"), manifest_path=assignment_manifest)), "sha256": descriptor["intake_sha256"]},
            "intake_manifest": {"path": str(resolve_record_path(((load_json(assignment_manifest).get("inputs") or {}).get("intake_manifest") or {}).get("path"), manifest_path=assignment_manifest)), "sha256": descriptor["intake_manifest_sha256"]},
            "completed_labels": {"path": str(completed_labels), "sha256": labels_sha},
            **descriptor["lineage"],
        },
        "outputs": {"labels": {"path": str(completed_labels), "sha256": labels_sha}},
        "counts": {
            "label_count": len(labels),
            "decision_counts": dict(sorted(Counter(str(row["decision"]) for row in labels).items())),
            "intake_kind_counts": dict(sorted(Counter(str(row.get("intake_kind") or "unspecified") for row in labels).items())),
        },
        "source_contract": SOURCE_CONTRACT,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--assignment-manifest", type=Path, required=True)
    parser.add_argument("--completed-labels", type=Path, required=True)
    parser.add_argument("--reviewer-slot", choices=SLOTS, required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(verify(**vars(args))["protocol"])


if __name__ == "__main__":
    main()
