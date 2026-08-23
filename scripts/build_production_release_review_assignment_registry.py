#!/usr/bin/env python3
"""Build a read-only registry of all V1 remediation review assignments.

The registry is an operational index only. It verifies every source intake and
both slot assignments, then writes their hashes and counts. It never creates a
review decision, evidence, answer, release permission, or materialized route.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "verify_production_release_intake_reviews",
    ROOT / "scripts" / "verify_production_release_intake_reviews.py",
)
_review = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_review)


PROTOCOL = "production_release_review_assignment_registry_v1"
LANES = (
    ("typed_plan_source_adjudication", "typed_plan_abstain_review_intake_v1", "typed_plan_abstain_review_queue_v1.jsonl", "typed_plan_abstain_review_queue_v1.manifest.json", "typed_plan_abstain_review_assignments_v1"),
    ("formula_evidence_completion", "formula_evidence_partial_review_intake_v1", "formula_evidence_partial_review_queue_v1.jsonl", "formula_evidence_partial_review_queue_v1.manifest.json", "formula_evidence_partial_review_assignments_v1"),
    ("independent_source_replay", "independent_source_replay_intake_v1", "independent_source_replay_intake_queue_v1.jsonl", "independent_source_replay_intake_queue_v1.manifest.json", "independent_source_replay_review_assignments_v1"),
    ("formula_evidence_materialization", "formula_evidence_materialization_intake_v1", "formula_evidence_materialization_intake_queue_v1.jsonl", "formula_evidence_materialization_intake_queue_v1.manifest.json", "formula_evidence_materialization_review_assignments_v1"),
    ("deterministic_executor_compile", "remainder_intakes_v1", "deterministic_executor_compile_intake_v1.jsonl", "production_release_remainder_intakes_v1.manifest.json", "deterministic_executor_compile_review_assignments_v1"),
    ("exact_source_conflict_adjudication", "remainder_intakes_v1", "exact_source_conflict_adjudication_intake_v1.jsonl", "production_release_remainder_intakes_v1.manifest.json", "exact_source_conflict_review_assignments_v1"),
    ("query_program_shadow_completion", "remainder_intakes_v1", "query_program_shadow_completion_intake_v1.jsonl", "production_release_remainder_intakes_v1.manifest.json", "query_program_shadow_review_assignments_v1"),
)


def build(*, root: Path, output: Path) -> dict[str, Any]:
    """Verify all seven V1 lanes and write an immutable assignment index."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite assignment registry: {output}")
    entries: list[dict[str, Any]] = []
    total = 0
    all_ids: set[int] = set()
    for lane, intake_dir, intake_name, manifest_name, assignment_dir in LANES:
        intake = root / intake_dir / intake_name
        intake_manifest = root / intake_dir / manifest_name
        assignment_manifest = root / assignment_dir / "production_release_intake_independent_review_assignments_v1.manifest.json"
        manifest = _review.load_json(assignment_manifest)
        slot_records: dict[str, dict[str, str]] = {}
        lane_ids: set[int] | None = None
        for slot in _review.SLOTS:
            assignment = Path(((manifest.get("outputs") or {}).get(slot) or {}).get("assignment", {}).get("path", ""))
            result = _review.validate_assignment(
                assignment=assignment, assignment_manifest=assignment_manifest, reviewer_slot=slot
            )
            ids = set(result["assignments_by_id"])
            if lane_ids is None:
                lane_ids = ids
            elif ids != lane_ids:
                raise ValueError(f"{lane}: reviewer assignments do not cover the same IDs")
            slot_records[slot] = {
                "assignment_path": str(assignment),
                "assignment_sha256": result["assignment_sha256"],
                "label_template_path": str(
                    ((manifest.get("outputs") or {}).get(slot) or {}).get("label_template", {}).get("path", "")
                ),
                "label_template_sha256": str(
                    ((manifest.get("outputs") or {}).get(slot) or {}).get("label_template", {}).get("sha256", "")
                ),
            }
        if lane_ids is None:
            raise ValueError(f"{lane}: no review assignment IDs")
        if all_ids.intersection(lane_ids):
            raise ValueError(f"{lane}: remediation lanes overlap")
        all_ids.update(lane_ids)
        total += len(lane_ids)
        entries.append(
            {
                "lane": lane,
                "question_count": len(lane_ids),
                "intake": {"path": str(intake), "sha256": _review.sha256_file(intake)},
                "intake_manifest": {"path": str(intake_manifest), "sha256": _review.sha256_file(intake_manifest)},
                "assignment_manifest": {"path": str(assignment_manifest), "sha256": _review.sha256_file(assignment_manifest)},
                "reviewer_assignments": slot_records,
            }
        )
    if total != 957 or len(all_ids) != 957:
        raise ValueError("Review assignments must cover the 957-ID remediation backlog exactly once")
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "assignment_status": "blank_independent_human_review",
        "question_count": total,
        "reviewer_slots": list(_review.SLOTS),
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "lanes": entries,
        "source_contract": _review.SOURCE_CONTRACT,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build(**vars(args))["question_count"])


if __name__ == "__main__":
    main()
