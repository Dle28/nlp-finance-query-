#!/usr/bin/env python3
"""Build a narrow source-coordinate queue for unresolved multi-component plans.

These packets prove only that each literal component can be reviewed against a
candidate source table.  They cannot accept the missing selection, aggregation
or arithmetic that connects those components into a whole-question program.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "computational_semantic_source_review_queue",
    ROOT / "scripts" / "build_computational_semantic_source_review_queue_v1.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_SOURCE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SOURCE)


PROTOCOL = "computational_semantic_component_source_review_queue_v1"
SEMANTIC_PROTOCOL = "computational_semantic_plan_v1"
NORMALIZATION_PROTOCOL = "source_preserving_report_normalization_v2"
SOURCE_CONTRACT = _SOURCE.SOURCE_CONTRACT


def build(
    *,
    questions: Path,
    semantic_plans: Path,
    semantic_manifest: Path,
    table_catalog: Path,
    table_catalog_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create blank component-coordinate packets for fully table-routable components."""
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "computational_semantic_component_source_review_queue_v1.jsonl"
    manifest_path = output_dir / "computational_semantic_component_source_review_queue_v1.manifest.json"
    if queue_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite component source-review queue: {output_dir}")

    semantic_meta = _SOURCE.load_json(semantic_manifest)
    catalog_meta = _SOURCE.load_json(table_catalog_manifest)
    semantic_sha = _SOURCE.require_hash(
        semantic_plans,
        (semantic_meta.get("output") or {}).get("sha256"),
        "semantic plans",
    )
    catalog_sha = _SOURCE.require_hash(table_catalog, catalog_meta.get("table_catalog_sha256"), "table catalog")
    questions_sha = _SOURCE.sha256_file(questions)
    if (
        semantic_meta.get("protocol") != SEMANTIC_PROTOCOL
        or semantic_meta.get("source_contract", {}).get("submission_eligible") is not False
        or ((semantic_meta.get("inputs") or {}).get("questions") or {}).get("sha256") != questions_sha
        or ((semantic_meta.get("inputs") or {}).get("table_catalog") or {}).get("sha256") != catalog_sha
    ):
        raise ValueError("Semantic plan manifest has an invalid or unbound source contract")
    if (
        catalog_meta.get("protocol") != NORMALIZATION_PROTOCOL
        or catalog_meta.get("source_contract", {}).get("evidence_eligible") is not False
        or catalog_meta.get("source_contract", {}).get("may_select_value_cell") is not False
    ):
        raise ValueError("Table catalog manifest is not metadata-only")

    questions_by_id = _SOURCE.index(_SOURCE.load_jsonl(questions), "id", "questions")
    plans_by_id = _SOURCE.index(_SOURCE.load_jsonl(semantic_plans), "question_id", "semantic plans")
    catalog_by_uid = _SOURCE.index(_SOURCE.load_jsonl(table_catalog), "internal_table_uid", "table catalog")
    if (
        semantic_meta.get("question_count") != len(questions_by_id)
        or semantic_meta.get("question_id_count") != len(plans_by_id)
        or set(questions_by_id) != set(plans_by_id)
    ):
        raise ValueError("Semantic plans must cover question records exactly")

    packets: list[dict[str, Any]] = []
    for question_id in sorted(plans_by_id, key=int):
        plan = plans_by_id[question_id]
        routes = list(plan.get("table_routes") or [])
        if (
            plan.get("semantic_status") != "partial"
            or plan.get("semantic_intent") != "known_multiple_components_only"
            or "UNSUPPORTED_COMPOSITION_TEMPLATE" not in (plan.get("reason_codes") or [])
            or not routes
            or any(route.get("status") != "candidate_tables_found" for route in routes)
        ):
            continue
        source_candidates = [
            _SOURCE._candidate_packet(plan=plan, route=route, catalog_by_uid=catalog_by_uid)
            for route in routes
        ]
        item = questions_by_id[question_id]
        review_context = {
            "question": item.get("question"),
            "question_plan": item.get("question_plan"),
            "semantic_plan": {
                "semantic_status": plan.get("semantic_status"),
                "semantic_intent": plan.get("semantic_intent"),
                "question_context": plan.get("question_context"),
                "semantic_axes": plan.get("semantic_axes"),
                "nodes": plan.get("nodes"),
                "output_node_id": plan.get("output_node_id"),
                "reason_codes": plan.get("reason_codes"),
            },
            "source_candidates": source_candidates,
        }
        packets.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": int(question_id),
                "immutable_review_context_sha256": _SOURCE.canonical_sha256(review_context),
                "review_context": review_context,
                "review_instructions": [
                    "Open each listed candidate independently and confirm only its literal source component, entity, period, unit and dimension.",
                    "Also verify the population/entities in the original question; a candidate table does not repair an incomplete population contract.",
                    "Record source coordinates only. Do not record a value, answer, formula result, selected period/entity, label or eligibility decision.",
                    "An accept means individual components were reviewed; it does not accept the unresolved composition or permit execution/materialization.",
                ],
                "review_decision_contract": {
                    "decision": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_coordinates_checked": None,
                    "period_unit_dimension_checked": None,
                    "notes": "",
                    "composition_accepted": False,
                    "materialization_allowed": False,
                },
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    if not packets:
        raise ValueError("No fully candidate-routable unresolved multi-component plans found")
    if len({packet["question_id"] for packet in packets}) != len(packets):
        raise ValueError("Component source review queue contains duplicate question IDs")

    queue_path.write_text(
        "".join(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n" for packet in packets),
        encoding="utf-8",
    )
    variable_counts = Counter(
        str(candidate["variable_id"])
        for packet in packets
        for candidate in packet["review_context"]["source_candidates"]
    )
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_status": "blank_component_source_coordinate_review",
        "question_count": len(packets),
        "question_ids": [packet["question_id"] for packet in packets],
        "source_variable_counts": dict(sorted(variable_counts.items())),
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "composition_execution_allowed": False,
        "inputs": {
            "questions": {"path": str(questions), "sha256": questions_sha},
            "semantic_plans": {"path": str(semantic_plans), "sha256": semantic_sha},
            "semantic_manifest": {"path": str(semantic_manifest), "sha256": _SOURCE.sha256_file(semantic_manifest)},
            "table_catalog": {"path": str(table_catalog), "sha256": catalog_sha},
            "table_catalog_manifest": {"path": str(table_catalog_manifest), "sha256": _SOURCE.sha256_file(table_catalog_manifest)},
        },
        "outputs": {"queue": {"path": str(queue_path), "sha256": _SOURCE.sha256_file(queue_path)}},
        "source_contract": SOURCE_CONTRACT,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--semantic-plans", type=Path, required=True)
    parser.add_argument("--semantic-manifest", type=Path, required=True)
    parser.add_argument("--table-catalog", type=Path, required=True)
    parser.add_argument("--table-catalog-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build(**{name: value.resolve() for name, value in vars(args).items()})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
