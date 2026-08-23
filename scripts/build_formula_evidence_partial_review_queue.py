#!/usr/bin/env python3
"""Build a blank source-review intake for partial Formula EvidenceSets.

It exposes formula/operand requirements and explicitly missing gates, but never
copies selected matches, source values, cells, execution results, labels, or
answers into review work.  Any completion must be rebuilt by the Formula
EvidenceSet producer and independently audited afterwards.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "formula_evidence_partial_review_queue_v1"
RELEASE_GATE_PROTOCOL = "production_release_gate_v1"
REMEDIATION_PROTOCOL = "production_release_remediation_queue_v1"
LANE = "formula_evidence_completion"
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


def _validate_release_gate(path: Path) -> dict[str, Any]:
    gate = load_json(path)
    if (
        gate.get("protocol") != RELEASE_GATE_PROTOCOL
        or gate.get("release_status") != "blocked"
        or gate.get("production_eligible") is not False
        or gate.get("submission_compilation_allowed") is not False
        or gate.get("answer_materialization_allowed") is not False
        or (gate.get("source_contract") or {}) != SOURCE_CONTRACT
    ):
        raise ValueError("Formula intake requires a blocked non-promotable release gate")
    return gate


def _validate_remediation_queue(
    *, path: Path, manifest_path: Path, release_gate: Path, formula_sha: str
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
    if ((manifest.get("inputs") or {}).get("formula_evidence") or {}).get("sha256") != formula_sha:
        raise ValueError("Remediation queue is not bound to Formula EvidenceSets")
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
        raise ValueError("Remediation queue contains no formula-evidence completion lane")
    return lane_ids


def _formula_contract(row: Mapping[str, Any]) -> dict[str, Any]:
    formula = row.get("formula") or {}
    if not isinstance(formula, Mapping):
        raise ValueError(f"Q{row.get('id')}: formula must be an object")
    operands: list[dict[str, Any]] = []
    for operand in formula.get("operands") or []:
        if not isinstance(operand, Mapping):
            raise ValueError(f"Q{row.get('id')}: formula operand must be an object")
        operands.append(
            {
                key: operand.get(key)
                for key in (
                    "operand_id", "label", "entity", "role", "stage_id", "years",
                    "required", "allowed_table_functions", "metric_hints",
                )
            }
        )
    return {
        "formula_id": formula.get("formula_id"),
        "label": formula.get("label"),
        "definition_status": formula.get("definition_status"),
        "execution_status": formula.get("execution_status"),
        "output_unit": formula.get("output_unit"),
        "operands": operands,
    }


def build(
    *, bundle_dir: Path, formula_evidence: Path, release_gate: Path,
    remediation_queue: Path, remediation_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    """Write one blank, source-only intake record for each partial FormulaSet."""
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "formula_evidence_partial_review_queue_v1.jsonl"
    manifest_path = output_dir / "formula_evidence_partial_review_queue_v1.manifest.json"
    if queue_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite Formula EvidenceSet intake: {output_dir}")
    gate = _validate_release_gate(release_gate)
    formula_manifest_path = formula_evidence.with_suffix(".manifest.json")
    formula_manifest = load_json(formula_manifest_path)
    formula_sha = require_hash(formula_evidence, formula_manifest.get("sidecar_sha256"), "Formula EvidenceSets")
    review_items_path = bundle_dir / "review_items.jsonl"
    review_items_sha = sha256_file(review_items_path)
    if (
        formula_manifest.get("bundle_review_items_sha256") != review_items_sha
        or _release_artifact_sha(gate, "formula_evidence") != formula_sha
        or _release_artifact_sha(gate, "bundle_review_items") != review_items_sha
    ):
        raise ValueError("Formula EvidenceSet/review-item lineage does not bind the release gate")
    items = index(load_jsonl(review_items_path), "id", "review items")
    formulas = index(load_jsonl(formula_evidence), "id", "Formula EvidenceSets")
    if not set(formulas) <= set(items):
        raise ValueError("Formula EvidenceSets reference unknown review items")
    lane_ids = _validate_remediation_queue(
        path=remediation_queue,
        manifest_path=remediation_manifest,
        release_gate=release_gate,
        formula_sha=formula_sha,
    )
    partial_ids = {
        question_id for question_id, row in formulas.items()
        if row.get("evidence_completeness") == "partial"
    }
    if lane_ids != partial_ids:
        raise ValueError("Formula remediation lane does not match every partial Formula EvidenceSet")
    rows: list[dict[str, Any]] = []
    for question_id in sorted(partial_ids, key=lambda value: (str((formulas[value].get("formula") or {}).get("formula_id") or ""), value)):
        formula_row = formulas[question_id]
        context = {
            "question": items[question_id].get("question"),
            "question_plan": items[question_id].get("question_plan"),
            "formula_contract": _formula_contract(formula_row),
            "evidence_completeness": "partial",
            "reason_codes": sorted(str(value) for value in formula_row.get("reason_codes") or []),
            "missing_operand_ids": sorted(str(value) for value in formula_row.get("missing_operand_ids") or []),
        }
        rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "formula_id": context["formula_contract"]["formula_id"],
                "immutable_review_context_sha256": canonical_sha256(context),
                "review_context": context,
                "review_instructions": [
                    "Reopen independently identified source material for each required operand.",
                    "Retain partial when an exact operand binding, entity, period, scope or literal unit is ambiguous.",
                    "Do not put source values, selected matches, table/cell coordinates, execution values, answers or eligibility decisions in this intake record.",
                    "Completion requires a newly built Formula EvidenceSet and its existing exact-row validator; this queue cannot complete it.",
                ],
                "review_decision_contract": {
                    "decision": None,
                    "decision_provenance": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_coordinates_checked": None,
                    "proposed_evidence_contract": None,
                    "materialization_allowed": False,
                },
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    if len({row["question_id"] for row in rows}) != len(rows) or {row["question_id"] for row in rows} != partial_ids:
        raise ValueError("Formula intake queue must cover each partial Formula EvidenceSet exactly once")
    queue_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    formula_counts = Counter(str(row["formula_id"]) for row in rows)
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_status": "blank_source_review_intake",
        "question_count": len(rows),
        "formula_counts": dict(sorted(formula_counts.items())),
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "bundle_review_items": {"path": str(review_items_path), "sha256": review_items_sha},
            "formula_evidence": {"path": str(formula_evidence), "sha256": formula_sha},
            "formula_evidence_manifest": {"path": str(formula_manifest_path), "sha256": sha256_file(formula_manifest_path)},
            "release_gate": {"path": str(release_gate), "sha256": sha256_file(release_gate)},
            "remediation_queue": {"path": str(remediation_queue), "sha256": sha256_file(remediation_queue)},
            "remediation_manifest": {"path": str(remediation_manifest), "sha256": sha256_file(remediation_manifest)},
        },
        "outputs": {"queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)}},
        "source_contract": SOURCE_CONTRACT,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
    parser.add_argument("--release-gate", type=Path, required=True)
    parser.add_argument("--remediation-queue", type=Path, required=True)
    parser.add_argument("--remediation-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(**vars(args))["formula_counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
