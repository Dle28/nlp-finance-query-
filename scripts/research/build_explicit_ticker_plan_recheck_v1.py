#!/usr/bin/env python3
"""Build a value-blind explicit-ticker planning overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.explicit_ticker_plan_recheck import build_explicit_ticker_plan_recheck


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("base-plans", "base-plans-manifest", "review-items", "entity-aliases", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    print(json.dumps(build_explicit_ticker_plan_recheck(
        base_plans_path=args.base_plans,
        base_plans_manifest_path=args.base_plans_manifest,
        review_items_path=args.review_items,
        entity_aliases_path=args.entity_aliases,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
