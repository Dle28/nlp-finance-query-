#!/usr/bin/env python3
"""Materialize only hash-bound, human-verified V3 metadata approvals."""
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.metadata_human_approval import materialize_metadata_human_approvals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", required=True, type=Path)
    parser.add_argument("--metadata-reviews", required=True, type=Path)
    parser.add_argument("--approval-queue-manifest", required=True, type=Path)
    parser.add_argument("--approval-queue", required=True, type=Path)
    parser.add_argument("--human-decisions", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        materialize_metadata_human_approvals(
            review_manifest_path=args.review_manifest,
            metadata_reviews_path=args.metadata_reviews,
            approval_queue_manifest_path=args.approval_queue_manifest,
            approval_queue_path=args.approval_queue,
            decisions_path=args.human_decisions,
            output_dir=args.output_dir,
        )["manifest_path"]
    )


if __name__ == "__main__":
    main()
