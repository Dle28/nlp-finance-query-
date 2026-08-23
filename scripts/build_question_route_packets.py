#!/usr/bin/env python3
"""Build hash-bound, bounded table-navigation packets without evidence selection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.route_packets import (
    DEFAULT_OPERAND_CANDIDATE_CAP,
    materialize_route_packets,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--routes-manifest", type=Path, required=True)
    parser.add_argument("--taxonomy-candidates", type=Path, required=True)
    parser.add_argument("--taxonomy-candidates-manifest", type=Path, required=True)
    parser.add_argument("--table-roles", type=Path, required=True)
    parser.add_argument("--sectors", type=Path, required=True)
    parser.add_argument("--routing-catalog", type=Path, required=True)
    parser.add_argument("--routing-manifest", type=Path, required=True)
    parser.add_argument("--document-metadata", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--structure-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cap", type=int, default=DEFAULT_OPERAND_CANDIDATE_CAP)
    args = parser.parse_args()
    manifest = materialize_route_packets(
        routes_path=args.routes.resolve(),
        routes_manifest_path=args.routes_manifest.resolve(),
        candidates_path=args.taxonomy_candidates.resolve(),
        candidates_manifest_path=args.taxonomy_candidates_manifest.resolve(),
        table_roles_path=args.table_roles.resolve(),
        sectors_path=args.sectors.resolve(),
        routing_catalog_path=args.routing_catalog.resolve(),
        routing_manifest_path=args.routing_manifest.resolve(),
        document_metadata_path=args.document_metadata.resolve(),
        structured_tables_path=args.structured_tables.resolve(),
        structure_manifest_path=args.structure_manifest.resolve(),
        output=args.output.resolve(),
        cap=args.cap,
    )
    print(
        json.dumps(
            {
                "question_count": manifest["question_count"],
                "route_ready_question_count": manifest["route_ready_question_count"],
                "packet_status_counts": manifest["packet_status_counts"],
                "operand_candidate_recall_proxy": manifest["operand_candidate_recall_proxy"],
                "output_sha256": manifest["outputs"]["packets"]["sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
