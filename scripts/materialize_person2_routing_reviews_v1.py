#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.person2_routing_reviews import materialize_reviews


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize independent Person 2 routing reviews.")
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--adjudication-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--routing-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--completed-at-utc", required=True)
    args = parser.parse_args()
    print(materialize_reviews(
        queue_path=args.queue, adjudication_manifest_path=args.adjudication_manifest,
        structured_tables_path=args.structured_tables, evidence_context_path=args.evidence_context,
        routing_catalog_path=args.routing_catalog, output=args.output,
        completed_at_utc=args.completed_at_utc,
    )["manifest_path"])


if __name__ == "__main__":
    main()
