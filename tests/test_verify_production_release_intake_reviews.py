from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"
spec = importlib.util.spec_from_file_location(
    "verify_production_release_intake_reviews",
    ROOT / "scripts" / "verify_production_release_intake_reviews.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": _sha(path)}


def _fixture(tmp_path: Path, *, remainder: bool = False) -> dict[str, Path | str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_contract = mod.SOURCE_CONTRACT
    release_gate = tmp_path / "release_gate.json"
    _write_json(
        release_gate,
        {
            "protocol": "production_release_gate_v1",
            "release_status": "blocked",
            "production_eligible": False,
            "submission_compilation_allowed": False,
            "answer_materialization_allowed": False,
            "source_contract": source_contract,
        },
    )
    remediation_queue = tmp_path / "remediation.jsonl"
    _write_jsonl(remediation_queue, [{"question_id": 7, "materialization_allowed": False}])
    remediation_manifest = tmp_path / "remediation.manifest.json"
    _write_json(
        remediation_manifest,
        {
            "protocol": "production_release_remediation_queue_v1",
            "queue_status": "non_materializable",
            "source_contract": source_contract,
            "inputs": {"release_gate": _record(release_gate)},
            "outputs": {"queue": _record(remediation_queue)},
        },
    )
    bundle = tmp_path / "review_items.jsonl"
    typed = tmp_path / "typed.jsonl"
    typed_manifest = tmp_path / "typed.manifest.json"
    for path in (bundle, typed, typed_manifest):
        path.write_text("{}\n", encoding="utf-8")

    if remainder:
        protocol = "production_release_remainder_intakes_v1"
        proposal_field = "proposed_contract"
        intake_kind = "blank_executor_contract_review"
        output_name = "deterministic_executor_compile"
    else:
        protocol = "typed_plan_abstain_review_queue_v1"
        proposal_field = "proposed_typed_plan"
        intake_kind = None
        output_name = "queue"
    context = {"question": "Question 7", "typed_contract": {"scope": "consolidated"}}
    decision_contract = {
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": None,
        "reviewed_at": None,
        "source_coordinates_checked": None,
        proposal_field: None,
        "materialization_allowed": False,
    }
    row = {
        "schema_version": 1,
        "protocol": protocol,
        "question_id": 7,
        "immutable_review_context_sha256": mod.canonical_sha256(context),
        "review_context": context,
        "review_decision_contract": decision_contract,
        "materialization_allowed": False,
        "source_contract": source_contract,
    }
    if intake_kind is not None:
        row["intake_kind"] = intake_kind
    intake = tmp_path / "intake.jsonl"
    _write_jsonl(intake, [row])
    intake_manifest = tmp_path / "intake.manifest.json"
    inputs = {
        "bundle_review_items": _record(bundle),
        "typed_plans": _record(typed),
        "typed_plans_manifest": _record(typed_manifest),
        "release_gate": _record(release_gate),
        "remediation_queue": _record(remediation_queue),
        "remediation_manifest": _record(remediation_manifest),
    }
    manifest = {
        "protocol": protocol,
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "source_contract": source_contract,
        "inputs": inputs,
        "outputs": {output_name: _record(intake)},
    }
    if remainder:
        manifest["lane_counts"] = {output_name: 1}
    else:
        manifest["question_count"] = 1
    _write_json(intake_manifest, manifest)

    assignments = mod.build_assignments(
        intake=intake,
        intake_manifest=intake_manifest,
        output_dir=tmp_path / "assignments",
    )
    assignment_manifest = Path(assignments["manifest_path"])
    assignment = Path(assignments["outputs"]["reviewer_a"]["assignment"]["path"])
    assignment_row = json.loads(assignment.read_text().splitlines()[0])
    coordinates = [{"source_locator": "report.pdf#page=1", "page_no": 1}]
    label = {
        "schema_version": 1,
        "protocol": "production_release_intake_review_label_v1",
        "assignment_id": assignment_row["assignment_id"],
        "immutable_assignment_payload_sha256": assignment_row["immutable_assignment_payload_sha256"],
        "reviewer_slot": "reviewer_a",
        "intake_protocol": protocol,
        "intake_kind": intake_kind,
        "question_id": 7,
        "immutable_review_context_sha256": row["immutable_review_context_sha256"],
        "decision": "accept",
        "decision_provenance": "human_verified",
        "reviewer_id": "human-a",
        "reviewed_at": "2026-08-13T00:00:00Z",
        "source_coordinates_checked": coordinates,
        "source_coordinates_sha256": mod.canonical_sha256({"source_coordinates_checked": coordinates}),
        "notes": "Reopened the report and verified the source context.",
        "is_blank_template": False,
        "materialization_allowed": False,
        "source_contract": source_contract,
    }
    label[proposal_field] = {"entities": ["ABC"], "scope": "consolidated", "operation": "direct_lookup"}
    labels = tmp_path / "completed_labels.jsonl"
    _write_jsonl(labels, [label])
    return {
        "assignment": assignment,
        "assignment_manifest": assignment_manifest,
        "completed_labels": labels,
        "reviewer_slot": "reviewer_a",
        "reviewer_id": "human-a",
        "proposal_field": proposal_field,
    }


def test_intake_review_verifier_requires_full_hash_bound_handoff(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = mod.verify(**{key: value for key, value in paths.items() if key != "proposal_field"}, output=tmp_path / "review.json")
    assert result["protocol"] == "production_release_intake_human_review_v1"
    assert result["counts"] == {
        "label_count": 1,
        "decision_counts": {"accept": 1},
        "intake_kind_counts": {"unspecified": 1},
    }
    assert result["materialization_allowed"] is False
    assert result["answer_materialization_allowed"] is False
    assert result["source_contract"]["submission_eligible"] is False


def test_intake_review_verifier_rejects_unsafe_or_incomplete_labels(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels_path = Path(paths["completed_labels"])
    labels = [json.loads(line) for line in labels_path.read_text().splitlines()]
    labels[0][str(paths["proposal_field"])] = {"raw_value": "forbidden"}
    _write_jsonl(labels_path, labels)
    with pytest.raises(ValueError, match="forbidden key"):
        mod.verify(**{key: value for key, value in paths.items() if key != "proposal_field"}, output=tmp_path / "unsafe.json")

    paths = _fixture(tmp_path / "incomplete")
    Path(paths["completed_labels"]).write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="cover exactly"):
        mod.verify(**{key: value for key, value in paths.items() if key != "proposal_field"}, output=tmp_path / "incomplete.json")


def test_intake_review_verifier_rejects_label_from_another_assignment(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    labels_path = Path(paths["completed_labels"])
    labels = [json.loads(line) for line in labels_path.read_text().splitlines()]
    labels[0]["reviewer_slot"] = "reviewer_b"
    _write_jsonl(labels_path, labels)
    with pytest.raises(ValueError, match="does not bind"):
        mod.verify(**{key: value for key, value in paths.items() if key != "proposal_field"}, output=tmp_path / "wrong-assignment.json")


def test_intake_review_verifier_binds_remainder_intake_kind(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, remainder=True)
    result = mod.verify(**{key: value for key, value in paths.items() if key != "proposal_field"}, output=tmp_path / "remainder.json")
    assert result["intake_protocol"] == "production_release_remainder_intakes_v1"
    assert result["intake_output_name"] == "deterministic_executor_compile"
    assert result["counts"]["intake_kind_counts"] == {"blank_executor_contract_review": 1}


def test_all_current_release_intakes_have_immutable_release_lineage() -> None:
    inputs = [
        (
            V5 / "typed_plan_abstain_review_intake_v1" / "typed_plan_abstain_review_queue_v1.jsonl",
            V5 / "typed_plan_abstain_review_intake_v1" / "typed_plan_abstain_review_queue_v1.manifest.json",
        ),
        (
            V5 / "formula_evidence_partial_review_intake_v1" / "formula_evidence_partial_review_queue_v1.jsonl",
            V5 / "formula_evidence_partial_review_intake_v1" / "formula_evidence_partial_review_queue_v1.manifest.json",
        ),
        (
            V5 / "independent_source_replay_intake_v1" / "independent_source_replay_intake_queue_v1.jsonl",
            V5 / "independent_source_replay_intake_v1" / "independent_source_replay_intake_queue_v1.manifest.json",
        ),
        (
            V5 / "formula_evidence_materialization_intake_v1" / "formula_evidence_materialization_intake_queue_v1.jsonl",
            V5 / "formula_evidence_materialization_intake_v1" / "formula_evidence_materialization_intake_queue_v1.manifest.json",
        ),
    ]
    remainder_manifest = V5 / "remainder_intakes_v1" / "production_release_remainder_intakes_v1.manifest.json"
    inputs.extend(
        (
            V5 / "remainder_intakes_v1" / f"{lane}_intake_v1.jsonl",
            remainder_manifest,
        )
        for lane in (
            "deterministic_executor_compile",
            "exact_source_conflict_adjudication",
            "query_program_shadow_completion",
        )
    )
    counts = [mod.validate_intake(intake=intake, intake_manifest=manifest)["intake_sha256"] for intake, manifest in inputs]
    assert len(counts) == 7
    assert all(len(value) == 64 for value in counts)


def test_real_intake_assignment_is_blind_complete_and_nonmaterializing(tmp_path: Path) -> None:
    intake = V5 / "typed_plan_abstain_review_intake_v1" / "typed_plan_abstain_review_queue_v1.jsonl"
    manifest = V5 / "typed_plan_abstain_review_intake_v1" / "typed_plan_abstain_review_queue_v1.manifest.json"
    result = mod.build_assignments(intake=intake, intake_manifest=manifest, output_dir=tmp_path / "assignments")
    assert result["assignment_count_per_reviewer"] == 336
    assert result["blind_to_other_review"] is True
    assert result["labels_prepopulated"] is False
    assert result["materialization_allowed"] is False
    template = Path(result["outputs"]["reviewer_a"]["label_template"]["path"])
    rows = [json.loads(line) for line in template.read_text().splitlines()]
    assert len(rows) == 336
    assert all(row["decision"] is None and row["is_blank_template"] is True for row in rows)
