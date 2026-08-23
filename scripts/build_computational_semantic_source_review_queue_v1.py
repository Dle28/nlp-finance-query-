#!/usr/bin/env python3
"""Build blank, hash-bound source-coordinate review packets for semantic routes.

This intake is deliberately narrower than an evidence set.  It exposes only
questions whose every required source variable has at least one eligible table
candidate after entity/year/scope filtering.  A reviewer may propose source
coordinates, but the queue cannot materialize an answer, label, evidence row,
or eligibility decision.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "computational_semantic_source_review_queue_v1"
SEMANTIC_PROTOCOL = "computational_semantic_plan_v1"
NORMALIZATION_PROTOCOL = "source_preserving_report_normalization_v2"
SOURCE_CONTRACT = {
    "candidate_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "materialization_allowed": False,
    "may_compute_answer": False,
    "may_select_value_cell": False,
    "may_propose_source_coordinates_for_review": True,
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


def index(rows: Iterable[Mapping[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in output:
            raise ValueError(f"{label} has an invalid or duplicate {key}: {value!r}")
        output[value] = dict(row)
    return output


def require_hash(path: Path, expected: object, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _has_variable(row: Mapping[str, Any], variable_id: str) -> list[dict[str, Any]]:
    return [
        dict(value)
        for value in row.get("canonical_variables") or []
        if isinstance(value, Mapping) and str(value.get("variable_id") or "") == variable_id
    ]


def _candidate_packet(
    *,
    plan: Mapping[str, Any],
    route: Mapping[str, Any],
    catalog_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    node_id = str(route.get("node_id") or "")
    source_nodes = {
        str(node.get("node_id") or ""): node
        for node in plan.get("nodes") or []
        if isinstance(node, Mapping) and node.get("op") == "source_lookup"
    }
    node = source_nodes.get(node_id)
    if node is None:
        raise ValueError(f"Q{plan.get('question_id')}: route refers to non-source node {node_id!r}")
    variable_id = str(route.get("variable_id") or "")
    if variable_id != str(node.get("variable_id") or ""):
        raise ValueError(f"Q{plan.get('question_id')}: route variable does not match source node")
    candidate_uids = sorted({str(value) for value in route.get("candidate_table_uids") or [] if str(value)})
    if not candidate_uids:
        raise ValueError(f"Q{plan.get('question_id')}: complete route has no candidate table UID")

    context = dict(plan.get("question_context") or {})
    entities = {str(value) for value in context.get("entities") or []}
    years = {int(value) for value in context.get("years") or []}
    scope = str(context.get("scope") or "")
    table_types = {str(value) for value in node.get("statement_types") or []}
    if not entities or not years or not scope or not table_types:
        raise ValueError(f"Q{plan.get('question_id')}: source route lacks complete context")

    candidates: list[dict[str, Any]] = []
    for uid in candidate_uids:
        table = catalog_by_uid.get(uid)
        if table is None:
            raise ValueError(f"Q{plan.get('question_id')}: candidate table UID is absent from catalog")
        header_years = {int(value) for value in table.get("available_period_years") or []}
        row_year = table.get("report_year")
        table_years = header_years | ({int(row_year)} if isinstance(row_year, int) else set())
        bindings = _has_variable(table, variable_id)
        if (
            table.get("routing_eligible") is not True
            or str(table.get("company") or "") not in entities
            or not years.intersection(table_years)
            or str(table.get("report_scope") or "") != scope
            or str(table.get("table_type") or "") not in table_types
            or not bindings
        ):
            raise ValueError(f"Q{plan.get('question_id')}: candidate table violates semantic route contract")
        candidates.append(
            {
                "internal_table_uid": uid,
                "document_id": table.get("document_id"),
                "company": table.get("company"),
                "report_year": table.get("report_year"),
                "report_scope": table.get("report_scope"),
                "table_type": table.get("table_type"),
                "available_period_years": sorted(table_years),
                "canonical_variable_bindings": bindings,
            }
        )
    return {
        "node_id": node_id,
        "variable_id": variable_id,
        "period_type": node.get("period_type"),
        "statement_types": sorted(table_types),
        "candidate_tables": candidates,
    }


def build(
    *,
    questions: Path,
    semantic_plans: Path,
    semantic_manifest: Path,
    table_catalog: Path,
    table_catalog_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create one blank review packet per fully candidate-routable semantic plan."""
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "computational_semantic_source_review_queue_v1.jsonl"
    manifest_path = output_dir / "computational_semantic_source_review_queue_v1.manifest.json"
    if queue_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite source-review queue: {output_dir}")

    semantic_meta = load_json(semantic_manifest)
    catalog_meta = load_json(table_catalog_manifest)
    semantic_sha = require_hash(semantic_plans, (semantic_meta.get("output") or {}).get("sha256"), "semantic plans")
    catalog_sha = require_hash(table_catalog, catalog_meta.get("table_catalog_sha256"), "table catalog")
    questions_sha = sha256_file(questions)
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

    questions_by_id = index(load_jsonl(questions), "id", "questions")
    plans_by_id = index(load_jsonl(semantic_plans), "question_id", "semantic plans")
    catalog_by_uid = index(load_jsonl(table_catalog), "internal_table_uid", "table catalog")
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
        if plan.get("semantic_status") != "typed_candidate" or not routes:
            continue
        if any(route.get("status") != "candidate_tables_found" for route in routes):
            continue
        source_candidates = [
            _candidate_packet(plan=plan, route=route, catalog_by_uid=catalog_by_uid)
            for route in routes
        ]
        item = questions_by_id[question_id]
        context = {
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
                "immutable_review_context_sha256": canonical_sha256(context),
                "review_context": context,
                "review_instructions": [
                    "Open the listed source table independently by internal_table_uid and document_id.",
                    "Confirm entity, report scope, period header, canonical row, unit and any missing dimension before accepting a route.",
                    "Record coordinates only; do not record a numeric value, answer, formula result, execution result, label or eligibility decision.",
                    "Reject when a table is only company-level while the question requires a counterparty, sector, instrument or other unmodelled dimension.",
                    "Accepting this packet does not create evidence or permit materialization; a later independent source-binding gate is required.",
                ],
                "review_decision_contract": {
                    "decision": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_coordinates_checked": None,
                    "period_unit_dimension_checked": None,
                    "notes": "",
                    "materialization_allowed": False,
                },
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    if not packets:
        raise ValueError("No fully candidate-routable typed semantic plans found")
    if len({row["question_id"] for row in packets}) != len(packets):
        raise ValueError("Source review queue contains duplicate question IDs")

    queue_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in packets),
        encoding="utf-8",
    )
    intent_counts = Counter(str(row["review_context"]["semantic_plan"]["semantic_intent"]) for row in packets)
    variable_counts = Counter(
        str(candidate["variable_id"])
        for row in packets
        for candidate in row["review_context"]["source_candidates"]
    )
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_status": "blank_source_coordinate_review",
        "question_count": len(packets),
        "question_ids": [row["question_id"] for row in packets],
        "semantic_intent_counts": dict(sorted(intent_counts.items())),
        "source_variable_counts": dict(sorted(variable_counts.items())),
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "questions": {"path": str(questions), "sha256": questions_sha},
            "semantic_plans": {"path": str(semantic_plans), "sha256": semantic_sha},
            "semantic_manifest": {"path": str(semantic_manifest), "sha256": sha256_file(semantic_manifest)},
            "table_catalog": {"path": str(table_catalog), "sha256": catalog_sha},
            "table_catalog_manifest": {"path": str(table_catalog_manifest), "sha256": sha256_file(table_catalog_manifest)},
        },
        "outputs": {"queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)}},
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
