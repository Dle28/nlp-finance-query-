#!/usr/bin/env python3
"""Reconcile two completed, hash-bound route-coverage review label files."""
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.route_coverage_review_reconciliation import reconcile_reviews


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment-manifest", type=Path, required=True)
    parser.add_argument("--reviewer-a-assignment", type=Path, required=True)
    parser.add_argument("--reviewer-b-assignment", type=Path, required=True)
    parser.add_argument("--reviewer-a-labels", type=Path, required=True)
    parser.add_argument("--reviewer-b-labels", type=Path, required=True)
    parser.add_argument(
        "--reviewer-a-review-manifest",
        dest="reviewer_a_review_manifest_path",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--reviewer-b-review-manifest",
        dest="reviewer_b_review_manifest_path",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(reconcile_reviews(**vars(args))["manifest_path"])


if __name__ == "__main__":
    main()
