#!/usr/bin/env python3
"""Build a non-promotable financial taxonomy candidate sidecar and coverage audit."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from finance_query.financial_taxonomy import FinancialTaxonomy, TAXONOMY_PROTOCOL
from finance_query.report_normalization import normalize_financial_variable, source_row_metric_label


SIDECAR_PROTOCOL = "financial_taxonomy_candidate_materialization_v1"
PRIMARY_STATEMENT_TYPES = {
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "equity_change_statement",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def table_source_labels(table: dict[str, Any]) -> list[str]:
    headers = {int(value) for value in table.get("header_row_indices") or []}
    return [
        source_row_metric_label(row)
        for row_index, row in enumerate(table.get("rows") or [])
        if row_index not in headers and source_row_metric_label(row)
    ]


def build_candidates(
    *,
    structured_tables: Path,
    routing_catalog: Path,
    taxonomy_path: Path,
    output: Path,
) -> dict[str, Any]:
    taxonomy = FinancialTaxonomy.load(taxonomy_path)
    tables = load_jsonl(structured_tables)
    catalog_rows = load_jsonl(routing_catalog)
    catalog = {str(row.get("internal_table_uid") or ""): row for row in catalog_rows}
    if "" in catalog or len(catalog) != len(catalog_rows):
        raise ValueError("Routing catalog has missing or duplicate table UIDs")
    table_uids = {str(row.get("internal_table_uid") or "") for row in tables}
    if "" in table_uids or len(table_uids) != len(tables):
        raise ValueError("Structured tables have missing or duplicate table UIDs")
    if table_uids != set(catalog):
        raise ValueError("Structured tables and routing catalog do not cover the same UIDs")

    labels_by_document: dict[str, list[str]] = defaultdict(list)
    for table in tables:
        labels_by_document[str(table.get("document_id") or "")].extend(table_source_labels(table))
    sector_rows: dict[str, dict[str, Any]] = {}
    sector_counts: Counter[str] = Counter()
    for document_id, labels in sorted(labels_by_document.items()):
        result = taxonomy.infer_sector(labels)
        sector_counts[str(result["sector"])] += 1
        sector_rows[document_id] = {
            "schema_version": 1,
            "protocol": SIDECAR_PROTOCOL,
            "record_kind": "document_sector_candidate",
            "document_id": document_id,
            **result,
            "source_contract": {
                "navigation_metadata_only": True,
                "evidence_eligible": False,
                "training_eligible": False,
                "submission_eligible": False,
            },
        }

    status_counts: Counter[str] = Counter()
    concept_counts: Counter[str] = Counter()
    axis_counts: Counter[str] = Counter()
    table_type_counts: Counter[str] = Counter()
    navigation_gate_counts: Counter[str] = Counter()
    labelled_rows = 0
    baseline_matches = 0
    unique_labels: set[str] = set()
    candidate_records: list[dict[str, Any]] = []
    role_concepts_by_table: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for table in tables:
        uid = str(table["internal_table_uid"])
        catalog_row = catalog[uid]
        document_id = str(table.get("document_id") or "")
        sector = str(sector_rows.get(document_id, {}).get("sector") or "unknown")
        table_type = str(catalog_row.get("table_type") or "other")
        headers = {int(value) for value in table.get("header_row_indices") or []}
        for row_index, row in enumerate(table.get("rows") or []):
            if row_index in headers:
                continue
            label = source_row_metric_label(row)
            if not label:
                continue
            labelled_rows += 1
            unique_labels.add(label)
            if normalize_financial_variable(row) is not None:
                baseline_matches += 1
            classification = taxonomy.classify_row(
                row,
                source_label=label,
                table_type=table_type,
                sector=sector,
            )
            unconstrained = taxonomy.classify_row(
                row,
                source_label=label,
                table_type=None,
                sector=sector,
            )
            if unconstrained["match_status"] == "exact_unique":
                concept_id = str(unconstrained["concept_candidates"][0]["concept_id"])
                concept = taxonomy.by_id[concept_id]
                primary_types = set(concept.statement_types).intersection(PRIMARY_STATEMENT_TYPES)
                # A literal accounting code is a strong primary-statement cue.
                # Without it, a concept declared for both a note and a primary
                # statement does not vote for table-role promotion.
                if "notes" in concept.statement_types and not concept.account_codes:
                    primary_types = set()
                for candidate_type in primary_types:
                    role_concepts_by_table[uid][candidate_type].add(concept_id)
            status = str(classification["match_status"])
            status_counts[status] += 1
            unique_candidate = (
                classification["concept_candidates"][0]
                if status == "exact_unique"
                else None
            )
            navigation_reason_codes: list[str] = []
            if status != "exact_unique":
                navigation_reason_codes.append("concept_not_exact_unique")
            if not bool(catalog_row.get("routing_eligible")):
                navigation_reason_codes.append("table_not_routing_eligible")
            if (
                unique_candidate is not None
                and "sector" in unique_candidate.get("constraints_applied", [])
                and sector == "unknown"
            ):
                navigation_reason_codes.append("sector_unresolved")
            navigation_gate_status = "ready" if not navigation_reason_codes else "blocked"
            navigation_gate_counts[navigation_gate_status] += 1
            if status == "unmatched":
                continue
            table_type_counts[table_type] += 1
            for candidate in classification["concept_candidates"]:
                concept_counts[str(candidate["concept_id"])] += 1
            for axis_name, values in classification["semantic_axes"].items():
                for value in values:
                    axis_counts[f"{axis_name}:{value}"] += 1
            candidate_records.append(
                {
                    "schema_version": 1,
                    "protocol": SIDECAR_PROTOCOL,
                    "record_kind": "row_semantic_candidate",
                    "internal_table_uid": uid,
                    "document_id": document_id,
                    "row_index": row_index,
                    "raw_source_row": [str(value) for value in row],
                    "table_type": table_type,
                    "table_type_status": catalog_row.get("table_type_status"),
                    "routing_eligible": bool(catalog_row.get("routing_eligible")),
                    "sector_candidate": sector,
                    "navigation_gate_status": navigation_gate_status,
                    "navigation_reason_codes": navigation_reason_codes,
                    **classification,
                }
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in candidate_records:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    sectors_output = output.with_name(output.stem + ".sectors.jsonl")
    with sectors_output.open("w", encoding="utf-8") as handle:
        for row in sector_rows.values():
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    table_roles_output = output.with_name(output.stem + ".table_roles.jsonl")
    table_role_counts: Counter[str] = Counter()
    table_role_rows: list[dict[str, Any]] = []
    for table in tables:
        uid = str(table["internal_table_uid"])
        existing = catalog[uid]
        existing_type = str(existing.get("table_type") or "other")
        existing_status = str(existing.get("table_type_status") or "metadata_provisional")
        votes = role_concepts_by_table.get(uid, {})
        ranked = sorted(
            ((candidate_type, len(concepts)) for candidate_type, concepts in votes.items()),
            key=lambda item: (-item[1], item[0]),
        )
        reason_codes: list[str] = []
        if existing_status == "source_structural" and existing_type in PRIMARY_STATEMENT_TYPES:
            status = "source_structural"
            proposed_type = existing_type
        elif not ranked or ranked[0][1] < 2:
            status = "insufficient_support"
            proposed_type = None
            reason_codes.append("fewer_than_two_distinct_primary_statement_concepts")
        elif len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            status = "ambiguous"
            proposed_type = None
            reason_codes.append("tied_primary_statement_concept_votes")
        else:
            status = "semantic_candidate"
            proposed_type = ranked[0][0]
            reason_codes.append("requires_independent_table_role_review")
        table_role_counts[status] += 1
        context_path = [
            str(value)
            for value in (
                (table.get("table_function") or {}).get("label"),
                (table.get("table_section") or {}).get("label"),
                (table.get("context_trace") or {}).get("source_title"),
            )
            if str(value or "").strip()
        ]
        table_role_rows.append(
            {
                "schema_version": 1,
                "protocol": SIDECAR_PROTOCOL,
                "record_kind": "table_role_candidate",
                "internal_table_uid": uid,
                "document_id": str(table.get("document_id") or ""),
                "existing_table_type": existing_type,
                "existing_table_type_status": existing_status,
                "proposed_table_type": proposed_type,
                "status": status,
                "reason_codes": reason_codes,
                "concept_votes": {
                    key: sorted(values) for key, values in sorted(votes.items())
                },
                "context_path": context_path,
                "source_contract": {
                    "navigation_metadata_only": True,
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
            }
        )
    with table_roles_output.open("w", encoding="utf-8") as handle:
        for row in table_role_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    concept_candidate_rows = sum(
        count for status, count in status_counts.items() if status in {"exact_unique", "exact_ambiguous"}
    )
    manifest = {
        "schema_version": 1,
        "protocol": SIDECAR_PROTOCOL,
        "taxonomy_protocol": TAXONOMY_PROTOCOL,
        "table_count": len(tables),
        "document_count": len(sector_rows),
        "labelled_row_count": labelled_rows,
        "unique_source_label_count": len(unique_labels),
        "baseline_exact_variable_row_count": baseline_matches,
        "baseline_exact_variable_coverage": baseline_matches / labelled_rows if labelled_rows else 0.0,
        "taxonomy_concept_candidate_row_count": concept_candidate_rows,
        "taxonomy_concept_candidate_coverage": concept_candidate_rows / labelled_rows if labelled_rows else 0.0,
        "strict_navigation_ready_row_count": navigation_gate_counts["ready"],
        "strict_navigation_ready_coverage": navigation_gate_counts["ready"] / labelled_rows if labelled_rows else 0.0,
        "candidate_record_count": len(candidate_records),
        "match_status_counts": dict(sorted(status_counts.items())),
        "navigation_gate_counts": dict(sorted(navigation_gate_counts.items())),
        "concept_counts": dict(sorted(concept_counts.items())),
        "semantic_axis_counts": dict(sorted(axis_counts.items())),
        "candidate_table_type_counts": dict(sorted(table_type_counts.items())),
        "document_sector_candidate_counts": dict(sorted(sector_counts.items())),
        "table_role_status_counts": dict(sorted(table_role_counts.items())),
        "inputs": {
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "routing_catalog": {"path": str(routing_catalog), "sha256": sha256_file(routing_catalog)},
            "taxonomy": {"path": str(taxonomy_path), "sha256": sha256_file(taxonomy_path)},
        },
        "outputs": {
            "row_candidates": {"path": str(output), "sha256": sha256_file(output)},
            "sector_candidates": {"path": str(sectors_output), "sha256": sha256_file(sectors_output)},
            "table_role_candidates": {"path": str(table_roles_output), "sha256": sha256_file(table_roles_output)},
        },
        "research_hypothesis": "A multidimensional taxonomy increases semantic row coverage without changing evidence provenance.",
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--routing-catalog", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_candidates(
        structured_tables=args.structured_tables.resolve(),
        routing_catalog=args.routing_catalog.resolve(),
        taxonomy_path=args.taxonomy.resolve(),
        output=args.output.resolve(),
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "labelled_rows": manifest["labelled_row_count"],
                "baseline_coverage": manifest["baseline_exact_variable_coverage"],
                "taxonomy_candidate_coverage": manifest["taxonomy_concept_candidate_coverage"],
                "status_counts": manifest["match_status_counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
