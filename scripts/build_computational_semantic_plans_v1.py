#!/usr/bin/env python3
"""Materialize hash-bound computational semantic plans and table routes."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from finance_query.computational_semantics import (
    COMPUTATIONAL_SEMANTICS_PROTOCOL,
    build_computational_semantic_plan,
    source_contract,
)
from finance_query.financial_taxonomy import FinancialTaxonomy
from finance_query.metric_registry import FinancialMetricRegistry


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON object records")
    return rows


def _question_id(item: Mapping[str, Any]) -> int:
    raw_id = item.get("id") if "id" in item else item.get("question_id")
    if raw_id is None or isinstance(raw_id, bool):
        raise ValueError("Question record is missing a usable id")
    return int(raw_id)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def build_plans(
    *,
    questions: Path,
    taxonomy_path: Path,
    metric_registry_path: Path,
    table_catalog: Path,
    output: Path,
) -> dict[str, Any]:
    """Build exactly one non-promotable semantic plan per explicit question ID."""
    inputs = [questions, taxonomy_path, metric_registry_path, table_catalog]
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(path)
    taxonomy = FinancialTaxonomy.load(taxonomy_path)
    registry = FinancialMetricRegistry.load(metric_registry_path, taxonomy=taxonomy)
    items = load_jsonl(questions)
    catalog_rows = load_jsonl(table_catalog)
    catalog_by_company: dict[str, list[dict[str, Any]]] = {}
    for row in catalog_rows:
        catalog_by_company.setdefault(str(row.get("company") or ""), []).append(row)
    ids = [_question_id(item) for item in items]
    duplicates = sorted(value for value, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"Questions contain duplicate ids: {duplicates}")
    ordered_items = [item for _, item in sorted(zip(ids, items), key=lambda pair: pair[0])]
    rows: list[dict[str, Any]] = []
    for item in ordered_items:
        source_plan = item.get("effective_question_plan") or item.get("question_plan") or {}
        entities = [str(value) for value in source_plan.get("tickers") or []]
        relevant_catalog = [
            row
            for entity in entities
            for row in catalog_by_company.get(entity, [])
        ]
        rows.append(
            build_computational_semantic_plan(
                item,
                taxonomy=taxonomy,
                registry=registry,
                table_catalog_rows=relevant_catalog,
            )
        )
    if [row["question_id"] for row in rows] != sorted(ids):
        raise ValueError("Semantic plan materialization lost or reordered explicit question IDs")

    output.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output, rows)
    status_counts = Counter(str(row["semantic_status"]) for row in rows)
    intent_counts = Counter(str(row["semantic_intent"]) for row in rows)
    difficulty_counts = Counter(str(row["difficulty_candidate"]) for row in rows)
    reason_counts: Counter[str] = Counter()
    for row in rows:
        reason_counts.update(str(value) for value in row.get("reason_codes") or [])
    leaf_count = sum(
        1
        for row in rows
        for node in row.get("nodes") or []
        if node.get("op") in {"source_lookup", "unresolved_source_lookup"}
    )
    canonical_leaf_count = sum(
        1
        for row in rows
        for node in row.get("nodes") or []
        if node.get("op") == "source_lookup"
    )
    table_route_status_counts: Counter[str] = Counter(
        str(route.get("status") or "")
        for row in rows
        for route in row.get("table_routes") or []
    )
    routed_leaf_count = sum(
        1
        for row in rows
        for route in row.get("table_routes") or []
        if route.get("status") == "candidate_tables_found"
    )
    candidate_leaf_count = sum(
        1
        for row in rows
        for route in row.get("table_routes") or []
        if route.get("status") in {"candidate_tables_found", "scope_ambiguous_candidates"}
    )
    manifest = {
        "schema_version": 1,
        "protocol": COMPUTATIONAL_SEMANTICS_PROTOCOL,
        "question_count": len(rows),
        "question_id_count": len({row["question_id"] for row in rows}),
        "semantic_status_counts": dict(sorted(status_counts.items())),
        "semantic_intent_counts": dict(sorted(intent_counts.items())),
        "difficulty_candidate_counts": dict(sorted(difficulty_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "source_leaf_count": leaf_count,
        "canonical_source_leaf_count": canonical_leaf_count,
        "fully_routed_source_leaf_count": routed_leaf_count,
        "source_leaf_with_semantic_table_candidates_count": candidate_leaf_count,
        "table_route_status_counts": dict(sorted(table_route_status_counts.items())),
        "inputs": {
            "questions": {"path": str(questions), "sha256": sha256_file(questions)},
            "taxonomy": {"path": str(taxonomy_path), "sha256": sha256_file(taxonomy_path)},
            "metric_registry": {"path": str(metric_registry_path), "sha256": sha256_file(metric_registry_path)},
            "table_catalog": {"path": str(table_catalog), "sha256": sha256_file(table_catalog)},
        },
        "output": {"path": str(output), "sha256": sha256_file(output)},
        "source_contract": source_contract(),
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--metric-registry", type=Path, required=True)
    parser.add_argument("--table-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_plans(
        questions=args.questions.resolve(),
        taxonomy_path=args.taxonomy.resolve(),
        metric_registry_path=args.metric_registry.resolve(),
        table_catalog=args.table_catalog.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
