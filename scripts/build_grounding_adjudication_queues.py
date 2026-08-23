#!/usr/bin/env python3
"""Materialize blank-decision grounding adjudication queues."""
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.grounding_adjudication import build_grounding_adjudication_queues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period-packets", required=True, type=Path)
    parser.add_argument("--period-manifest", required=True, type=Path)
    parser.add_argument("--no-candidate-audit", required=True, type=Path)
    parser.add_argument("--document-metadata", required=True, type=Path)
    parser.add_argument("--routing-catalog", required=True, type=Path)
    parser.add_argument("--structured-tables", required=True, type=Path)
    parser.add_argument("--evidence-context", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = build_grounding_adjudication_queues(
        period_packets_path=args.period_packets,
        period_manifest_path=args.period_manifest,
        no_candidate_audit_path=args.no_candidate_audit,
        document_metadata_path=args.document_metadata,
        routing_catalog_path=args.routing_catalog,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        output_dir=args.output_dir,
    )
    print(result["manifest_path"])


if __name__ == "__main__":
    main()
