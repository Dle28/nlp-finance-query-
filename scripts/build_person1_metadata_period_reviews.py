#!/usr/bin/env python3
"""Build hash-bound Person-1 metadata and period review sidecars."""
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.independent_adjudication_reviews import build_independent_adjudication_reviews


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-queue", required=True, type=Path)
    parser.add_argument("--period-queue", type=Path)
    parser.add_argument("--adjudication-manifest", required=True, type=Path)
    parser.add_argument("--review-plan", required=True, type=Path)
    parser.add_argument("--structured-tables", required=True, type=Path)
    parser.add_argument("--evidence-context", required=True, type=Path)
    parser.add_argument("--repository-root", default=Path.cwd(), type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--artifact-version", default="v1")
    parser.add_argument("--evidence-manifest", type=Path)
    args = parser.parse_args()
    result = build_independent_adjudication_reviews(
        metadata_queue_path=args.metadata_queue,
        period_queue_path=args.period_queue,
        adjudication_manifest_path=args.adjudication_manifest,
        review_plan_path=args.review_plan,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        repository_root=args.repository_root,
        output_dir=args.output_dir,
        artifact_version=args.artifact_version,
        evidence_manifest_path=args.evidence_manifest,
    )
    print(result["manifest_path"])


if __name__ == "__main__":
    main()
