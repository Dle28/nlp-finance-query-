#!/usr/bin/env python3
"""Build a fail-closed V1-review-to-V2 handoff audit."""
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.review_sidecar_handoff import build_review_sidecar_handoff_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", required=True, type=Path)
    parser.add_argument("--metadata-reviews", required=True, type=Path)
    parser.add_argument("--period-reviews", required=True, type=Path)
    parser.add_argument("--v2-metadata-queue", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = build_review_sidecar_handoff_audit(
        review_manifest_path=args.review_manifest,
        metadata_reviews_path=args.metadata_reviews,
        period_reviews_path=args.period_reviews,
        v2_metadata_queue_path=args.v2_metadata_queue,
        output_dir=args.output_dir,
    )
    print(result["manifest_path"])


if __name__ == "__main__":
    main()
