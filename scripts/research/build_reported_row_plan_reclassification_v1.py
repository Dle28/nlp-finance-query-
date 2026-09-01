#!/usr/bin/env python3
"""Build a source-gated planning revision for explicit disclosed report rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.reported_row_plan_reclassification import (
    build_reported_row_plan_reclassification,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "base-plans",
        "base-plans-manifest",
        "review-items",
        "entity-aliases",
        "base-route-overlay",
        "base-route-overlay-manifest",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    result = build_reported_row_plan_reclassification(
        base_plans_path=args.base_plans,
        base_plans_manifest_path=args.base_plans_manifest,
        review_items_path=args.review_items,
        entity_aliases_path=args.entity_aliases,
        base_route_overlay_path=args.base_route_overlay,
        base_route_overlay_manifest_path=args.base_route_overlay_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
