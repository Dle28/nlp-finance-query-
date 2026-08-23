#!/usr/bin/env python3
"""Build blank, hash-bound scope-review packets for complete semantic routes.

The input plan may name one or more possible report scopes.  This builder keeps
those options separate and asks a reviewer to prove scope from source context;
it never treats candidate availability as a scope decision.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "computational_semantic_scope_review_queue_v1"
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
    "may_propose_scope_for_review": True,
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


def _bindings(table: Mapping[str, Any], variable_id: str) -> list[dict[str, Any]]:
    return [
        dict(value)
        for value in table.get("canonical_variables") or []
        if isinstance(value, Mapping) and str(value.get("variable_id") or "") == variable_id
    ]


def _candidate_tables(
    *,
    plan: Mapping[str, Any],
    route: Mapping[str, Any],
    scope: str,
    catalog_by_uid: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    nodes = {
        str(node.get("node_id") or ""): node
        for node in plan.get("nodes") or []
        if isinstance(node, Mapping) and node.get("op") == "source_lookup"
    }
    node_id = str(route.get("node_id") or "")
    node = nodes.get(node_id)
    variable_id = str(route.get("variable_id") or "")
    if node is None or variable_id != str(node.get("variable_id") or ""):
        raise ValueError(f"Q{plan.get('question_id')}: invalid source-route node binding")
    context = dict(plan.get("question_context") or {})
    entities = {str(value) for value in context.get("entities") or []}
    years = {int(value) for value in context.get("years") or []}
    table_types = {str(value) for value in node.get("statement_types") or []}
    if not entities or not years or not table_types:
        raise ValueError(f"Q{plan.get('question_id')}: incomplete entity/year/table-type context")
    ids_by_scope = route.get("candidate_table_uids_by_scope") or {}
    raw_ids = ids_by_scope.get(scope)
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError(f"Q{plan.get('question_id')}: missing candidate tables for scope {scope!r}")

    output: list[dict[str, Any]] = []
    for value in sorted({str(uid) for uid in raw_ids if str(uid)}):
        table = catalog_by_uid.get(value)
        if table is None:
            raise ValueError(f"Q{plan.get('question_id')}: scope candidate is absent from catalog")
        header_years = {int(year) for year in table.get("available_period_years") or []}
        report_year = table.get("report_year")
        table_years = header_years | ({int(report_year)} if isinstance(report_year, int) else set())
        bindings = _bindings(table, variable_id)
        if (
            table.get("routing_eligible") is not True
            or str(table.get("company") or "") not in entities
            or str(table.get("report_scope") or "") != scope
            or str(table.get("table_type") or "") not in table_types
            or not years.intersection(table_years)
            or not bindings
        ):
            raise ValueError(f"Q{plan.get('question_id')}: scope candidate violates source route contract")
        output.append(
            {
                "internal_table_uid": value,
                "document_id": table.get("document_id"),
                "company": table.get("company"),
                "report_year": table.get("report_year"),
                "report_scope": table.get("report_scope"),
                "table_type": table.get("table_type"),
                "available_period_years": sorted(table_years),
                "canonical_variable_bindings": bindings,
            }
        )
    return output


def _scope_options(
    *, plan: Mapping[str, Any], routes: list[Mapping[str, Any]], catalog_by_uid: Mapping[str, Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    candidate_scope_sets = [
        set(str(scope) for scope in (route.get("candidate_table_uids_by_scope") or {}) if str(scope))
        for route in routes
    ]
    common_scopes = set.intersection(*candidate_scope_sets) if candidate_scope_sets else set()
    if not common_scopes:
        raise ValueError(f"Q{plan.get('question_id')}: source routes have no common scope option")
    options: dict[str, list[dict[str, Any]]] = {}
    for scope in sorted(common_scopes):
        options[scope] = [
            {
                "node_id": route.get("node_id"),
                "variable_id": route.get("variable_id"),
                "candidate_tables": _candidate_tables(
                    plan=plan,
                    route=route,
                    scope=scope,
                    catalog_by_uid=catalog_by_uid,
                ),
            }
            for route in routes
        ]
    return options


def build(
    *,
    questions: Path,
    semantic_plans: Path,
    semantic_manifest: Path,
    table_catalog: Path,
    table_catalog_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create blank review packets for complete plans blocked only on report scope."""
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "computational_semantic_scope_review_queue_v1.jsonl"
    manifest_path = output_dir / "computational_semantic_scope_review_queue_v1.manifest.json"
    if queue_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite scope-review queue: {output_dir}")

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
        context = dict(plan.get("question_context") or {})
        if (
            plan.get("semantic_status") != "context_blocked"
            or context.get("scope") is not None
            or not routes
            or any(route.get("status") != "scope_ambiguous_candidates" for route in routes)
        ):
            continue
        options = _scope_options(plan=plan, routes=routes, catalog_by_uid=catalog_by_uid)
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
            "scope_options": options,
        }
        packets.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": int(question_id),
                "immutable_review_context_sha256": canonical_sha256(review_context),
                "review_context": review_context,
                "review_instructions": [
                    "Open source documents independently and determine the intended report scope from document title, question wording and source context.",
                    "Do not choose a scope merely because it is the only candidate catalogued; catalog coverage is navigation metadata, not scope evidence.",
                    "When a plan has multiple source variables, an accepted scope must support all listed source routes.",
                    "Reject when the source table does not represent a required counterparty, sector, instrument, ownership level or other detail dimension.",
                    "Record a proposed scope and source coordinates only; do not record numeric values, answers, labels, execution results or eligibility decisions.",
                    "An accepted scope remains non-materializable until an independent source-binding/provenance gate approves it.",
                ],
                "review_decision_contract": {
                    "decision": None,
                    "proposed_scope": None,
                    "scope_evidence_kind": None,
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
        raise ValueError("No complete scope-ambiguous semantic plans found")
    if len({packet["question_id"] for packet in packets}) != len(packets):
        raise ValueError("Scope review queue contains duplicate question IDs")

    queue_path.write_text(
        "".join(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n" for packet in packets),
        encoding="utf-8",
    )
    option_counts = Counter(
        "+".join(sorted(packet["review_context"]["scope_options"])) for packet in packets
    )
    intent_counts = Counter(
        str(packet["review_context"]["semantic_plan"]["semantic_intent"]) for packet in packets
    )
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_status": "blank_scope_review",
        "question_count": len(packets),
        "question_ids": [packet["question_id"] for packet in packets],
        "scope_option_counts": dict(sorted(option_counts.items())),
        "semantic_intent_counts": dict(sorted(intent_counts.items())),
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
