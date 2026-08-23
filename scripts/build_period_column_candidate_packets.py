#!/usr/bin/env python3
"""Build research-only period-column candidate packets and blocker diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.period_column_candidates import materialize_period_column_packets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-packets", type=Path, required=True)
    parser.add_argument("--route-packets-manifest", type=Path, required=True)
    parser.add_argument("--question-routes", type=Path, required=True)
    parser.add_argument("--question-routes-manifest", type=Path, required=True)
    parser.add_argument("--taxonomy-candidates", type=Path, required=True)
    parser.add_argument("--taxonomy-candidates-manifest", type=Path, required=True)
    parser.add_argument("--table-roles", type=Path, required=True)
    parser.add_argument("--sectors", type=Path, required=True)
    parser.add_argument("--routing-catalog", type=Path, required=True)
    parser.add_argument("--routing-manifest", type=Path, required=True)
    parser.add_argument("--document-metadata", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--structure-manifest", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-candidate-audit-output", type=Path, required=True)
    args = parser.parse_args()
    manifest = materialize_period_column_packets(
        packets_path=args.route_packets.resolve(),
        packets_manifest_path=args.route_packets_manifest.resolve(),
        routes_path=args.question_routes.resolve(),
        routes_manifest_path=args.question_routes_manifest.resolve(),
        candidates_path=args.taxonomy_candidates.resolve(),
        candidates_manifest_path=args.taxonomy_candidates_manifest.resolve(),
        table_roles_path=args.table_roles.resolve(),
        sectors_path=args.sectors.resolve(),
        routing_catalog_path=args.routing_catalog.resolve(),
        routing_manifest_path=args.routing_manifest.resolve(),
        document_metadata_path=args.document_metadata.resolve(),
        structured_tables_path=args.structured_tables.resolve(),
        structure_manifest_path=args.structure_manifest.resolve(),
        evidence_context_path=args.evidence_context.resolve(),
        evidence_manifest_path=args.evidence_manifest.resolve(),
        output=args.output.resolve(),
        no_candidate_audit_output=args.no_candidate_audit_output.resolve(),
    )
    print(json.dumps({
        "question_count": manifest["question_count"],
        "packet_status_counts": manifest["packet_status_counts"],
        "period_column_candidate_count": manifest["period_column_candidate_count"],
        "output_sha256": manifest["outputs"]["period_packets"]["sha256"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
