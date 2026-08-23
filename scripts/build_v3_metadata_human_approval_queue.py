#!/usr/bin/env python3
"""Build the human approval queue for V3 metadata repair proposals."""
from __future__ import annotations

import argparse
from pathlib import Path
from finance_query.v3_metadata_human_queue import build_human_approval_queue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", required=True, type=Path)
    parser.add_argument("--metadata-reviews", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(build_human_approval_queue(review_manifest_path=args.review_manifest, metadata_reviews_path=args.metadata_reviews, output_dir=args.output_dir)["manifest_path"])


if __name__ == "__main__":
    main()
