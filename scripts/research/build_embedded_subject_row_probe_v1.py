#!/usr/bin/env python3
"""Build a navigation-only probe for ownership-note subject rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.embedded_subject_row_probe import build_embedded_subject_row_probe


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "reclassification-dir",
        "review-items",
        "table-candidates",
        "table-candidates-manifest",
        "full-assets",
        "full-assets-manifest",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--minimum-subject-token-coverage", type=float, default=1.0)
    args = parser.parse_args()
    print(
        json.dumps(
            build_embedded_subject_row_probe(
                reclassification_dir=args.reclassification_dir,
                review_items_path=args.review_items,
                table_candidates_path=args.table_candidates,
                table_candidates_manifest_path=args.table_candidates_manifest,
                full_assets_path=args.full_assets,
                full_assets_manifest_path=args.full_assets_manifest,
                output_dir=args.output_dir,
                expected_question_count=args.expected_question_count,
                minimum_subject_token_coverage=args.minimum_subject_token_coverage,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
