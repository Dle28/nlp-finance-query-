#!/usr/bin/env python3
"""Build blank, hash-bound review packets for unmodelled lookup dimensions.

The semantic planner deliberately abstains when a reported statement line is
qualified by a dimension that the current taxonomy cannot represent (for
example counterparty, measurement basis, instrument or provision).  This
builder turns those abstentions into a small, human-reviewable intake without
turning a guessed dimension into a source binding, answer or label.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "computational_semantic_dimension_review_queue_v1"
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
    "may_propose_dimension_contract_for_review": True,
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


def _candidate_table_ids(route: Mapping[str, Any]) -> list[str]:
    """Collect metadata-only candidate UIDs from direct and scope-separated routes."""
    ids = {str(value) for value in route.get("candidate_table_uids") or [] if str(value)}
    for yearly_routes in (route.get("by_scope_and_year") or {}).values():
        if not isinstance(yearly_routes, list):
            raise ValueError("Invalid scope-separated source route")
        for yearly_route in yearly_routes:
            if not isinstance(yearly_route, Mapping):
                raise ValueError("Invalid yearly source route")
            ids.update(str(value) for value in yearly_route.get("candidate_table_uids") or [] if str(value))
    return sorted(ids)


def _matching_bindings(table: Mapping[str, Any], variable_id: str) -> list[dict[str, Any]]:
    return [
        dict(binding)
        for binding in table.get("canonical_variables") or []
        if isinstance(binding, Mapping) and str(binding.get("variable_id") or "") == variable_id
    ]


def _navigation_candidates(
    *, route: Mapping[str, Any], catalog_by_uid: Mapping[str, Mapping[str, Any]], question_id: str
) -> list[dict[str, Any]]:
    """Expose only catalog metadata, never values, coordinates or answer evidence."""
    variable_id = str(route.get("variable_id") or "")
    if not variable_id:
        raise ValueError(f"Q{question_id}: source route has no canonical variable")
    output: list[dict[str, Any]] = []
    for uid in _candidate_table_ids(route):
        table = catalog_by_uid.get(uid)
        if table is None:
            raise ValueError(f"Q{question_id}: source route candidate is absent from table catalog")
        bindings = _matching_bindings(table, variable_id)
        if table.get("routing_eligible") is not True or not bindings:
            raise ValueError(f"Q{question_id}: source route candidate violates catalog contract")
        output.append(
            {
                "internal_table_uid": uid,
                "document_id": table.get("document_id"),
                "company": table.get("company"),
                "report_year": table.get("report_year"),
                "report_scope": table.get("report_scope"),
                "table_type": table.get("table_type"),
                "available_period_years": table.get("available_period_years"),
                "canonical_variable_bindings": bindings,
            }
        )
    return output


def _route_diagnostics(
    *, plan: Mapping[str, Any], catalog_by_uid: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    source_nodes = {
        str(node.get("node_id") or ""): node
        for node in plan.get("nodes") or []
        if isinstance(node, Mapping) and node.get("op") == "source_lookup"
    }
    diagnostics: list[dict[str, Any]] = []
    for route in plan.get("table_routes") or []:
        if not isinstance(route, Mapping):
            raise ValueError(f"Q{plan.get('question_id')}: invalid table route")
        node_id = str(route.get("node_id") or "")
        variable_id = str(route.get("variable_id") or "")
        node = source_nodes.get(node_id)
        if node is None or variable_id != str(node.get("variable_id") or ""):
            raise ValueError(f"Q{plan.get('question_id')}: route does not bind a source node")
        diagnostics.append(
            {
                "node_id": node_id,
                "variable_id": variable_id,
                "period_type": node.get("period_type"),
                "statement_types": sorted(str(value) for value in node.get("statement_types") or []),
                "route_status": route.get("status"),
                "candidate_table_count": len(_candidate_table_ids(route)),
                "candidate_table_metadata": _navigation_candidates(
                    route=route,
                    catalog_by_uid=catalog_by_uid,
                    question_id=str(plan.get("question_id")),
                ),
            }
        )
    if not diagnostics:
        raise ValueError(f"Q{plan.get('question_id')}: dimension-blocked plan has no source routes")
    return diagnostics


def build(
    *,
    questions: Path,
    semantic_plans: Path,
    semantic_manifest: Path,
    table_catalog: Path,
    table_catalog_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create one blank packet per direct lookup blocked on an unmodelled dimension."""
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "computational_semantic_dimension_review_queue_v1.jsonl"
    manifest_path = output_dir / "computational_semantic_dimension_review_queue_v1.manifest.json"
    if queue_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite dimension-review queue: {output_dir}")

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
        dimensions = list(plan.get("unmodelled_detail_dimensions") or [])
        if (
            plan.get("semantic_intent") != "reported_value_lookup"
            or plan.get("semantic_status") != "partial"
            or "UNMODELED_DETAIL_DIMENSION" not in (plan.get("reason_codes") or [])
            or not dimensions
        ):
            continue
        if any(not isinstance(dimension, str) or not dimension for dimension in dimensions):
            raise ValueError(f"Q{question_id}: invalid unmodelled dimension identifier")
        item = questions_by_id[question_id]
        review_context = {
            "question": item.get("question"),
            "question_plan": item.get("question_plan"),
            "semantic_plan": {
                "semantic_status": plan.get("semantic_status"),
                "semantic_intent": plan.get("semantic_intent"),
                "question_context": plan.get("question_context"),
                "semantic_axes": plan.get("semantic_axes"),
                "unmodelled_detail_dimensions": sorted(set(dimensions)),
                "nodes": plan.get("nodes"),
                "output_node_id": plan.get("output_node_id"),
                "reason_codes": plan.get("reason_codes"),
            },
            "source_route_diagnostics": _route_diagnostics(plan=plan, catalog_by_uid=catalog_by_uid),
        }
        packets.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": int(question_id),
                "immutable_review_context_sha256": canonical_sha256(review_context),
                "review_context": review_context,
                "review_instructions": [
                    "Reopen the original report and determine whether each listed detail cue changes the requested financial fact.",
                    "Use candidate table metadata only to navigate; it is not proof that the table contains the qualified fact.",
                    "If supported, propose an evidence-preserving semantic dimension contract (dimension identity, selector wording and representation needed), not a value or answer.",
                    "Reject a broad statement-line mapping when the question requires a counterparty, industry, instrument, schedule, provision or measurement basis not represented by that line.",
                    "Record no numeric value, answer, formula result, execution output, training label or eligibility decision.",
                    "An accepted contract remains non-materializable until taxonomy, source binding, provenance, period/unit and execution gates independently approve it.",
                ],
                "review_decision_contract": {
                    "decision": None,
                    "proposed_dimension_contract": None,
                    "dimension_evidence_kind": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_coordinates_checked": None,
                    "notes": "",
                    "materialization_allowed": False,
                },
                "materialization_allowed": False,
                "source_contract": SOURCE_CONTRACT,
            }
        )
    if not packets:
        raise ValueError("No dimension-blocked reported lookups found")
    if len({packet["question_id"] for packet in packets}) != len(packets):
        raise ValueError("Dimension review queue contains duplicate question IDs")

    queue_path.write_text(
        "".join(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n" for packet in packets),
        encoding="utf-8",
    )
    dimension_counts = Counter(
        dimension
        for packet in packets
        for dimension in packet["review_context"]["semantic_plan"]["unmodelled_detail_dimensions"]
    )
    route_status_counts = Counter(
        str(route["route_status"])
        for packet in packets
        for route in packet["review_context"]["source_route_diagnostics"]
    )
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_status": "blank_dimension_contract_review",
        "question_count": len(packets),
        "question_ids": [packet["question_id"] for packet in packets],
        "detail_dimension_counts": dict(sorted(dimension_counts.items())),
        "route_status_counts": dict(sorted(route_status_counts.items())),
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
